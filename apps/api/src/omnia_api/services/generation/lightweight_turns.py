from __future__ import annotations

from uuid import UUID

from sqlalchemy.ext.asyncio import async_sessionmaker

from omnia_api.core.db import get_engine
from omnia_api.core.redis import publish_event
from omnia_api.models.generation_run import GenerationRun
from omnia_api.models.message import Message
from omnia_api.models.project import Project
from omnia_api.services.clarify import generate_clarify_questions
from omnia_api.services.discovery import (
    infer_niche_label,
    plan_discovery_questions,
    serve_planned_question,
)
from omnia_api.services.generation.onboarding import (
    _build_onboarding_survey,
    _maybe_result_type_question,
)


async def _run_clarify(
    project_id: UUID, assistant_message_id: UUID, prompt: str, language: str = "ru"
) -> None:
    """Pre-generation clarify turn: stream 3–4 questions into the assistant
    message, persist them, finalize. NO files, NO snapshot, NO generation — the
    user's answers (next message) drive the real build via history."""
    text = await generate_clarify_questions(prompt, language=language)
    await publish_event(
        project_id,
        "llm.chunk",
        {"message_id": str(assistant_message_id), "delta": text},
    )
    factory = async_sessionmaker(get_engine(), expire_on_commit=False)
    async with factory() as session:
        msg = await session.get(Message, assistant_message_id)
        if msg is not None:
            msg.content = text
            msg.tokens_in = 0
            msg.tokens_out = 0
            await session.commit()
    await publish_event(
        project_id,
        "llm.done",
        {"message_id": str(assistant_message_id), "snapshot_id": None},
    )


async def _run_text_turn(project_id: UUID, assistant_message_id: UUID, text: str) -> None:
    """Stream a pre-computed assistant message (no LLM, no build) and finalize.

    Used by the progressive-discovery ASK turn: the next question was already
    decided in ``post_prompt``, so we just publish it as one chunk + persist +
    done. Mirrors ``_run_clarify`` minus the gateway call."""
    await publish_event(
        project_id,
        "llm.chunk",
        {"message_id": str(assistant_message_id), "delta": text, "seq": 1},
    )
    factory = async_sessionmaker(get_engine(), expire_on_commit=False)
    async with factory() as session:
        msg = await session.get(Message, assistant_message_id)
        if msg is not None:
            msg.content = text
            msg.tokens_in = 0
            msg.tokens_out = 0
            await session.commit()
    await publish_event(
        project_id,
        "llm.done",
        {"message_id": str(assistant_message_id), "snapshot_id": None},
    )


_INSTALL_CARD_TEXT = (
    "Готово — собрал установщик. Нажми кнопку ниже: скачается архив проекта. "
    "Если это программа (Python/Node), внутри лежит **run.bat** — распакуй и сделай "
    "двойной клик по нему (на Mac — run.command): он сам поставит зависимости и "
    "запустит. Если это сайт — открой его кнопкой «Открыть» сверху.\n\n"
    "<install-bundle></install-bundle>"
)


_RUN_ASK_TEXT = (
    "Похоже, ты хочешь запустить проект у себя на компьютере. Собрать установщик "
    "(архив с run.bat — распаковал, двойной клик, и оно само поставится и "
    "запустится)? Или продолжим дорабатывать проект?"
)


_RUN_DECLINE_REPLY = "Понял, установщик не собираю. Напиши, что доработать или добавить — сделаю."


def _failed_build_explanation(run: GenerationRun) -> str:
    """User-safe factual explanation; never invokes the code-generation agent."""

    if run.error == "API process restarted before generation completed":
        reason = "процесс API перезапустился до завершения агента"
    elif run.error == "build finished without a committed snapshot":
        reason = (
            "агент завершил цикл, но система не получила подтверждённый рабочий снимок приложения"
        )
    else:
        reason = "запуск завершился ошибкой до создания проверенного снимка"
    return (
        f"Предыдущая попытка завершилась без рабочей версии: {reason}. "
        "Новую сборку не запускаю. Чтобы попробовать исправление, "
        "напишите: «почини и продолжай»."
    )


_ASYNC_ONBOARDING_PLACEHOLDER = "Секунду — подбираю пару вопросов под твою задачу…"


async def _run_async_onboarding(
    project_id: UUID, assistant_message_id: UUID, prompt: str, language: str
) -> None:
    """Plan the onboarding question batch off-band and deliver it over WS.

    Never raises (R-10): a gateway miss degrades to the deterministic batch so the
    popup still opens, and the message is always finalized (llm.done) so the UI
    never hangs on the spinner."""
    # Placeholder so the assistant bubble isn't blank for the ~minute Opus thinks.
    await publish_event(
        project_id,
        "llm.chunk",
        {
            "message_id": str(assistant_message_id),
            "delta": _ASYNC_ONBOARDING_PLACEHOLDER,
            "seq": 1,
        },
    )

    # The slow part — one Opus call. Fail-soft to the deterministic batch so the
    # popup opens regardless (mirrors plan_discovery_questions' own fallback).
    try:
        questions = await plan_discovery_questions(prompt, language=language)
    except Exception:  # a gateway hiccup must never hang onboarding (R-10)
        questions = []
    try:
        _type_q = await _maybe_result_type_question(prompt, language)
        if _type_q is not None:
            questions = [_type_q, *questions]
    except Exception:  # the type-clarify question is best-effort (R-10)
        pass
    if not questions:
        from omnia_api.services.discovery import _plan_fallback

        questions = _plan_fallback()
    plan = [q.to_dict() for q in questions]
    survey = _build_onboarding_survey(plan)

    # Stash the plan on the project + serve question 0, persisting the question as
    # the assistant message content (single source of truth on reload).
    ask = serve_planned_question(plan, 0)
    question_text = ask.message if ask is not None else _ASYNC_ONBOARDING_PLACEHOLDER
    factory = async_sessionmaker(get_engine(), expire_on_commit=False)
    async with factory() as session:
        project = await session.get(Project, project_id)
        if project is not None:
            project.discovery_plan = plan
        msg = await session.get(Message, assistant_message_id)
        if msg is not None:
            msg.content = question_text
            msg.tokens_in = 0
            msg.tokens_out = 0
        await session.commit()

    # Replace the placeholder with the real first question (stream.sync sets the
    # whole content), open the survey popup, then finalize the turn.
    await publish_event(
        project_id,
        "stream.sync",
        {
            "message_id": str(assistant_message_id),
            "content": question_text,
            "seq": 1,
        },
    )
    if survey:
        await publish_event(
            project_id,
            "onboarding.survey",
            {
                "message_id": str(assistant_message_id),
                "survey": [question.model_dump(mode="json") for question in survey],
                "question_index": 1,
                "question_total": len(plan),
                "niche": infer_niche_label(prompt) or None,
            },
        )
    await publish_event(
        project_id,
        "llm.done",
        {"message_id": str(assistant_message_id), "snapshot_id": None},
    )
