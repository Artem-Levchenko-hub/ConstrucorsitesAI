from __future__ import annotations

import asyncio
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

from omnia_api.models.project_cell import ProjectCellActivityLease
from omnia_api.services.agent_progress import bounded_redacted_text
from omnia_api.services.project_cell_proofs import require_sha256_digest

_MAX_DIAGNOSTIC_BYTES = 4096


class ProjectCellActivityConflict(RuntimeError):
    pass


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


async def reconcile_activity(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    workspace_id: UUID,
    operation_id: UUID,
    poll_status: Callable[[UUID], Awaitable[Any]],
    cancellation_requested: bool = False,
) -> Any:
    """Reconcile a durable API lease with the controller-owned operation journal."""
    status = await poll_status(operation_id)
    now = datetime.now(status.heartbeat_at.tzinfo)
    if status.state in {"starting", "running"} and not cancellation_requested:
        async with session_factory() as session:
            lease = await session.get(ProjectCellActivityLease, operation_id)
            if lease is None or lease.workspace_id != workspace_id:
                raise ProjectCellActivityConflict("activity lease not found for reconciliation")
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
        lease = await session.get(ProjectCellActivityLease, operation_id)
        if lease is None or lease.workspace_id != workspace_id:
            raise ProjectCellActivityConflict("activity lease not found for reconciliation")
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
) -> T:
    """Run or reattach work while mirroring bounded journal progress into the DB."""
    now = datetime.now(lease.deadline_at.tzinfo)
    async with session_factory() as session:
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
            or existing.state != ActivityState.ACTIVE.value
        ):
            raise ProjectCellActivityConflict("activity replay envelope mismatch")
        await session.commit()

    async def heartbeat_loop() -> None:
        while True:
            await asyncio.sleep(heartbeat_seconds)
            try:
                status = await reconcile_activity(
                    session_factory=session_factory,
                    workspace_id=lease.workspace_id,
                    operation_id=lease.operation_id,
                    poll_status=poll_status,
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

    await emit(
        "tool.started",
        {
            "operation_id": str(lease.operation_id),
            "phase": lease.phase or lease.kind.value,
            "deadline_at": lease.deadline_at.isoformat(),
            "log_bytes": 0,
        },
    )
    heartbeat = asyncio.create_task(heartbeat_loop())
    try:
        result = await work()
    except asyncio.CancelledError:
        await reconcile_activity(
            session_factory=session_factory,
            workspace_id=lease.workspace_id,
            operation_id=lease.operation_id,
            poll_status=poll_status,
            cancellation_requested=True,
        )
        await emit(
            "tool.finished",
            {
                "operation_id": str(lease.operation_id),
                "phase": lease.phase or lease.kind.value,
                "state": ActivityState.CANCELLED.value,
            },
        )
        raise
    except Exception as exc:
        async with session_factory() as session:
            await finish_activity(
                session,
                operation_id=lease.operation_id,
                state=ActivityState.FAILED,
                finished_at=datetime.now(lease.deadline_at.tzinfo),
                diagnostic=type(exc).__name__,
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
        raise
    else:
        result_state = (
            terminal_state(result)
            if terminal_state is not None
            else ActivityState.COMPLETED
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
