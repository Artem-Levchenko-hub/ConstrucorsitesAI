"""Durable FIFO coordination for physical Project Cell capacity."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import cast
from uuid import UUID

from sqlalchemy import case, exists, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from omnia_api.models.generation_run import GenerationRun
from omnia_api.models.project_cell import ProjectCellOperation, ProjectCellWorkspace
from omnia_api.services.generation_runs import (
    ACTIVE_GENERATION_STATUSES,
    write_capacity_dispatch_claim,
)
from omnia_api.services.orchestrator_client import ProjectCellOrchestratorClient
from omnia_api.services.project_cell_lifecycle import (
    ProjectCellOperationOutcome,
    execute_cell_operation,
    replay_indeterminate_cell_operation,
)
from omnia_api.services.project_cells import ACTIVE_OPERATION_STATUSES, reserve_cell_operation

_SCHEDULER_LOCK_KEY = "project-cell-capacity-scheduler"
_WAITING_ACTION = "Ожидаю ресурсы сервера"
_WAITING_DETAIL = (
    "Проект сохранён и запустится автоматически, как только освободится мощность."
)
_LOCAL_ADMISSION_EVENTS: dict[UUID, asyncio.Event] = {}


def capacity_admission_event(run_id: UUID) -> asyncio.Event:
    event = _LOCAL_ADMISSION_EVENTS.get(run_id)
    if event is None:
        event = asyncio.Event()
        _LOCAL_ADMISSION_EVENTS[run_id] = event
    return event


def signal_capacity_admitted(run_id: UUID) -> None:
    capacity_admission_event(run_id).set()


def clear_capacity_admission_event(run_id: UUID) -> None:
    _LOCAL_ADMISSION_EVENTS.pop(run_id, None)


@dataclass(frozen=True, slots=True)
class CapacityTurn:
    run_id: UUID
    is_head: bool
    position: int
    reason: str | None
    retry_after_seconds: int


async def _scheduler_lock(session: AsyncSession) -> None:
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
        {"key": _SCHEDULER_LOCK_KEY},
    )


async def claim_capacity_turn(session: AsyncSession, run_id: UUID) -> CapacityTurn:
    await _scheduler_lock(session)
    run = await session.get(GenerationRun, run_id)
    if run is None or run.status != "queued_for_capacity":
        return CapacityTurn(
            run_id=run_id,
            is_head=False,
            position=0,
            reason=None,
            retry_after_seconds=1,
        )
    queued = list(
        (
            await session.execute(
                select(GenerationRun)
                .where(GenerationRun.status == "queued_for_capacity")
                .order_by(GenerationRun.created_at, GenerationRun.id)
            )
        )
        .scalars()
        .all()
    )
    position = next(
        (index for index, item in enumerate(queued, start=1) if item.id == run_id),
        0,
    )
    operation = await session.scalar(
        select(ProjectCellOperation)
        .where(
            ProjectCellOperation.generation_run_id == run_id,
            ProjectCellOperation.status == "waiting_capacity",
        )
        .order_by(ProjectCellOperation.created_at.desc())
        .limit(1)
    )
    return CapacityTurn(
        run_id=run_id,
        is_head=position == 1,
        position=position,
        reason=operation.capacity_reason if operation is not None else None,
        retry_after_seconds=1,
    )


async def claim_idle_hibernation_victim(
    session: AsyncSession,
    *,
    requesting_run_id: UUID,
) -> ProjectCellWorkspace | None:
    await _scheduler_lock(session)
    requesting_run = await session.get(GenerationRun, requesting_run_id)
    if requesting_run is None:
        return None
    return cast(
        ProjectCellWorkspace | None,
        await session.scalar(
            select(ProjectCellWorkspace)
            .where(
                ProjectCellWorkspace.project_id != requesting_run.project_id,
                ProjectCellWorkspace.state == "ready",
            ProjectCellWorkspace.generation_run_id.is_(None),
            ProjectCellWorkspace.deleted_at.is_(None),
            ~exists(
                select(ProjectCellOperation.id).where(
                    ProjectCellOperation.workspace_id == ProjectCellWorkspace.id,
                    ProjectCellOperation.status.in_(ACTIVE_OPERATION_STATUSES),
                )
            ),
            )
            .order_by(
                case((ProjectCellWorkspace.ready_at.is_(None), 1), else_=0),
                ProjectCellWorkspace.ready_at,
                ProjectCellWorkspace.updated_at,
                ProjectCellWorkspace.id,
            )
            .with_for_update(skip_locked=True)
            .limit(1)
        )
    )


async def claim_stale_generation_lease(
    session: AsyncSession,
    *,
    requesting_run_id: UUID,
) -> tuple[ProjectCellWorkspace, UUID] | None:
    """Claim a ready workspace whose durable generation is already terminal."""

    await _scheduler_lock(session)
    requesting_run = await session.get(GenerationRun, requesting_run_id)
    if requesting_run is None:
        return None
    row = (
        await session.execute(
            select(ProjectCellWorkspace, GenerationRun.id)
            .join(
                GenerationRun,
                GenerationRun.id == ProjectCellWorkspace.generation_run_id,
            )
            .where(
                ProjectCellWorkspace.project_id != requesting_run.project_id,
                ProjectCellWorkspace.state == "ready",
                ProjectCellWorkspace.deleted_at.is_(None),
                GenerationRun.status.not_in(ACTIVE_GENERATION_STATUSES),
                ~exists(
                    select(ProjectCellOperation.id).where(
                        ProjectCellOperation.workspace_id == ProjectCellWorkspace.id,
                        ProjectCellOperation.status.in_(ACTIVE_OPERATION_STATUSES),
                    )
                ),
            )
            .order_by(
                case((ProjectCellWorkspace.ready_at.is_(None), 1), else_=0),
                ProjectCellWorkspace.ready_at,
                ProjectCellWorkspace.updated_at,
                ProjectCellWorkspace.id,
            )
            .with_for_update(of=ProjectCellWorkspace, skip_locked=True)
            .limit(1)
        )
    ).one_or_none()
    if row is None:
        return None
    return row[0], row[1]


async def release_one_stale_generation_lease(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    requesting_run_id: UUID,
    client: ProjectCellOrchestratorClient,
) -> bool:
    """Fence and release one terminal run without stopping ready compute."""

    async with session_factory() as session:
        claimed = await claim_stale_generation_lease(
            session,
            requesting_run_id=requesting_run_id,
        )
        if claimed is None:
            await session.rollback()
            return False
        workspace, stale_run_id = claimed
        operation = await session.scalar(
            select(ProjectCellOperation)
            .where(
                ProjectCellOperation.workspace_id == workspace.id,
                ProjectCellOperation.generation_run_id == stale_run_id,
                ProjectCellOperation.kind == "release",
                ProjectCellOperation.status == "indeterminate",
            )
            .order_by(ProjectCellOperation.created_at.desc())
            .limit(1)
        )
        replay_indeterminate = operation is not None
        if operation is None:
            operation, _ = await reserve_cell_operation(
                session,
                workspace_id=workspace.id,
                generation_run_id=stale_run_id,
                kind="release",
                idempotency_key=f"capacity:release:{workspace.id}:{stale_run_id}",
                request={},
            )
        workspace_id = workspace.id
        await session.commit()

    if replay_indeterminate:
        outcome = await replay_indeterminate_cell_operation(
            session_factory,
            operation.id,
            client,
        )
    else:
        outcome = await execute_cell_operation(session_factory, operation.id, client)
    response = outcome.response
    if (
        outcome.status != "completed"
        or response is None
        or response.workspace_id != workspace_id
        or response.state != "resources_ready"
        or response.fencing_epoch is None
    ):
        return False
    async with session_factory() as session:
        await _scheduler_lock(session)
        locked_workspace = await session.scalar(
            select(ProjectCellWorkspace)
            .where(ProjectCellWorkspace.id == workspace_id)
            .with_for_update()
        )
        later_effect_may_be_unknown = await session.scalar(
            select(ProjectCellOperation.id)
            .where(
                ProjectCellOperation.workspace_id == workspace_id,
                ProjectCellOperation.fencing_epoch > response.fencing_epoch,
                ProjectCellOperation.status.not_in(("failed", "cancelled")),
            )
            .limit(1)
        )
        if (
            locked_workspace is None
            or locked_workspace.generation_run_id != stale_run_id
            or locked_workspace.fencing_epoch < response.fencing_epoch
            or later_effect_may_be_unknown is not None
        ):
            await session.rollback()
            return False
        locked_workspace.generation_run_id = None
        locked_workspace.updated_at = datetime.now(UTC)
        await session.commit()
    return True


async def hibernate_one_idle_workspace(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    requesting_run_id: UUID,
    client: ProjectCellOrchestratorClient,
) -> bool:
    await release_one_stale_generation_lease(
        session_factory,
        requesting_run_id=requesting_run_id,
        client=client,
    )
    async with session_factory() as session:
        victim = await claim_idle_hibernation_victim(
            session,
            requesting_run_id=requesting_run_id,
        )
        if victim is None:
            await session.rollback()
            return False
        checkpoint_ref = f"capacity-{requesting_run_id.hex[:12]}"
        operation, _ = await reserve_cell_operation(
            session,
            workspace_id=victim.id,
            generation_run_id=None,
            kind="pause",
            idempotency_key=f"capacity:{requesting_run_id}:pause:{victim.id}",
            request={"checkpoint_ref": checkpoint_ref},
        )
        victim_id = victim.id
        await session.commit()

    outcome = await execute_cell_operation(session_factory, operation.id, client)
    response = outcome.response
    if (
        outcome.status != "completed"
        or response is None
        or response.workspace_id != victim_id
        or response.state != "resources_paused"
        or response.fencing_epoch is None
    ):
        return False
    async with session_factory() as session:
        workspace = await session.scalar(
            select(ProjectCellWorkspace)
            .where(ProjectCellWorkspace.id == victim_id)
            .with_for_update()
        )
        if (
            workspace is None
            or workspace.generation_run_id is not None
            or workspace.fencing_epoch != response.fencing_epoch
        ):
            await session.rollback()
            return False
        workspace.state = "stopped"
        workspace.updated_at = datetime.now(UTC)
        await session.commit()
    return True


async def wait_for_capacity(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    run_id: UUID,
    operation_id: UUID,
    client: ProjectCellOrchestratorClient,
    emit: Callable[[dict[str, object]], Awaitable[None]],
    dispatch_token: UUID | None = None,
) -> ProjectCellOperationOutcome:
    last_progress: tuple[int, str | None] | None = None
    last_emitted_at = 0.0
    while True:
        async with session_factory() as session:
            run = await session.scalar(
                select(GenerationRun)
                .where(GenerationRun.id == run_id)
                .with_for_update()
            )
            if run is None:
                raise RuntimeError("queued generation run disappeared")
            if run.status in {"cancel_requested", "cancelled"}:
                return await _cancel_waiting_operation(session, operation_id)
            run.status = "queued_for_capacity"
            if dispatch_token is not None:
                write_capacity_dispatch_claim(
                    run,
                    token=dispatch_token,
                    lease_seconds=30,
                )
            turn = await claim_capacity_turn(session, run_id)
            await session.commit()

        progress = (turn.position, turn.reason)
        now = time.monotonic()
        if progress != last_progress or now - last_emitted_at >= 15:
            await emit(
                {
                    "action": _WAITING_ACTION,
                    "detail": _WAITING_DETAIL,
                    "status": "running",
                    "queue_position": turn.position,
                    "capacity_reason": turn.reason,
                }
            )
            last_progress = progress
            last_emitted_at = now
        if not turn.is_head:
            await asyncio.sleep(max(1, min(turn.retry_after_seconds, 10)))
            continue

        await hibernate_one_idle_workspace(
            session_factory,
            requesting_run_id=run_id,
            client=client,
        )
        outcome = await execute_cell_operation(session_factory, operation_id, client)
        if outcome.status == "waiting_capacity":
            async with session_factory() as session:
                operation = await session.get(ProjectCellOperation, operation_id)
                delay = 1
                if operation is not None and operation.next_attempt_at is not None:
                    delay = max(
                        1,
                        min(
                            10,
                            int(
                                (
                                    operation.next_attempt_at - datetime.now(UTC)
                                ).total_seconds()
                            ),
                        ),
                    )
            await asyncio.sleep(delay)
            continue
        if outcome.status == "completed":
            return outcome
        return outcome


async def _cancel_waiting_operation(
    session: AsyncSession,
    operation_id: UUID,
) -> ProjectCellOperationOutcome:
    operation = await session.scalar(
        select(ProjectCellOperation)
        .where(ProjectCellOperation.id == operation_id)
        .with_for_update()
    )
    if operation is None:
        raise RuntimeError("queued Project Cell operation disappeared")
    if operation.status in {"pending", "waiting_capacity"}:
        operation.status = "cancelled"
        operation.finished_at = datetime.now(UTC)
        operation.capacity_reason = None
        operation.next_attempt_at = None
        await session.commit()
    return ProjectCellOperationOutcome(
        operation_id=operation.id,
        workspace_id=operation.workspace_id,
        kind=operation.kind,
        status=operation.status,
        fencing_epoch=operation.fencing_epoch,
        response=None,
        result_payload=None,
        error=operation.error,
        reconciles_operation_id=None,
    )
