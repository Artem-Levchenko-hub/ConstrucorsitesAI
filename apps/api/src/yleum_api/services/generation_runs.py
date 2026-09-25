from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal, cast
from uuid import UUID

from fastapi import status
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from yleum_api.core.errors import ApiError
from yleum_api.models.generation_run import GenerationRun
from yleum_api.models.message import Message
from yleum_api.models.project import Project
from yleum_api.models.project_cell import ProjectCellOperation, ProjectCellWorkspace
from yleum_api.models.restoration import Restoration

ACTIVE_GENERATION_STATUSES = (
    "pending",
    "queued_for_capacity",
    "running",
    "cancel_requested",
)
INTERRUPTED_GENERATION_STATUSES = ("pending", "running", "cancel_requested")


@dataclass(frozen=True, slots=True)
class _AdaptationOwnerStatusTarget:
    run_id: UUID
    workspace_id: UUID
    operation_id: UUID
    project_id: UUID
    owner_id: UUID


def has_sealed_adaptation_proof(run: GenerationRun) -> bool:
    """True while a durable proof or activation intent of an adaptation is unsettled."""
    root = run.agent_state if isinstance(run.agent_state, dict) else {}
    state = root.get("max_finalization")
    proof = state.get("restoration_adaptation_proof") if isinstance(state, dict) else None
    attempt = (
        state.get("restoration_adaptation_proof_attempt")
        if isinstance(state, dict)
        else None
    )
    activation = root.get("restoration_adaptation_activation")
    activation_terminal = isinstance(activation, dict) and activation.get("state") in {
        "completed",
        "cancelled",
    }
    return (
        (
            (isinstance(proof, dict) and proof.get("state") == "proof_ready")
            or (isinstance(attempt, dict) and attempt.get("status") == "issued")
        )
        and not activation_terminal
    )


async def _adaptation_owner_status_target(
    session: AsyncSession,
    run: GenerationRun,
) -> _AdaptationOwnerStatusTarget | None:
    state = run.agent_state if isinstance(run.agent_state, dict) else {}
    raw = state.get("restoration_adaptation")
    if not isinstance(raw, dict):
        return None
    try:
        operation_id = UUID(str(raw["operation_id"]))
        if raw.get("adaptation_run_id") != str(run.id):
            return None
    except (KeyError, TypeError, ValueError, AttributeError):
        return None
    workspace_id = await session.scalar(
        select(ProjectCellWorkspace.id).where(ProjectCellWorkspace.project_id == run.project_id)
    )
    if workspace_id is None:
        return None
    return _AdaptationOwnerStatusTarget(
        run_id=run.id,
        workspace_id=workspace_id,
        operation_id=operation_id,
        project_id=run.project_id,
        owner_id=run.user_id,
    )


async def _notify_terminal_adaptation_target(
    target: _AdaptationOwnerStatusTarget,
) -> bool:
    from yleum_api.services.orchestrator_client import (
        project_cell_update_restoration_adaptation_owner_status,
    )

    for _attempt in range(3):
        try:
            await project_cell_update_restoration_adaptation_owner_status(
                workspace_id=target.workspace_id,
                operation_id=target.operation_id,
                project_id=target.project_id,
                owner_id=target.owner_id,
                generation_run_id=target.run_id,
                state="terminal",
            )
        except Exception:
            continue
        return True
    logging.getLogger(__name__).error(
        "restoration adaptation terminal status notification failed",
        extra={"generation_run_id": str(target.run_id)},
    )
    return False


async def _retry_terminal_adaptation_notifications(
    session: AsyncSession,
    *,
    limit: int = 100,
) -> int:
    runs = list(
        (
            await session.execute(
                select(GenerationRun)
                .where(
                    GenerationRun.status.in_(("completed", "failed", "cancelled")),
                    GenerationRun.agent_state[
                        "restoration_adaptation_owner_status"
                    ].as_string().in_(("terminal_pending", "activation_cancel_pending")),
                )
                .order_by(GenerationRun.finished_at, GenerationRun.id)
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )
    targets = {
        run.id: (target, (run.agent_state or {}).get("restoration_adaptation_owner_status"))
        for run in runs
        if (target := await _adaptation_owner_status_target(session, run)) is not None
    }
    # Release the read transaction before any orchestrator HTTP call. Delivery
    # is at-least-once; a crash before the CAS below leaves the durable marker.
    await session.commit()
    notified = {
        run_id
        for run_id, (target, marker) in targets.items()
        if marker == "activation_cancel_pending"
        or await _notify_terminal_adaptation_target(target)
    }
    delivered = 0
    for run_id in notified:
        target, expected_marker = targets[run_id]
        await session.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:project_id))"),
            {"project_id": str(target.project_id)},
        )
        await session.execute(
            select(Project.id).where(Project.id == target.project_id).with_for_update()
        )
        operation = await session.get(Restoration, target.operation_id, with_for_update=True)
        run = await session.get(GenerationRun, run_id, with_for_update=True)
        if run is None:
            await session.commit()
            continue
        state = run.agent_state if isinstance(run.agent_state, dict) else {}
        if (
            run.status not in {"completed", "failed", "cancelled"}
            or state.get("restoration_adaptation_owner_status") != expected_marker
        ):
            await session.commit()
            continue
        if expected_marker == "activation_cancel_pending":
            if (
                operation is not None
                and operation.adaptation_run_id == run.id
                and operation.state != "completed"
                and not operation.activation_effects_admitted
            ):
                operation.activation_cancel_requested_at = (
                    operation.activation_cancel_requested_at or datetime.now(UTC)
                )
                operation.state = (
                    "reconciling" if isinstance(operation.activation_request, dict) else "cancelled"
                )
                operation.phase = (
                    "activation_cancel"
                    if isinstance(operation.activation_request, dict)
                    else "activation_cancelled"
                )
                operation.error = None
                operation.revision += 1
                operation.updated_at = datetime.now(UTC)
            run.agent_state = {
                **state,
                "restoration_adaptation_owner_status": (
                    "sealed_proof_retained"
                    if operation is not None
                    and isinstance(operation.activation_request, dict)
                    else "terminal_pending"
                ),
            }
            delivered += 1
            await session.commit()
            continue
        run.agent_state = {
            **state,
            "restoration_adaptation_owner_status": "terminal_notified",
        }
        if operation is not None and operation.adaptation_run_id == run.id:
            if operation.state == "adapting":
                operation.state = "cancelled" if run.status == "cancelled" else "failed"
                operation.phase = "generation"
                fallback = (
                    "adaptation activation was not completed"
                    if run.status == "completed"
                    else f"adaptation generation {run.status}"
                )
                operation.error = (run.error or fallback)[:2000]
                operation.revision += 1
                operation.updated_at = datetime.now(UTC)
            if operation.activation_notification_state == "pending":
                operation.activation_notification_state = "delivered"
        delivered += 1
        await session.commit()
    return delivered


async def retry_terminal_adaptation_notifications(
    session: AsyncSession | None = None,
    *,
    limit: int = 100,
) -> int:
    """Deliver durable adaptation terminal markers without holding SQL locks."""
    if session is not None:
        return await _retry_terminal_adaptation_notifications(session, limit=limit)

    from yleum_api.core.db import get_engine

    factory = async_sessionmaker(get_engine(), expire_on_commit=False)
    async with factory() as own_session:
        return await _retry_terminal_adaptation_notifications(own_session, limit=limit)


async def terminalize_generation_run_locked(
    session: AsyncSession,
    run: GenerationRun,
    *,
    status: Literal["completed", "failed", "cancelled"],
    error: str | None = None,
    finished_at: datetime | None = None,
) -> None:
    """Write a terminal run and its bound adaptation in the same transaction."""
    run.status = status
    run.finished_at = finished_at or datetime.now(UTC)
    if error is not None:
        run.error = error[:2000]
    state = run.agent_state if isinstance(run.agent_state, dict) else {}
    raw_adaptation = state.get("restoration_adaptation")
    if (
        isinstance(raw_adaptation, dict)
        and raw_adaptation.get("adaptation_run_id") == str(run.id)
    ):
        sealed = has_sealed_adaptation_proof(run)
        run.agent_state = {
            **state,
            **(
                {"restoration_adaptation_cancel_requested": True}
                if status == "cancelled" and sealed
                else {}
            ),
            "restoration_adaptation_owner_status": (
                "activation_cancel_pending"
                if status == "cancelled" and sealed
                else "sealed_proof_retained" if sealed else "terminal_pending"
            ),
        }


async def record_generation_product_failure(
    session: AsyncSession, run_id: UUID, error: str,
) -> None:
    """Record failure before finalising the message, without releasing execution ownership.

    The caller commits before tokens_out/llm.done. Lifecycle completion still waits
    for the executor cleanup; both completion paths consume this durable outcome.
    """
    run = await session.scalar(
        select(GenerationRun).where(GenerationRun.id == run_id)
        .execution_options(populate_existing=True).with_for_update()
    )
    if run is None or run.status in {"completed", "failed", "cancelled"}:
        return
    run.agent_state = {
        **(run.agent_state or {}),
        "product_outcome": {"status": "failed", "error": error[:2000]},
    }


def _generation_completion_error(run: GenerationRun, message: Message | None) -> str | None:
    outcome = (run.agent_state or {}).get("product_outcome")
    if isinstance(outcome, dict) and outcome.get("status") == "failed":
        error = outcome.get("error")
        return (
            error[:2000] if isinstance(error, str) and error
            else "generation did not complete successfully"
        )
    if run.response_mode == "build" and (message is None or message.snapshot_id is None):
        return "build finished without a committed snapshot"
    return None


@dataclass(frozen=True, slots=True)
class GenerationDispatch:
    schema_version: int
    project_id: UUID
    user_id: UUID
    user_message_id: UUID
    assistant_message_id: UUID
    current_snapshot_id: UUID | None
    prompt_text: str
    model_id: str
    force_model: str | None
    is_free: bool
    orchestrate: bool
    selected_elements: list[dict[str, object]] | None

    def to_json(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "project_id": str(self.project_id),
            "user_id": str(self.user_id),
            "user_message_id": str(self.user_message_id),
            "assistant_message_id": str(self.assistant_message_id),
            "current_snapshot_id": (
                str(self.current_snapshot_id) if self.current_snapshot_id is not None else None
            ),
            "prompt_text": self.prompt_text,
            "model_id": self.model_id,
            "force_model": self.force_model,
            "is_free": self.is_free,
            "orchestrate": self.orchestrate,
            "selected_elements": self.selected_elements,
        }


def load_generation_dispatch(run: GenerationRun) -> GenerationDispatch:
    state = run.agent_state
    if type(state) is not dict or type(state.get("dispatch")) is not dict:
        raise ValueError("generation dispatch is missing")
    raw = state["dispatch"]
    assert type(raw) is dict
    expected = {
        "schema_version",
        "project_id",
        "user_id",
        "user_message_id",
        "assistant_message_id",
        "current_snapshot_id",
        "prompt_text",
        "model_id",
        "force_model",
        "is_free",
        "orchestrate",
        "selected_elements",
    }
    # Runs dispatched before business profiles were retired still carry the
    # `free_business_id` key; it is accepted and ignored.
    if set(raw) - {"free_business_id"} != expected:
        raise ValueError("generation dispatch must contain exact keys")
    if (
        type(raw.get("schema_version")) is not int
        or raw["schema_version"] != 1
        or any(
            type(raw.get(key)) is not str
            for key in (
                "project_id",
                "user_id",
                "user_message_id",
                "assistant_message_id",
                "prompt_text",
                "model_id",
            )
        )
        or (
            raw.get("current_snapshot_id") is not None
            and type(raw["current_snapshot_id"]) is not str
        )
        or (raw.get("force_model") is not None and type(raw["force_model"]) is not str)
        or type(raw.get("is_free")) is not bool
        or type(raw.get("orchestrate")) is not bool
        or (raw.get("selected_elements") is not None and type(raw["selected_elements"]) is not list)
    ):
        raise ValueError("generation dispatch field types are invalid")
    prompt_text = str(raw["prompt_text"])
    model_id = str(raw["model_id"])
    if not 1 <= len(prompt_text) <= 30_000 or not model_id:
        raise ValueError("generation dispatch text fields are invalid")
    selected = raw.get("selected_elements")
    if selected is not None:
        if any(type(item) is not dict for item in selected):
            raise ValueError("generation dispatch selected elements are invalid")
        try:
            json.dumps(selected, allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise ValueError("generation dispatch selected elements are invalid") from exc
    try:
        dispatch = GenerationDispatch(
            schema_version=1,
            project_id=UUID(str(raw["project_id"])),
            user_id=UUID(str(raw["user_id"])),
            user_message_id=UUID(str(raw["user_message_id"])),
            assistant_message_id=UUID(str(raw["assistant_message_id"])),
            current_snapshot_id=(
                UUID(str(raw["current_snapshot_id"]))
                if raw.get("current_snapshot_id") is not None
                else None
            ),
            prompt_text=prompt_text,
            model_id=model_id,
            force_model=(str(raw["force_model"]) if raw.get("force_model") is not None else None),
            is_free=bool(raw["is_free"]),
            orchestrate=bool(raw["orchestrate"]),
            selected_elements=selected,
        )
    except ValueError as exc:
        raise ValueError("generation dispatch UUID is invalid") from exc
    if dispatch.project_id != run.project_id or dispatch.user_id != run.user_id:
        raise ValueError("generation dispatch ownership mismatch")
    return dispatch


def store_generation_dispatch(run: GenerationRun, dispatch: GenerationDispatch) -> None:
    if dispatch.project_id != run.project_id or dispatch.user_id != run.user_id:
        raise ValueError("generation dispatch ownership mismatch")
    state = dict(run.agent_state or {})
    state["dispatch"] = dispatch.to_json()
    run.agent_state = state


def write_capacity_dispatch_claim(
    run: GenerationRun,
    *,
    token: UUID,
    lease_seconds: int,
    now: datetime | None = None,
) -> None:
    """Persist a queue-only dispatch lease without discarding other agent state."""

    current = now or datetime.now(UTC)
    state = dict(run.agent_state) if isinstance(run.agent_state, dict) else {}
    state["capacity_dispatch_claim"] = {
        "token": str(token),
        "expires_at": (current + timedelta(seconds=lease_seconds)).isoformat(),
    }
    run.agent_state = state


def capacity_dispatch_claim_token(run: GenerationRun) -> UUID | None:
    state = run.agent_state if isinstance(run.agent_state, dict) else {}
    raw = state.get("capacity_dispatch_claim")
    if not isinstance(raw, dict) or set(raw) != {"token", "expires_at"}:
        return None
    try:
        return UUID(str(raw["token"]))
    except (TypeError, ValueError):
        return None


def has_capacity_dispatch_claim(run: GenerationRun) -> bool:
    state = run.agent_state if isinstance(run.agent_state, dict) else {}
    return "capacity_dispatch_claim" in state


def capacity_admitted_dispatch_token(run: GenerationRun) -> UUID | None:
    state = run.agent_state if isinstance(run.agent_state, dict) else {}
    raw = state.get("capacity_admitted_dispatch_token")
    if raw is None:
        return None
    try:
        return UUID(str(raw))
    except (TypeError, ValueError):
        return None


def has_capacity_admitted_dispatch_token(run: GenerationRun) -> bool:
    state = run.agent_state if isinstance(run.agent_state, dict) else {}
    return "capacity_admitted_dispatch_token" in state


def mark_capacity_dispatch_admitted(run: GenerationRun, *, token: UUID) -> None:
    state = dict(run.agent_state) if isinstance(run.agent_state, dict) else {}
    state.pop("capacity_dispatch_claim", None)
    state["capacity_admitted_dispatch_token"] = str(token)
    run.agent_state = state


async def promote_generation_after_admission(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    run_id: UUID,
    dispatch_token: UUID | None,
) -> Literal["admitted", "cancelled", "lost", "missing"]:
    """CAS the exact pending/queued owner to running without reviving terminals."""

    async with session_factory() as session:
        run = await session.scalar(
            select(GenerationRun)
            .where(GenerationRun.id == run_id)
            .with_for_update()
        )
        if run is None:
            return "missing"
        if run.status in {"cancel_requested", "cancelled"}:
            return "cancelled"
        if run.status == "queued_for_capacity":
            if (
                dispatch_token is None
                or capacity_dispatch_claim_token(run) != dispatch_token
            ):
                return "lost"
            mark_capacity_dispatch_admitted(run, token=dispatch_token)
        elif run.status == "pending":
            if (
                has_capacity_dispatch_claim(run)
                or has_capacity_admitted_dispatch_token(run)
            ):
                return "lost"
            if dispatch_token is not None:
                mark_capacity_dispatch_admitted(run, token=dispatch_token)
        elif run.status == "running":
            admitted_token = capacity_admitted_dispatch_token(run)
            if dispatch_token is not None:
                return "admitted" if admitted_token == dispatch_token else "lost"
            return (
                "lost" if has_capacity_admitted_dispatch_token(run) else "admitted"
            )
        else:
            return "lost"
        run.status = "running"
        run.started_at = run.started_at or datetime.now(UTC)
        await session.commit()
        return "admitted"


async def apply_cancelled_generation_locked(
    session: AsyncSession,
    run: GenerationRun,
) -> None:
    """Terminalize a locked run, assistant row and waiting operations atomically."""

    msg = None
    if run.assistant_message_id is not None:
        msg = await session.scalar(
            select(Message)
            .where(
                Message.id == run.assistant_message_id,
                Message.project_id == run.project_id,
            )
            .with_for_update()
        )
    operations = list(
        (
            await session.execute(
                select(ProjectCellOperation)
                .where(
                    ProjectCellOperation.generation_run_id == run.id,
                    ProjectCellOperation.status.in_(("pending", "waiting_capacity")),
                )
                .with_for_update()
            )
        )
        .scalars()
        .all()
    )
    now = datetime.now(UTC)
    if msg is not None and msg.tokens_out is None:
        marker = "[Отменено пользователем]"
        if marker not in (msg.content or ""):
            msg.content = f"{msg.content.rstrip()}\n\n{marker}".strip()
        msg.tokens_in = msg.tokens_in or 0
        msg.tokens_out = 0
    await terminalize_generation_run_locked(
        session,
        run,
        status="cancelled",
        finished_at=now,
    )
    for operation in operations:
        operation.status = "cancelled"
        operation.capacity_reason = None
        operation.next_attempt_at = None
        operation.finished_at = now
    await compile_terminal_run_memory(session, run)


async def compile_terminal_run_memory(session: AsyncSession, run: GenerationRun) -> None:
    """Compile memory behind a savepoint so memory cannot corrupt run state."""

    if run.status not in {"completed", "failed", "cancelled"}:
        return
    try:
        from yleum_api.core.config import get_settings
        from yleum_api.services.project_memory import compile_project_memory_revision

        if not get_settings().use_project_memory:
            return
        async with session.begin_nested():
            await compile_project_memory_revision(session, run)
    except Exception:
        logging.getLogger(__name__).exception(
            "project memory compile failed for run %s",
            run.id,
        )


def prompt_hash(prompt: str) -> str:
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()


async def reserve_generation_run(
    session: AsyncSession,
    *,
    project_id: UUID,
    user_id: UUID,
    idempotency_key: str,
    prompt: str,
) -> tuple[GenerationRun, bool]:
    """Atomically reserve the only active execution slot for a project.

    The transaction-scoped advisory lock serialises the check+insert across API
    processes. A retry with the same key replays the original run. A different
    request while work is active is rejected instead of racing the same repo.
    """

    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:project_id))"),
        {"project_id": str(project_id)},
    )

    existing = (
        await session.execute(
            select(GenerationRun).where(
                GenerationRun.project_id == project_id,
                GenerationRun.idempotency_key == idempotency_key,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        if existing.prompt_hash != prompt_hash(prompt):
            raise ApiError(
                "idempotency_conflict",
                "idempotency key was already used for another prompt",
                status.HTTP_409_CONFLICT,
                details={"run_id": str(existing.id)},
            )
        return existing, True

    # Restoration uses this same lock order and keeps its claim across worker restarts.
    from yleum_api.services.restorations import assert_no_active_restoration

    await session.execute(select(Project.id).where(Project.id == project_id).with_for_update())
    await assert_no_active_restoration(session, project_id)

    active = (
        await session.execute(
            select(GenerationRun)
            .where(
                GenerationRun.project_id == project_id,
                GenerationRun.status.in_(ACTIVE_GENERATION_STATUSES),
            )
            .order_by(GenerationRun.created_at.desc())
            .limit(1)
            .execution_options(populate_existing=True)
            .with_for_update()
        )
    ).scalar_one_or_none()

    # The assistant row is finalised before llm.done is published. Close this
    # tiny lifecycle gap here so a queued prompt arriving on llm.done is accepted
    # even if the task callback has not updated generation_runs yet.
    if active is not None and active.assistant_message_id is not None:
        assistant = await session.get(Message, active.assistant_message_id)
        if assistant is not None and assistant.tokens_out is not None:
            completion_error = _generation_completion_error(active, assistant)
            await terminalize_generation_run_locked(
                session,
                active,
                status="failed" if completion_error else "completed",
                error=completion_error if completion_error and not active.error else None,
            )
            await session.flush()
            await compile_terminal_run_memory(session, active)
            active = None

    if active is not None:
        raise ApiError(
            "generation_active",
            "generation already in progress",
            status.HTTP_409_CONFLICT,
            details={
                "active_run_id": str(active.id),
                "active_message_id": (
                    str(active.assistant_message_id) if active.assistant_message_id else None
                ),
                "active_status": active.status,
            },
        )

    # Self-heal a missed compiler write (or bootstrap one last pre-0046 run)
    # before the new worker reads project memory. Idempotency on run_id makes this
    # a cheap no-op during normal operation.
    latest_terminal = (
        await session.execute(
            select(GenerationRun)
            .where(
                GenerationRun.project_id == project_id,
                GenerationRun.status.in_(("completed", "failed", "cancelled")),
            )
            .order_by(GenerationRun.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if latest_terminal is not None:
        await compile_terminal_run_memory(session, latest_terminal)

    run = GenerationRun(
        project_id=project_id,
        user_id=user_id,
        idempotency_key=idempotency_key,
        prompt_hash=prompt_hash(prompt),
        status="pending",
    )
    session.add(run)
    await session.flush()
    return run, False


async def _recover_interrupted_generation_runs(session: AsyncSession) -> int:
    runs = list(
        (
            await session.execute(
                select(GenerationRun)
                .where(
                    GenerationRun.status.in_(INTERRUPTED_GENERATION_STATUSES),
                    GenerationRun.execution_backend == "api",
                )
                .with_for_update()
            )
        )
        .scalars()
        .all()
    )
    now = datetime.now(UTC)
    marker = "[Генерация прервана перезапуском сервера — отправьте запрос повторно]"
    for run in runs:
        await terminalize_generation_run_locked(
            session,
            run,
            status="failed",
            error="API process restarted before generation completed",
            finished_at=now,
        )
        if run.assistant_message_id is None:
            await compile_terminal_run_memory(session, run)
            continue
        message = await session.get(Message, run.assistant_message_id)
        if message is not None and message.tokens_out is None:
            if marker not in (message.content or ""):
                message.content = f"{message.content.rstrip()}\n\n{marker}".strip()
            message.tokens_in = message.tokens_in or 0
            message.tokens_out = 0
        await compile_terminal_run_memory(session, run)

    await session.commit()
    await _retry_terminal_adaptation_notifications(session)
    return len(runs)


async def recover_interrupted_generation_runs(
    session: AsyncSession | None = None,
) -> int:
    """Release executions that cannot survive an API-process restart.

    Only API-owned coroutines are interrupted by this process restarting.
    Independently owned worker dispatches are excluded even before they start;
    the worker resumes its pending queue and keeps live executions in place.
    Finalising legacy API-owned work prevents a permanent single-flight lock
    and a chat row that looks as if it were streaming forever.
    """

    if session is not None:
        return await _recover_interrupted_generation_runs(session)

    from yleum_api.core.db import get_engine

    factory = async_sessionmaker(get_engine(), expire_on_commit=False)
    async with factory() as own_session:
        return await _recover_interrupted_generation_runs(own_session)


async def set_generation_run_status(
    run_id: UUID,
    new_status: str,
    *,
    error: str | None = None,
) -> None:
    """Update lifecycle state from a fire-and-forget task in its own session."""

    from yleum_api.core.db import get_engine

    factory = async_sessionmaker(get_engine(), expire_on_commit=False)
    async with factory() as session:
        run = await session.get(GenerationRun, run_id)
        if run is None:
            return
        if run.status in {"completed", "failed", "cancelled"}:
            await session.rollback()
            return
        now = datetime.now(UTC)
        if new_status == "running" and run.status == "queued_for_capacity":
            await session.rollback()
            return
        if new_status in {"cancelled", "completed", "failed"}:
            await terminalize_generation_run_locked(
                session,
                run,
                status=cast(Literal["completed", "failed", "cancelled"], new_status),
                error=error,
                finished_at=now,
            )
        else:
            run.status = new_status
        if new_status == "running" and run.started_at is None:
            run.started_at = now
        if error is not None and new_status not in {"cancelled", "completed", "failed"}:
            run.error = error[:2000]
        await compile_terminal_run_memory(session, run)
        await session.commit()


async def _finalize_generation_run(session: AsyncSession, run_id: UUID) -> str:
    run = await session.scalar(
        select(GenerationRun)
        .where(GenerationRun.id == run_id)
        .execution_options(populate_existing=True)
        .with_for_update()
    )
    if run is None:
        return "failed"
    if run.status in {"completed", "failed", "cancelled"}:
        await session.commit()
        return run.status
    message = (
        await session.get(Message, run.assistant_message_id)
        if run.assistant_message_id is not None
        else None
    )
    completion_error = _generation_completion_error(run, message)
    await terminalize_generation_run_locked(
        session,
        run,
        status="failed" if completion_error else "completed",
        error=completion_error if completion_error and not run.error else None,
    )
    await compile_terminal_run_memory(session, run)
    await session.commit()
    return run.status


async def finalize_generation_run(
    run_id: UUID,
    session: AsyncSession | None = None,
) -> str:
    """Finish a run according to its product outcome, not coroutine survival.

    A build coroutine can terminate normally after the agent reports a red build
    or an unavailable container.  Historically that was recorded as
    ``completed`` even though no snapshot existed.  Build turns are successful
    only when their assistant message points at a committed snapshot; clarify
    turns and legitimate edit no-ops remain successful without one. An explicit
    failed product outcome takes precedence even if a snapshot was committed.
    """

    if session is not None:
        return await _finalize_generation_run(session, run_id)

    from yleum_api.core.db import get_engine

    factory = async_sessionmaker(get_engine(), expire_on_commit=False)
    async with factory() as own_session:
        return await _finalize_generation_run(own_session, run_id)


async def _reconcile_completed_build_runs(session: AsyncSession) -> int:
    runs = list(
        (
            await session.execute(
                select(GenerationRun).where(
                    GenerationRun.status == "completed",
                    GenerationRun.response_mode == "build",
                )
            )
        )
        .scalars()
        .all()
    )
    changed = 0
    for run in runs:
        message = (
            await session.get(Message, run.assistant_message_id)
            if run.assistant_message_id is not None
            else None
        )
        if message is not None and message.snapshot_id is not None:
            continue
        await terminalize_generation_run_locked(
            session,
            run,
            status="failed",
            error=run.error or "build finished without a committed snapshot",
            finished_at=run.finished_at,
        )
        changed += 1
    await session.commit()
    return changed


async def reconcile_completed_build_runs(
    session: AsyncSession | None = None,
) -> int:
    """Correct historical builds that were labelled complete without a snapshot."""

    if session is not None:
        return await _reconcile_completed_build_runs(session)

    from yleum_api.core.db import get_engine

    factory = async_sessionmaker(get_engine(), expire_on_commit=False)
    async with factory() as own_session:
        return await _reconcile_completed_build_runs(own_session)
