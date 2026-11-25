from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from yleum_api.core.errors import ApiError
from yleum_api.models.generation_run import GenerationRun
from yleum_api.models.message import Message
from yleum_api.models.project import Project
from yleum_api.schemas.message import (
    ChatQuestionAnswer,
    ChatQuestionDeclined,
    ChatQuestionRequest,
    PromptRequest,
)
from yleum_api.services import max_owner_diagnosis
from yleum_api.services.generation.source_intent import is_max_owner_question
from yleum_api.services.generation_runs import try_prompt_project_lock
from yleum_api.services.secret_safety import contains_provider_secret

logger = logging.getLogger(__name__)
DIAGNOSIS_TIMEOUT_SECONDS = 26.0
# Diagnosis uses Core connections also needed by active generations.
_DIAGNOSIS_SLOTS = asyncio.BoundedSemaphore(2)


def _message_ids(project_id: UUID, user_id: UUID, key: str) -> tuple[UUID, UUID]:
    # The two messages themselves are the durable receipt. The client key is
    # never needed to render history or stored on either row.
    turn = uuid5(NAMESPACE_URL, f"yleum:max-owner-question:{project_id}:{user_id}:{key}")
    return uuid5(turn, "user"), uuid5(turn, "assistant")


def _key_conflict() -> ApiError:
    return ApiError("idempotency_conflict", "Этот ключ уже использован для другого запроса.", 409)


def _reject_credentials(*values: str) -> None:
    if any(contains_provider_secret(value) for value in values):
        raise ApiError(
            "integration_credentials_invalid",
            "Ключи и токены нужно добавлять через «Интеграции». Уберите ключ из сообщения.",
            400,
        )


async def guard_unaccepted_max_prompt(
    session: AsyncSession, project_id: UUID, user_id: UUID, payload: PromptRequest,
) -> None:
    """Called under the project lock after exact legacy run lookup."""
    if payload.idempotency_key is not None:
        user_message_id, _ = _message_ids(project_id, user_id, payload.idempotency_key)
        if await session.get(Message, user_message_id) is not None:
            raise _key_conflict()
    if (
        payload.reference_image_ids or payload.font_selections or payload.selected_elements
        or payload.supersedes_run_id is not None or payload.restoration_adaptation is not None
        or payload.continuation is not None or payload.max_config_version is not None
        or payload.skip_clarify or payload.design_preset_id is not None
    ):
        return
    if is_max_owner_question(payload.prompt):
        _reject_credentials(payload.prompt)
        raise ApiError(
            "project_question_required",
            "Обновите страницу и отправьте вопрос в чат проекта.",
            409,
        )


async def _check_generation_key(session: AsyncSession, project_id: UUID, key: str) -> None:
    existing = await session.scalar(select(GenerationRun.id).where(
        GenerationRun.project_id == project_id, GenerationRun.idempotency_key == key,
    ))
    if existing is not None:
        raise _key_conflict()


async def _read_answer(
    session: AsyncSession, project: Project, user_id: UUID, prompt: str,
) -> str:
    unavailable = ApiError("advice_unavailable", "Ответ пока недоступен. Повторите вопрос.", 503)
    try:
        async with asyncio.timeout(DIAGNOSIS_TIMEOUT_SECONDS):
            answer = await max_owner_diagnosis.answer_owner_question(
                session, project, user_id, prompt,
            )
    except Exception as exc:
        logger.warning("MAX owner diagnosis failed (%s)", type(exc).__name__)
        raise unavailable from None
    if not answer.strip() or "\x00" in answer or contains_provider_secret(answer):
        raise unavailable
    return answer


async def _replay_question(
    session: AsyncSession, project_id: UUID, prompt: str,
    user_message_id: UUID, assistant_message_id: UUID,
) -> ChatQuestionAnswer | None:
    user_row = await session.get(Message, user_message_id)
    if user_row is None:
        return None
    if (
        user_row.project_id != project_id or user_row.role != "user"
        or not user_row.chat_question or user_row.content != prompt
    ):
        raise _key_conflict()
    answer_row = await session.get(Message, assistant_message_id)
    if (
        answer_row is None or answer_row.project_id != project_id
        or answer_row.role != "assistant" or not answer_row.chat_question
    ):
        raise _key_conflict()
    return ChatQuestionAnswer(
        user_message_id=user_message_id, assistant_message_id=assistant_message_id,
        answer=answer_row.content,
    )


async def submit_max_owner_question(
    session: AsyncSession, project: Project, user_id: UUID, payload: ChatQuestionRequest,
    limit_new_answer: Callable[[], None],
) -> ChatQuestionAnswer | ChatQuestionDeclined:
    if project.template != "max_miniapp":
        raise ApiError("max_project_required", "Этот чат доступен для проекта MAX Mini App.", 409)
    _reject_credentials(payload.prompt, payload.idempotency_key)
    project_id = project.id
    user_message_id, assistant_message_id = _message_ids(
        project_id, user_id, payload.idempotency_key,
    )

    replay = await _replay_question(
        session, project_id, payload.prompt, user_message_id, assistant_message_id,
    )
    if replay is not None:
        return replay
    if not is_max_owner_question(payload.prompt):
        return ChatQuestionDeclined()
    limit_new_answer()
    if _DIAGNOSIS_SLOTS.locked():
        raise ApiError(
            "rate_limited", "Чат сейчас отвечает на другие вопросы. Повторите через мгновение.",
            429,
            details={"reason": "chat_question_capacity", "retryable": True, "retry_after": 2},
        )

    async with _DIAGNOSIS_SLOTS:
        # Serialize question work without holding the project admission lock
        # during diagnosis. Active build and publication work use the other lock.
        acquired = await session.scalar(
            text("SELECT pg_try_advisory_xact_lock(hashtext('max-owner-question'), hashtext(:id))"),
            {"id": str(project_id)},
        )
        if acquired is not True:
            raise ApiError(
                "project_busy", "Предыдущий вопрос ещё обрабатывается. Повторите через мгновение.",
                409,
                details={"reason": "chat_question_busy", "retryable": True, "retry_after": 2},
            )
        replay = await _replay_question(
            session, project_id, payload.prompt, user_message_id, assistant_message_id,
        )
        if replay is not None:
            return replay

        await _check_generation_key(session, project_id, payload.idempotency_key)
        asked_at = datetime.now(UTC)
        answer = await _read_answer(session, project, user_id, payload.prompt)
        await try_prompt_project_lock(session, project_id)
        # An edit with the same key may have been admitted during the read.
        await _check_generation_key(session, project_id, payload.idempotency_key)
        session.add_all([
            Message(
                id=user_message_id, project_id=project_id, role="user", content=payload.prompt,
                chat_question=True, created_at=asked_at,
            ),
            Message(
                id=assistant_message_id, project_id=project_id, role="assistant", content=answer,
                chat_question=True, tokens_out=0,
                created_at=max(datetime.now(UTC), asked_at + timedelta(microseconds=1)),
            ),
        ])
        await session.commit()
    return ChatQuestionAnswer(
        user_message_id=user_message_id, assistant_message_id=assistant_message_id, answer=answer,
    )
