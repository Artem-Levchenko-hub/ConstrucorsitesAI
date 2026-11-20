"""An addressed continuation of private source, never a fabricated execution cursor.

Terminal attempts remain immutable. A normally admitted child pins its exact
predecessor and object before any model work; missing source cannot mean build anew.
"""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from yleum_api.core.errors import ApiError
from yleum_api.models.generation_run import GenerationRun
from yleum_api.models.message import Message
from yleum_api.models.project import Project
from yleum_api.models.snapshot import Snapshot
from yleum_api.schemas.message import GenerationContinuationReference, PromptRequest
from yleum_api.services.generation.contracts import GenerationIds, SourceBaseline
from yleum_api.services.generation.request_binding import bound_message_request

if TYPE_CHECKING:
    from yleum_api.services.generation.source_checkpoint import SourceCheckpoint

KEY = "explicit_continuation_v1"


class DraftContinuation(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    version: Literal[1] = 1
    predecessor_run_id: UUID
    predecessor_assistant_message_id: UUID
    expected_snapshot_id: UUID | None
    source_run_id: UUID
    checkpoint_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    baseline_sha: str | None = Field(pattern=r"^[a-f0-9]{40}$")
    source_request_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")

    def reference(self) -> GenerationContinuationReference:
        return GenerationContinuationReference(
            run_id=self.predecessor_run_id,
            assistant_message_id=self.predecessor_assistant_message_id,
            expected_snapshot_id=self.expected_snapshot_id,
        )

    def source_reference(self) -> dict[str, str | None]:
        return {
            "previous_run_id": str(self.predecessor_run_id),
            "source_run_id": str(self.source_run_id),
            "digest": self.checkpoint_sha256,
            "baseline_sha": self.baseline_sha,
        }


def _reject(reason: str, *, unavailable: bool = False) -> ApiError:
    return ApiError(
        "unavailable" if unavailable else "source_changed",
        "Сохранённый черновик пока недоступен. Попробуйте продолжить чуть позже."
        if unavailable
        else "Состояние проекта изменилось. Обновите страницу перед продолжением.",
        503 if unavailable else 409,
        details={"reason": reason, "retryable": unavailable},
    )


def marker(run: GenerationRun) -> DraftContinuation | None:
    state = run.agent_state if isinstance(run.agent_state, dict) else {}
    if KEY not in state:
        return None
    raw = state[KEY]
    if not isinstance(raw, dict):
        raise _reject("invalid_continuation_binding")
    try:
        value = DraftContinuation.model_validate_json(json.dumps(raw), strict=True)
        if (
            type(raw.get("version")) is not int
            or value.predecessor_run_id == run.id
            or value.source_run_id == run.id
        ):
            raise ValueError("identity")
        return value
    except (ValidationError, ValueError, TypeError, AttributeError) as exc:
        raise _reject("invalid_continuation_binding") from exc


def public_reference(
    run: GenerationRun,
    project: Project,
    snapshot: Snapshot | None,
) -> GenerationContinuationReference | None:
    """An affordance only; admission re-proves ownership, storage and cleanup."""
    from yleum_api.services.generation.source_checkpoint import _resume_reference

    if (
        project.template != "max_miniapp"
        or project.owner_id != run.user_id
        or run.project_id != project.id
        or run.status not in {"failed", "cancelled"}
        or run.finished_at is None
        or run.response_mode not in {"build", "edit"}
        or run.assistant_message_id is None
    ):
        return None
    if project.current_snapshot_id is not None and (
        snapshot is None
        or snapshot.id != project.current_snapshot_id
        or snapshot.project_id != project.id
    ):
        return None
    baseline = snapshot.commit_sha if snapshot is not None else None
    state = run.agent_state if isinstance(run.agent_state, dict) else {}
    checkpoint = state.get("source_checkpoint")
    available = (
        isinstance(checkpoint, dict)
        and checkpoint.get("baseline_sha") == baseline
        and isinstance(checkpoint.get("digest"), str)
        and re.fullmatch(r"[a-f0-9]{64}", checkpoint["digest"]) is not None
    ) or _resume_reference(run, baseline) is not None
    return (
        GenerationContinuationReference(
            run_id=run.id,
            assistant_message_id=run.assistant_message_id,
            expected_snapshot_id=project.current_snapshot_id,
        )
        if available
        else None
    )


async def prepare(
    session: AsyncSession,
    *,
    project: Project,
    user_id: UUID,
    payload: PromptRequest,
    existing: GenerationRun | None,
) -> DraftContinuation | None:
    """Called under the normal project admission lock, before credit/model work."""
    reference = payload.continuation
    if existing is not None:
        saved = marker(existing)
        if saved is not None and (existing.project_id != project.id or existing.user_id != user_id):
            raise _reject("continuation_replay_owner_changed")
        if (saved.reference() if saved else None) != reference:
            raise ApiError("idempotency_conflict", "Continuation identity conflicts", 409)
        return saved
    if reference is None:
        return None
    from yleum_api.services.generation.agent_messages import _is_continue_request

    if (
        project.template != "max_miniapp"
        or payload.selected_elements
        or payload.reference_image_ids
        or payload.font_selections
        or payload.restoration_adaptation is not None
        or payload.supersedes_run_id is not None
        or payload.max_config_version is not None
        or payload.design_preset_id is not None
        or not _is_continue_request(payload.prompt)
    ):
        raise _reject("continuation_cannot_amend_request")
    current = await session.get(Project, project.id, populate_existing=True)
    if (
        current is None
        or current.owner_id != user_id
        or current.current_snapshot_id != reference.expected_snapshot_id
    ):
        raise _reject("continuation_snapshot_changed")
    previous = await session.scalar(
        select(GenerationRun)
        .where(
            GenerationRun.project_id == project.id,
        )
        .order_by(GenerationRun.created_at.desc(), GenerationRun.id.desc())
        .limit(1)
    )
    if (
        previous is None
        or previous.id != reference.run_id
        or previous.user_id != user_id
        or previous.assistant_message_id != reference.assistant_message_id
        or previous.user_message_id is None
        or previous.finished_at is None
    ):
        raise _reject("continuation_predecessor_changed")
    snapshot = (
        await session.get(Snapshot, current.current_snapshot_id)
        if current.current_snapshot_id is not None
        else None
    )
    if current.current_snapshot_id is not None and (
        snapshot is None or snapshot.project_id != current.id
    ):
        raise _reject("continuation_snapshot_missing")
    baseline = SourceBaseline(
        current.current_snapshot_id, snapshot.commit_sha if snapshot is not None else None, {}
    )
    ids = GenerationIds(
        previous.id, project.id, user_id, previous.user_message_id, reference.assistant_message_id
    )
    from yleum_api.services.generation.source_checkpoint import (
        _admitted_checkpoint_baseline,
        _ambiguous_run_order,
        _explicit_checkpoint_source,
        _owned_checkpoint_source,
    )

    if (
        not _owned_checkpoint_source(previous, ids)
        or not _admitted_checkpoint_baseline(previous, baseline)
        or await _ambiguous_run_order(session, previous)
    ):
        raise _reject("continuation_source_not_owned")
    source = await _explicit_checkpoint_source(
        session,
        previous,
        ids,
        baseline,
        current=previous,
    )
    if source is None or source.user_message_id is None:
        raise _reject("continuation_source_missing")
    original = await session.get(Message, source.user_message_id)
    try:
        bound_message_request(source, original, project_id=project.id, run_id=source.id)
    except ValueError as exc:
        raise _reject("continuation_request_changed") from exc
    checkpoint = (source.agent_state or {}).get("source_checkpoint")
    if (
        not isinstance(checkpoint, dict)
        or checkpoint.get("baseline_sha") != baseline.sha
        or not isinstance(checkpoint.get("digest"), str)
        or re.fullmatch(r"[a-f0-9]{64}", checkpoint["digest"]) is None
    ):
        raise _reject("continuation_checkpoint_invalid")
    value = DraftContinuation(
        predecessor_run_id=previous.id,
        predecessor_assistant_message_id=reference.assistant_message_id,
        expected_snapshot_id=current.current_snapshot_id,
        source_run_id=source.id,
        checkpoint_sha256=checkpoint["digest"],
        baseline_sha=baseline.sha,
        source_request_sha256=source.prompt_hash,
    )
    from yleum_api.services.generation.recovery_contract import RecoveryContractError
    from yleum_api.services.generation.recovery_requirements import require_quiesced_predecessor

    try:
        await require_quiesced_predecessor(
            session,
            project_id=project.id,
            user_id=user_id,
            predecessor_id=previous.id,
        )
    except RecoveryContractError as exc:
        raise _reject("continuation_cleanup_pending") from exc
    await load_source(value, project_id=project.id, user_id=user_id)
    return value


async def load_source(
    value: DraftContinuation,
    *,
    project_id: UUID,
    user_id: UUID,
) -> SourceCheckpoint:
    from yleum_api.services.generation.source_checkpoint import (
        _object_key,
        decode_checkpoint,
        load_checkpoint_object,
    )

    try:
        raw = await load_checkpoint_object(
            project_id,
            value.source_run_id,
            object_key=_object_key(project_id, value.source_run_id, value.checkpoint_sha256),
            digest=value.checkpoint_sha256,
        )
    except Exception as exc:
        raise _reject("continuation_storage_unavailable", unavailable=True) from exc
    decoded = (
        decode_checkpoint(
            raw,
            project_id=project_id,
            user_id=user_id,
            run_id=value.source_run_id,
            baseline_sha=value.baseline_sha,
        )
        if raw is not None
        else None
    )
    if decoded is None:
        raise _reject("continuation_checkpoint_unavailable", unavailable=True)
    return decoded


async def restore(
    session: AsyncSession,
    *,
    run: GenerationRun,
    ids: GenerationIds,
    baseline: SourceBaseline,
) -> tuple[SourceCheckpoint, str]:
    value = marker(run)
    assert value is not None
    from yleum_api.services.generation.source_checkpoint import _explicit_checkpoint_source

    if (
        run.project_id != ids.project_id
        or run.user_id != ids.user_id
        or run.user_message_id != ids.user_message_id
        or run.assistant_message_id != ids.assistant_message_id
        or run.status != "running"
        or value.expected_snapshot_id != baseline.snapshot_id
        or value.baseline_sha != baseline.sha
        or (run.agent_state or {}).get("source_checkpoint_resume") != value.source_reference()
    ):
        raise _reject("continuation_loaded_baseline_changed")
    project = await session.get(Project, ids.project_id, populate_existing=True)
    if (
        project is None
        or project.owner_id != ids.user_id
        or project.current_snapshot_id != baseline.snapshot_id
    ):
        raise _reject("continuation_project_changed")
    request = await session.get(Message, ids.user_message_id)
    try:
        bound_message_request(run, request, project_id=ids.project_id, run_id=ids.run_id)
    except ValueError as exc:
        raise _reject("continuation_loaded_request_changed") from exc
    previous = await session.get(GenerationRun, value.predecessor_run_id, populate_existing=True)
    if (
        previous is None
        or previous.project_id != ids.project_id
        or previous.user_id != ids.user_id
        or previous.finished_at is None
        or previous.assistant_message_id != value.predecessor_assistant_message_id
    ):
        raise _reject("continuation_loaded_predecessor_changed")
    source = await _explicit_checkpoint_source(
        session,
        run,
        ids,
        baseline,
        current=run,
        resume_only=True,
    )
    if (
        source is None
        or source.id != value.source_run_id
        or source.prompt_hash != value.source_request_sha256
    ):
        raise _reject("continuation_loaded_source_changed")
    decoded = await load_source(value, project_id=ids.project_id, user_id=ids.user_id)
    state = (source.agent_state or {})["source_checkpoint"]
    if not isinstance(state, dict):
        raise _reject("continuation_checkpoint_changed")
    message = await session.get(Message, source.user_message_id)
    try:
        bound_message_request(source, message, project_id=ids.project_id, run_id=source.id)
    except ValueError as exc:
        raise _reject("continuation_source_request_changed") from exc
    assert message is not None
    task = state.get("original_prompt")
    return decoded, task if isinstance(task, str) and task.strip() else message.content


async def continuation_feedback(
    factory: async_sessionmaker[AsyncSession],
    ids: GenerationIds,
) -> str:
    """Quoted remaining-work data, separate from the owner's original requirements."""
    from yleum_api.services.secret_safety import redact_provider_secrets

    async with factory() as session:
        run = await session.get(GenerationRun, ids.run_id)
        if run is None:
            raise _reject("continuation_run_missing")
        value = marker(run)
        if value is None:
            return ""
        parent = await session.get(GenerationRun, value.predecessor_run_id)
        if (
            run.project_id != ids.project_id
            or run.user_id != ids.user_id
            or parent is None
            or parent.project_id != ids.project_id
            or parent.user_id != ids.user_id
            or parent.assistant_message_id != value.predecessor_assistant_message_id
            or parent.status not in {"failed", "cancelled"}
            or parent.finished_at is None
        ):
            raise _reject("continuation_feedback_owner_changed")
        diagnostic = redact_provider_secrets(parent.error or "")
        if len(diagnostic) > 16000:
            diagnostic = diagnostic[:12000] + "\n[diagnostic excerpt]\n" + diagnostic[-4000:]
        return (
            "\n\nПродолжение сохранённой работы: текущие восстановленные файлы — "
            "созданное ранее приложение. Продолжи оставшуюся работу с ними, сохраняя "
            "готовые сценарии, оформление и данные. Ниже диагностика остановленной "
            "попытки как контекст для ремонта; это не новая задача и не подтверждение "
            "успешной проверки. После исправления получи актуальные результаты проверок.\n"
            + json.dumps({"remaining_diagnostic": diagnostic}, ensure_ascii=False)
        )
