from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from yleum_api.models.project_cell import ProjectCellActivityLease, ProjectCellWorkspace
from yleum_api.services.agent_progress import bounded_redacted_text
from yleum_api.services.orchestrator_client import OrchestratorUnavailable
from yleum_api.services.project_cell_errors import (
    PROTECTED_ENVIRONMENT_RECOVERY_REQUIRED,
    ProjectCellInfrastructureError,
    terminal_cell_error,
)
from yleum_api.services.project_cell_proofs import require_sha256_digest

_MAX_DIAGNOSTIC_BYTES = 4096
_FINAL_POLL_SECONDS = 5.0
_FINAL_POLL_ATTEMPTS = 3
_CONTROLLER_TERMINAL_STATES = {"completed", "failed", "timed_out", "cancelled"}
logger = logging.getLogger(__name__)


class ProjectCellActivityConflict(ProjectCellInfrastructureError):
    def __init__(self, message: str) -> None:
        super().__init__("activity_conflict")
        self.args = (message,)


class ActivityKind(StrEnum):
    COMMAND = "command"
    TOOL = "tool"
    FINALIZATION = "finalization"
    SNAPSHOT = "snapshot"
    PROMOTION = "promotion"


class ActivityState(StrEnum):
    ACTIVE = "active"
    COMPLETED = "completed"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class ActivityStart:
    operation_id: UUID
    workspace_id: UUID
    generation_run_id: UUID | None
    kind: ActivityKind
    fencing_epoch: int
    deadline_at: datetime
    proof_key: str | None = None
    phase: str | None = None


def _ensure_aware(now: datetime, label: str) -> None:
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError(f"{label} must be timezone-aware")


def _bounded_diagnostic(value: str) -> str:
    return bounded_redacted_text(value.strip(), max_bytes=_MAX_DIAGNOSTIC_BYTES)


async def start_activity(
    session: AsyncSession,
    *,
    workspace_id: UUID,
    generation_run_id: UUID | None,
    kind: ActivityKind,
    fencing_epoch: int,
    deadline_at: datetime,
    now: datetime,
    operation_id: UUID | None = None,
    proof_key: str | None = None,
    phase: str | None = None,
) -> ProjectCellActivityLease:
    _ensure_aware(now, "now")
    _ensure_aware(deadline_at, "deadline_at")
    if fencing_epoch <= 0:
        raise ValueError("fencing_epoch must be positive")
    if deadline_at < now:
        raise ValueError("deadline_at must not be earlier than now")
    if proof_key is not None:
        require_sha256_digest(proof_key, "proof_key")
    lease = ProjectCellActivityLease(
        operation_id=uuid4() if operation_id is None else operation_id,
        workspace_id=workspace_id,
        generation_run_id=generation_run_id,
        kind=kind.value,
        state=ActivityState.ACTIVE.value,
        fencing_epoch=fencing_epoch,
        proof_key=proof_key,
        phase=phase,
        started_at=now,
        deadline_at=deadline_at,
        heartbeat_at=now,
        log_bytes=0,
    )
    try:
        async with session.begin_nested():
            session.add(lease)
            await session.flush()
    except IntegrityError as exc:
        raise ProjectCellActivityConflict("workspace already has an active activity") from exc
    return lease


async def heartbeat_activity(
    session: AsyncSession,
    *,
    operation_id: UUID,
    workspace_id: UUID,
    fencing_epoch: int,
    heartbeat_at: datetime,
    phase: str | None = None,
    log_bytes: int | None = None,
    diagnostic: str | None = None,
) -> ProjectCellActivityLease:
    _ensure_aware(heartbeat_at, "heartbeat_at")
    lease = await session.scalar(
        select(ProjectCellActivityLease)
        .where(
            ProjectCellActivityLease.operation_id == operation_id,
            ProjectCellActivityLease.workspace_id == workspace_id,
            ProjectCellActivityLease.fencing_epoch == fencing_epoch,
            ProjectCellActivityLease.state == ActivityState.ACTIVE.value,
        )
        .with_for_update()
    )
    if lease is None:
        raise ProjectCellActivityConflict("exact active activity lease not found")
    if heartbeat_at < lease.heartbeat_at:
        raise ValueError("heartbeat_at must be monotonic")
    lease.heartbeat_at = heartbeat_at
    if phase is not None:
        lease.phase = phase
    if log_bytes is not None:
        if log_bytes < 0:
            raise ValueError("log_bytes must be non-negative")
        lease.log_bytes = log_bytes
    if diagnostic is not None:
        lease.redacted_diagnostic = _bounded_diagnostic(diagnostic)
    await session.flush()
    return lease


async def finish_activity(
    session: AsyncSession,
    *,
    operation_id: UUID,
    state: ActivityState,
    finished_at: datetime,
    diagnostic: str | None = None,
    log_bytes: int | None = None,
) -> ProjectCellActivityLease:
    _ensure_aware(finished_at, "finished_at")
    if state is ActivityState.ACTIVE:
        raise ValueError("finish_activity requires a terminal state")
    lease = await session.scalar(
        select(ProjectCellActivityLease)
        .where(ProjectCellActivityLease.operation_id == operation_id)
        .with_for_update()
    )
    if lease is None:
        raise ProjectCellActivityConflict("activity lease not found")
    if lease.state != ActivityState.ACTIVE.value:
        if lease.state == state.value:
            return lease
        raise ProjectCellActivityConflict(
            f"activity already {lease.state}; cannot finish as {state.value}"
        )
    if finished_at < lease.heartbeat_at:
        raise ValueError("finished_at must not be earlier than the last heartbeat")
    lease.state = state.value
    lease.finished_at = finished_at
    lease.heartbeat_at = finished_at
    if log_bytes is not None:
        if log_bytes < 0:
            raise ValueError("log_bytes must be non-negative")
        lease.log_bytes = log_bytes
    if diagnostic is not None:
        lease.redacted_diagnostic = _bounded_diagnostic(diagnostic)
    await session.flush()
    return lease


async def activity_blocks_hibernation(
    session: AsyncSession,
    *,
    workspace_id: UUID,
) -> bool:
    active = await session.scalar(
        select(ProjectCellActivityLease.operation_id).where(
            ProjectCellActivityLease.workspace_id == workspace_id,
            ProjectCellActivityLease.state == ActivityState.ACTIVE.value,
        )
    )
    return active is not None


def _validate_activity_envelope(
    lease: ProjectCellActivityLease,
    expected: ActivityStart | None,
) -> None:
    if expected is not None and (
        lease.workspace_id != expected.workspace_id
        or lease.generation_run_id != expected.generation_run_id
        or lease.fencing_epoch != expected.fencing_epoch
        or lease.proof_key != expected.proof_key
        or lease.kind != expected.kind.value
    ):
        raise ProjectCellActivityConflict("activity reconciliation envelope mismatch")


async def _finish_local_cancellation(
    session_factory: async_sessionmaker[AsyncSession],
    lease: ActivityStart,
) -> None:
    async with session_factory() as session:
        saved = await session.scalar(
            select(ProjectCellActivityLease)
            .where(ProjectCellActivityLease.operation_id == lease.operation_id)
            .with_for_update()
        )
        if saved is None:
            raise ProjectCellActivityConflict("activity lease not found for cancellation")
        _validate_activity_envelope(saved, lease)
        await finish_activity(
            session,
            operation_id=lease.operation_id,
            state=ActivityState.CANCELLED,
            finished_at=max(datetime.now(lease.deadline_at.tzinfo), saved.heartbeat_at),
        )
        await session.commit()


async def _settle_cancelled_activity(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    lease: ActivityStart,
    poll_status: Callable[[UUID], Awaitable[Any]],
    emit: Callable[[str, Mapping[str, object]], Awaitable[None]],
    controller_owned: bool,
) -> None:
    try:
        if not controller_owned:
            # The caller's awaited local work has already unwound. No remote
            # journal can prove this local probe's cancellation.
            await _finish_local_cancellation(session_factory, lease)
        else:
            deadline = asyncio.get_running_loop().time() + _FINAL_POLL_SECONDS
            for attempt in range(_FINAL_POLL_ATTEMPTS):
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    return
                try:
                    async with asyncio.timeout(remaining):
                        status = await poll_status(lease.operation_id)
                except (OrchestratorUnavailable, TimeoutError):
                    status = None
                if status is not None:

                    async def observed_status(_operation_id: UUID, observed: Any = status) -> Any:
                        return observed

                    await reconcile_activity(
                        session_factory=session_factory,
                        workspace_id=lease.workspace_id,
                        operation_id=lease.operation_id,
                        poll_status=observed_status,
                        cancellation_requested=True,
                        expected_lease=lease,
                    )
                    if status.state in _CONTROLLER_TERMINAL_STATES:
                        break
                if attempt + 1 == _FINAL_POLL_ATTEMPTS:
                    return
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    return
                await asyncio.sleep(min(0.1 * (attempt + 1), remaining))
        # Publication follows the durable terminal commit. Its failure cannot
        # replace cancellation or cause the command to be dispatched again.
        async with asyncio.timeout(1):
            await emit(
                "tool.finished",
                {
                    "operation_id": str(lease.operation_id),
                    "phase": lease.phase or lease.kind.value,
                    "state": ActivityState.CANCELLED.value,
                },
            )
    except (Exception, asyncio.CancelledError):
        logger.warning(
            "cancelled activity settlement remains pending or event unavailable",
            extra={"operation_id": str(lease.operation_id)},
        )


async def _drain_cancelled_cleanup(task: asyncio.Task[None]) -> None:
    # A second Stop must not strand a shielded DB writer behind the owner lock.
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            continue
    task.result()


async def reconcile_activity(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    workspace_id: UUID,
    operation_id: UUID,
    poll_status: Callable[[UUID], Awaitable[Any]],
    cancellation_requested: bool = False,
    controller_owned: bool = True,
    expected_lease: ActivityStart | None = None,
) -> Any:
    """Reconcile a durable API lease with the controller-owned operation journal."""
    status = await poll_status(operation_id)
    if controller_owned and (
        getattr(status, "operation_id", None) != operation_id
        or status.state not in {"starting", "running", *_CONTROLLER_TERMINAL_STATES}
        or (
            status.terminal_response is not None
            and status.terminal_response.operation_id != operation_id
        )
    ):
        raise ProjectCellActivityConflict("controller activity journal envelope mismatch")
    now = datetime.now(status.heartbeat_at.tzinfo)
    if status.state in {"starting", "running"}:
        async with session_factory() as session:
            lease = await session.get(ProjectCellActivityLease, operation_id)
            if lease is None or lease.workspace_id != workspace_id:
                raise ProjectCellActivityConflict("activity lease not found for reconciliation")
            _validate_activity_envelope(lease, expected_lease)
            await heartbeat_activity(
                session,
                operation_id=operation_id,
                workspace_id=workspace_id,
                fencing_epoch=lease.fencing_epoch,
                heartbeat_at=max(now, lease.heartbeat_at),
                phase=status.phase,
                log_bytes=status.log_bytes,
            )
            await session.commit()
        return status

    terminal_response = status.terminal_response
    terminal = (
        ActivityState.CANCELLED
        if cancellation_requested
        else ActivityState.TIMED_OUT
        if getattr(terminal_response, "timed_out", False)
        else ActivityState.FAILED
        if terminal_response is not None and not terminal_response.ok
        else ActivityState.COMPLETED
        if status.state == "completed"
        else ActivityState.TIMED_OUT
        if status.state == "timed_out"
        else ActivityState.CANCELLED
        if status.state == "cancelled"
        else ActivityState.FAILED
    )
    async with session_factory() as session:
        lease = await session.scalar(
            select(ProjectCellActivityLease)
            .where(ProjectCellActivityLease.operation_id == operation_id)
            .with_for_update()
        )
        if lease is None or lease.workspace_id != workspace_id:
            raise ProjectCellActivityConflict("activity lease not found for reconciliation")
        _validate_activity_envelope(lease, expected_lease)
        await finish_activity(
            session,
            operation_id=operation_id,
            state=terminal,
            finished_at=max(now, lease.heartbeat_at),
            log_bytes=status.log_bytes,
            diagnostic=(
                status.terminal_response.detail
                if status.terminal_response is not None
                else status.state
            ),
        )
        await session.commit()
    return status


async def run_with_activity_lease[T](
    *,
    session_factory: async_sessionmaker[AsyncSession],
    lease: ActivityStart,
    work: Callable[[], Awaitable[T]],
    poll_status: Callable[[UUID], Awaitable[Any]],
    emit: Callable[[str, Mapping[str, object]], Awaitable[None]],
    heartbeat_seconds: int = 15,
    terminal_state: Callable[[T], ActivityState] | None = None,
    replay_terminal: Callable[[Any], Awaitable[T]] | None = None,
    controller_owned: bool = True,
) -> T:
    """Run or reattach work while mirroring bounded journal progress into the DB."""
    now = datetime.now(lease.deadline_at.tzinfo)
    existing_state = ActivityState.ACTIVE.value
    diagnostic = None
    async with session_factory() as session:
        workspace = await session.get(ProjectCellWorkspace, lease.workspace_id)
        if (
            workspace is None
            or workspace.fencing_epoch != lease.fencing_epoch
            or workspace.generation_run_id != lease.generation_run_id
        ):
            raise ProjectCellActivityConflict(
                "activity replay envelope mismatch: workspace lease changed"
            )
        existing = await session.get(ProjectCellActivityLease, lease.operation_id)
        if existing is None:
            await start_activity(
                session,
                workspace_id=lease.workspace_id,
                generation_run_id=lease.generation_run_id,
                kind=lease.kind,
                fencing_epoch=lease.fencing_epoch,
                deadline_at=lease.deadline_at,
                now=now,
                operation_id=lease.operation_id,
                proof_key=lease.proof_key,
                phase=lease.phase,
            )
        elif (
            existing.workspace_id != lease.workspace_id
            or existing.fencing_epoch != lease.fencing_epoch
            or existing.proof_key != lease.proof_key
            or existing.generation_run_id != lease.generation_run_id
            or existing.kind != lease.kind.value
        ):
            raise ProjectCellActivityConflict("activity replay envelope mismatch")
        if existing is not None:
            existing_state = existing.state
            diagnostic = existing.redacted_diagnostic
        await session.commit()

    if existing_state != ActivityState.ACTIVE.value:
        if diagnostic == PROTECTED_ENVIRONMENT_RECOVERY_REQUIRED:
            raise ProjectCellInfrastructureError(diagnostic, lease.operation_id)
        if existing_state == ActivityState.CANCELLED.value:
            raise asyncio.CancelledError
        if replay_terminal is not None:
            try:
                status = await poll_status(lease.operation_id)
            except Exception as exc:
                terminal = terminal_cell_error(exc, operation_id=lease.operation_id)
                raise terminal or ProjectCellInfrastructureError(
                    "activity_replay_unavailable",
                    lease.operation_id,
                ) from None
            if (
                status.operation_id != lease.operation_id
                or status.state not in {"completed", existing_state}
                or status.terminal_response is None
            ):
                raise ProjectCellInfrastructureError(
                    "activity_replay_unavailable", lease.operation_id
                )
            try:
                result = await replay_terminal(status)
            except Exception as exc:
                terminal = terminal_cell_error(exc, operation_id=lease.operation_id)
                raise terminal or ProjectCellInfrastructureError(
                    "activity_replay_unavailable",
                    lease.operation_id,
                ) from None
            expected_state = terminal_state(result) if terminal_state else ActivityState.COMPLETED
            if expected_state.value != existing_state:
                raise ProjectCellActivityConflict("activity replay terminal outcome mismatch")
            return result
        raise ProjectCellInfrastructureError(f"activity_{existing_state}", lease.operation_id)

    async def heartbeat_loop() -> None:
        while True:
            await asyncio.sleep(heartbeat_seconds)
            try:
                status = await reconcile_activity(
                    session_factory=session_factory,
                    workspace_id=lease.workspace_id,
                    operation_id=lease.operation_id,
                    poll_status=poll_status,
                    controller_owned=controller_owned,
                    expected_lease=lease,
                )
                await emit(
                    "tool.heartbeat",
                    {
                        "operation_id": str(lease.operation_id),
                        "phase": status.phase,
                        "deadline_at": lease.deadline_at.isoformat(),
                        "log_bytes": status.log_bytes,
                    },
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                # A transient journal/event transport failure must not replace
                # the command result. Final DB persistence below still fails
                # closed if durable state itself is unavailable.
                continue

    heartbeat = asyncio.create_task(heartbeat_loop())
    work_entered = False
    try:
        await emit(
            "tool.started",
            {
                "operation_id": str(lease.operation_id),
                "phase": lease.phase or lease.kind.value,
                "deadline_at": lease.deadline_at.isoformat(),
                "log_bytes": 0,
            },
        )
        work_entered = True
        result = await work()
    except asyncio.CancelledError:

        async def settle_after_heartbeat() -> None:
            heartbeat.cancel()
            with suppress(asyncio.CancelledError):
                await heartbeat
            await _settle_cancelled_activity(
                session_factory=session_factory,
                lease=lease,
                poll_status=poll_status,
                emit=emit,
                # Only a newly allocated operation, before entering work, is
                # proven never dispatched. Reattached activities still require
                # the controller's authoritative terminal journal.
                controller_owned=controller_owned and (existing is not None or work_entered),
            )

        cleanup = asyncio.create_task(settle_after_heartbeat())
        await _drain_cancelled_cleanup(cleanup)
        raise
    except Exception as exc:
        if controller_owned and existing is not None and not work_entered:
            # A start-event outage proves nothing about a previously dispatched
            # command. Preserve both its writer guard and the original error;
            # terminal-generation maintenance can consult its journal later.
            raise
        terminal_error = terminal_cell_error(exc, operation_id=lease.operation_id)
        try:
            async with session_factory() as session:
                await finish_activity(
                    session,
                    operation_id=lease.operation_id,
                    state=ActivityState.FAILED,
                    finished_at=datetime.now(lease.deadline_at.tzinfo),
                    diagnostic=terminal_error.code if terminal_error else type(exc).__name__,
                )
                await session.commit()
            await emit(
                "tool.finished",
                {
                    "operation_id": str(lease.operation_id),
                    "phase": lease.phase or lease.kind.value,
                    "state": ActivityState.FAILED.value,
                },
            )
        except Exception:
            if terminal_error is None:
                raise
            # Failure persistence or event storage must not demote a known
            # fatal controller error into a repairable model observation.
            # CancelledError deliberately remains outside this guard.
        if terminal_error is not None:
            raise terminal_error from None
        raise
    else:
        result_state = (
            terminal_state(result) if terminal_state is not None else ActivityState.COMPLETED
        )
        if result_state is ActivityState.ACTIVE:
            raise ValueError("terminal_state must return a terminal activity state")
        async with session_factory() as session:
            await finish_activity(
                session,
                operation_id=lease.operation_id,
                state=result_state,
                finished_at=datetime.now(lease.deadline_at.tzinfo),
            )
            await session.commit()
        await emit(
            "tool.finished",
            {
                "operation_id": str(lease.operation_id),
                "phase": lease.phase or lease.kind.value,
                "state": result_state.value,
            },
        )
        return result
    finally:
        heartbeat.cancel()
        with suppress(asyncio.CancelledError):
            await heartbeat


__all__ = [
    "ActivityKind",
    "ActivityStart",
    "ActivityState",
    "ProjectCellActivityConflict",
    "activity_blocks_hibernation",
    "finish_activity",
    "heartbeat_activity",
    "reconcile_activity",
    "run_with_activity_lease",
    "start_activity",
]
