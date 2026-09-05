"""Durable FIFO coordination for physical Project Cell capacity."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import UUID

from sqlalchemy import and_, case, exists, or_, select, text, true
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from omnia_api.core.config import get_settings
from omnia_api.models.generation_run import GenerationRun
from omnia_api.models.project_cell import (
    ProjectCellActivityLease,
    ProjectCellOperation,
    ProjectCellWorkspace,
)
from omnia_api.services.generation_runs import (
    ACTIVE_GENERATION_STATUSES,
    write_capacity_dispatch_claim,
)
from omnia_api.services.orchestrator_client import ProjectCellOrchestratorClient
from omnia_api.services.project_cell_lifecycle import (
    ProjectCellOperationOutcome,
    execute_cell_operation,
    reconcile_indeterminate_cell_operation,
    replay_indeterminate_cell_operation,
)
from omnia_api.services.project_cell_recovery import recover_ensure_operation
from omnia_api.services.project_cells import ACTIVE_OPERATION_STATUSES, reserve_cell_operation

_SCHEDULER_LOCK_KEY = "project-cell-capacity-scheduler"
_WAITING_ACTION = "Ожидаю ресурсы сервера"
_WAITING_DETAIL = "Проект сохранён и запустится автоматически, как только освободится мощность."
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
    expected_workspace_id: UUID | None = None,
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
                (
                    ProjectCellWorkspace.project_id != requesting_run.project_id
                    if expected_workspace_id is None
                    else ProjectCellWorkspace.id == expected_workspace_id
                ),
                ProjectCellWorkspace.state == "ready",
                ProjectCellWorkspace.generation_run_id.is_(None),
                ProjectCellWorkspace.deleted_at.is_(None),
                ~exists(
                    select(ProjectCellOperation.id).where(
                        ProjectCellOperation.workspace_id == ProjectCellWorkspace.id,
                        ProjectCellOperation.status.in_(ACTIVE_OPERATION_STATUSES),
                        ~and_(
                            ProjectCellOperation.kind == "pause",
                            ProjectCellOperation.generation_run_id.is_(None),
                            ProjectCellOperation.status.in_(("pending", "waiting_capacity")),
                            ProjectCellOperation.idempotency_key.like("capacity:%:pause:%"),
                        ),
                    )
                ),
                ~exists(
                    select(ProjectCellActivityLease.operation_id).where(
                        ProjectCellActivityLease.workspace_id == ProjectCellWorkspace.id,
                        ProjectCellActivityLease.state == "active",
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
        ),
    )


async def claim_stale_generation_lease(
    session: AsyncSession,
    *,
    requesting_run_id: UUID,
    workspace_id: UUID | None = None,
) -> tuple[ProjectCellWorkspace, UUID] | None:
    """Claim terminal work, including ensure-complete/agent-bootstrap-incomplete cells."""

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
                (
                    ProjectCellWorkspace.project_id != requesting_run.project_id
                    if workspace_id is None
                    else ProjectCellWorkspace.id == workspace_id
                ),
                or_(
                    ProjectCellWorkspace.state == "ready",
                    and_(
                        ProjectCellWorkspace.state.in_(("provisioning", "failed")),
                        exists(
                            select(ProjectCellOperation.id).where(
                                ProjectCellOperation.workspace_id == ProjectCellWorkspace.id,
                                ProjectCellOperation.kind == "ensure",
                                ProjectCellOperation.status.in_(("completed", "indeterminate")),
                                ProjectCellOperation.generation_run_id
                                == ProjectCellWorkspace.generation_run_id,
                            )
                        ),
                    ),
                ),
                ProjectCellWorkspace.deleted_at.is_(None),
                GenerationRun.status.not_in(ACTIVE_GENERATION_STATUSES),
                (
                    ~exists(
                        select(ProjectCellOperation.id).where(
                            ProjectCellOperation.workspace_id == ProjectCellWorkspace.id,
                            ProjectCellOperation.generation_run_id
                            == ProjectCellWorkspace.generation_run_id,
                            ProjectCellOperation.next_attempt_at > datetime.now(UTC),
                        )
                    )
                    if workspace_id is None
                    else true()
                ),
                ~exists(
                    select(ProjectCellOperation.id).where(
                        ProjectCellOperation.workspace_id == ProjectCellWorkspace.id,
                        ProjectCellOperation.kind == "release",
                        ProjectCellOperation.idempotency_key.like(
                            f"capacity:release:%:{requesting_run_id.hex[:12]}:3"
                        ),
                        ProjectCellOperation.status.in_(("failed", "cancelled")),
                    )
                ),
                ~exists(
                    select(ProjectCellOperation.id).where(
                        ProjectCellOperation.workspace_id == ProjectCellWorkspace.id,
                        ProjectCellOperation.status.in_(ACTIVE_OPERATION_STATUSES),
                        ~and_(
                            ProjectCellOperation.kind.in_(("release", "reconcile")),
                            ProjectCellOperation.generation_run_id
                            == ProjectCellWorkspace.generation_run_id,
                            ProjectCellOperation.status.in_(("pending", "waiting_capacity")),
                            or_(
                                ProjectCellOperation.idempotency_key.startswith(
                                    "capacity:release:"
                                ),
                                ProjectCellOperation.idempotency_key.startswith(
                                    "capacity:reconcile:"
                                ),
                                ProjectCellOperation.idempotency_key.startswith("cell-recovery:"),
                            ),
                        ),
                    )
                ),
                ~exists(
                    select(ProjectCellActivityLease.operation_id).where(
                        ProjectCellActivityLease.workspace_id == ProjectCellWorkspace.id,
                        ProjectCellActivityLease.state == "active",
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
    workspace_id: UUID | None = None,
    reclaim_for_repair: bool = False,
) -> bool:
    """Fence and release one terminal run without stopping ready compute."""

    async with session_factory() as cleanup_session:
        # A terminal run cannot consume an undispatched admission. Keep unknown
        # effects intact; row locks serialize this with a concurrent dispatcher.
        waiting_query = (
            select(ProjectCellOperation)
            .join(
                GenerationRun,
                GenerationRun.id == ProjectCellOperation.generation_run_id,
            )
            .where(
                GenerationRun.status.not_in(ACTIVE_GENERATION_STATUSES),
                ProjectCellOperation.kind == "ensure",
                ProjectCellOperation.status.in_(("pending", "waiting_capacity")),
            )
        )
        if workspace_id is not None:
            waiting_query = waiting_query.where(ProjectCellOperation.workspace_id == workspace_id)
        waiting_ensures = list(
            (
                await cleanup_session.scalars(
                    waiting_query.with_for_update(of=ProjectCellOperation, skip_locked=True).limit(
                        100
                    )
                )
            ).all()
        )
        for waiting_ensure in waiting_ensures:
            waiting_ensure.status = "cancelled"
            waiting_ensure.finished_at = datetime.now(UTC)
            waiting_ensure.next_attempt_at = None
            waiting_ensure.capacity_reason = None
        await cleanup_session.commit()
    async with session_factory() as session:
        claimed = await claim_stale_generation_lease(
            session,
            requesting_run_id=requesting_run_id,
            workspace_id=workspace_id,
        )
        if claimed is None:
            await session.rollback()
            return False
        workspace, stale_run_id = claimed
        # A deadline can interrupt ensure after resources have been created.
        # Observe at a higher fence; never turn uncertainty into cancellation.
        latest_completed_fence = (
            await session.scalar(
                select(ProjectCellOperation.fencing_epoch)
                .where(
                    ProjectCellOperation.workspace_id == workspace.id,
                    ProjectCellOperation.status == "completed",
                )
                .order_by(ProjectCellOperation.fencing_epoch.desc())
                .limit(1)
            )
            or 0
        )
        unknown = await session.scalar(
            select(ProjectCellOperation)
            .where(
                ProjectCellOperation.workspace_id == workspace.id,
                ProjectCellOperation.kind.in_(("ensure", "reconcile", "status")),
                ProjectCellOperation.status == "indeterminate",
                ProjectCellOperation.fencing_epoch > latest_completed_fence,
            )
            .order_by(ProjectCellOperation.fencing_epoch.desc())
            .limit(1)
        )
        ensure_anchor = await session.scalar(
            select(ProjectCellOperation)
            .where(
                ProjectCellOperation.workspace_id == workspace.id,
                ProjectCellOperation.generation_run_id == stale_run_id,
                ProjectCellOperation.kind == "ensure",
            )
            .order_by(ProjectCellOperation.created_at, ProjectCellOperation.id)
            .limit(1)
        )
        recovery_tail = await session.scalar(
            select(ProjectCellOperation)
            .where(
                ProjectCellOperation.workspace_id == workspace.id,
                ProjectCellOperation.generation_run_id == stale_run_id,
                ProjectCellOperation.kind.in_(("ensure", "reconcile")),
            )
            .order_by(
                case(
                    (ProjectCellOperation.status.in_(ACTIVE_OPERATION_STATUSES), 1),
                    else_=0,
                ).desc(),
                ProjectCellOperation.fencing_epoch.desc().nullslast(),
                ProjectCellOperation.created_at.desc(),
            )
            .limit(1)
        )
        needs_repair = (
            recovery_tail is not None
            and ensure_anchor is not None
            and (
                recovery_tail.status == "indeterminate"
                or (
                    recovery_tail.idempotency_key.startswith("cell-recovery:")
                    and recovery_tail.status != "completed"
                )
                or (
                    recovery_tail.kind == "reconcile"
                    and recovery_tail.status == "completed"
                    and (recovery_tail.result_payload or {}).get("state")
                    in {"partial", "degraded", "retained", "resources_paused"}
                )
            )
        )
        if unknown is not None or needs_repair:
            reconciliation = None
            if ensure_anchor is None or (unknown is not None and unknown.kind == "status"):
                assert unknown is not None
                reconciliation, _ = await reserve_cell_operation(
                    session,
                    workspace_id=workspace.id,
                    generation_run_id=stale_run_id,
                    kind="reconcile",
                    idempotency_key=f"capacity:reconcile:{unknown.id}:{requesting_run_id.hex[:12]}",
                    request={"indeterminate_operation_id": str(unknown.id)},
                )
            await session.commit()
            if reconciliation is not None:
                assert unknown is not None
                outcome = await reconcile_indeterminate_cell_operation(
                    session_factory,
                    unknown.id,
                    reconciliation.id,
                    client,
                )
            else:
                assert ensure_anchor is not None
                outcome = await recover_ensure_operation(session_factory, ensure_anchor.id, client)
            if outcome.status == "waiting_capacity":
                async with session_factory() as cancel_session:
                    await _cancel_waiting_operation(cancel_session, outcome.operation_id)
                if reclaim_for_repair:
                    # A same-project retry cannot reach normal admission until
                    # this old lease is reconciled. Reclaim only on a proven
                    # capacity rejection, never merely on a lost response.
                    await hibernate_one_idle_workspace(
                        session_factory,
                        requesting_run_id=requesting_run_id,
                        client=client,
                    )
            response = outcome.response
            if (
                outcome.status == "completed"
                and response is not None
                and response.state == "resources_ready"
            ):
                async with session_factory() as update_session:
                    locked = await update_session.scalar(
                        select(ProjectCellWorkspace)
                        .where(
                            ProjectCellWorkspace.id == workspace.id,
                        )
                        .with_for_update()
                    )
                    if (
                        locked is not None
                        and locked.generation_run_id == stale_run_id
                        and locked.fencing_epoch == response.fencing_epoch
                    ):
                        locked.state = "ready"
                        locked.provider_ref = response.provider_ref
                        await update_session.commit()
            elif outcome.status in {"waiting_capacity", "failed", "cancelled", "completed"} or (
                outcome.status == "indeterminate"
                and recovery_tail is not None
                and recovery_tail.kind == "reconcile"
                and recovery_tail.status == "indeterminate"
            ):
                async with session_factory() as cooldown_session:
                    deferred = await cooldown_session.get(
                        ProjectCellOperation,
                        outcome.operation_id,
                    )
                    if deferred is not None and deferred.status not in {"running", "pending"}:
                        deferred.next_attempt_at = datetime.now(UTC) + timedelta(seconds=30)
                        await cooldown_session.commit()
            # Recheck activity and terminal status on the next scheduler pass.
            return False
        latest_ensure_fence = (
            await session.scalar(
                select(ProjectCellOperation.fencing_epoch)
                .where(
                    ProjectCellOperation.workspace_id == workspace.id,
                    ProjectCellOperation.kind == "ensure",
                    ProjectCellOperation.status.not_in(("failed", "cancelled")),
                )
                .order_by(ProjectCellOperation.fencing_epoch.desc())
                .limit(1)
            )
            or 0
        )
        operation = await session.scalar(
            select(ProjectCellOperation)
            .where(
                ProjectCellOperation.workspace_id == workspace.id,
                ProjectCellOperation.generation_run_id == stale_run_id,
                ProjectCellOperation.kind == "release",
                ProjectCellOperation.status == "indeterminate",
                ProjectCellOperation.fencing_epoch > latest_ensure_fence,
            )
            .order_by(ProjectCellOperation.created_at.desc())
            .limit(1)
        )
        replay_indeterminate = operation is not None
        if operation is None:
            base_key = f"capacity:release:{workspace.id}:{stale_run_id}"
            prior = list(
                (
                    await session.scalars(
                        select(ProjectCellOperation)
                        .where(
                            ProjectCellOperation.workspace_id == workspace.id,
                            ProjectCellOperation.idempotency_key.startswith(base_key),
                        )
                        .order_by(ProjectCellOperation.created_at.desc())
                    )
                ).all()
            )
            operation = prior[0] if prior else None
            retry_prefix = f"{base_key}:{requesting_run_id.hex[:12]}:"
            retry_count = sum(item.idempotency_key.startswith(retry_prefix) for item in prior)
            if operation is not None and (
                operation.status in {"failed", "cancelled"}
                or (
                    operation.status not in {"pending", "waiting_capacity"}
                    and (operation.fencing_epoch or 0) <= latest_ensure_fence
                )
            ):
                if retry_count >= 3:
                    await session.rollback()
                    return False
                operation = None
            if operation is None:
                operation, _ = await reserve_cell_operation(
                    session,
                    workspace_id=workspace.id,
                    generation_run_id=stale_run_id,
                    kind="release",
                    idempotency_key=(f"{retry_prefix}{retry_count + 1}" if prior else base_key),
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
        # Victim-wide durable cooldown: the next requester must not spend its
        # whole budget retrying the same unavailable oldest cell.
        if outcome.status in {"failed", "cancelled", "indeterminate"}:
            async with session_factory() as retry_session:
                failed_operation = await retry_session.get(
                    ProjectCellOperation,
                    outcome.operation_id,
                )
                if failed_operation is not None and failed_operation.status == outcome.status:
                    failed_operation.next_attempt_at = datetime.now(UTC) + timedelta(seconds=30)
                    await retry_session.commit()
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
                ~and_(
                    ProjectCellOperation.kind.in_(("status", "reconcile")),
                    ProjectCellOperation.status == "completed",
                ),
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
        locked_workspace.state = "ready"
        locked_workspace.provider_ref = response.provider_ref
        locked_workspace.updated_at = datetime.now(UTC)
        await session.commit()
    return True


async def hibernate_one_idle_workspace(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    requesting_run_id: UUID,
    client: ProjectCellOrchestratorClient,
    expected_workspace_id: UUID | None = None,
) -> bool:
    await release_one_stale_generation_lease(
        session_factory,
        requesting_run_id=requesting_run_id,
        client=client,
        workspace_id=expected_workspace_id,
    )
    async with session_factory() as session:
        victim = await claim_idle_hibernation_victim(
            session,
            requesting_run_id=requesting_run_id,
            expected_workspace_id=expected_workspace_id,
        )
        if victim is None:
            await session.rollback()
            return False
        checkpoint_ref = f"capacity-{requesting_run_id.hex[:12]}"
        idempotency_base = f"capacity:{requesting_run_id}:pause:{victim.id}"
        prior_operations = list(
            (
                await session.execute(
                    select(ProjectCellOperation)
                    .where(
                        ProjectCellOperation.workspace_id == victim.id,
                        ProjectCellOperation.generation_run_id.is_(None),
                        ProjectCellOperation.kind == "pause",
                        ProjectCellOperation.idempotency_key.like(f"{idempotency_base}%"),
                    )
                    .order_by(ProjectCellOperation.created_at.desc())
                )
            )
            .scalars()
            .all()
        )
        # Adopt an interrupted pause, keeping its original checkpoint/envelope.
        operation = await session.scalar(
            select(ProjectCellOperation)
            .where(
                ProjectCellOperation.workspace_id == victim.id,
                ProjectCellOperation.generation_run_id.is_(None),
                ProjectCellOperation.kind == "pause",
                ProjectCellOperation.status.in_(("pending", "waiting_capacity")),
                ProjectCellOperation.idempotency_key.like("capacity:%:pause:%"),
            )
            .order_by(ProjectCellOperation.created_at)
            .limit(1)
        )
        if operation is None:
            operation = prior_operations[0] if prior_operations else None
        replay_indeterminate = operation is not None and operation.status == "indeterminate"
        if operation is None or operation.status in {"failed", "cancelled"}:
            idempotency_key = (
                idempotency_base
                if not prior_operations
                else f"{idempotency_base}:{len(prior_operations) + 1}"
            )
            operation, _ = await reserve_cell_operation(
                session,
                workspace_id=victim.id,
                generation_run_id=None,
                kind="pause",
                idempotency_key=idempotency_key,
                request={"checkpoint_ref": checkpoint_ref},
            )
            replay_indeterminate = False
        victim_id = victim.id
        await session.commit()

    if not await _hibernate_victim_still_idle(
        session_factory,
        workspace_id=victim_id,
        pause_operation_id=operation.id,
        client=client,
    ):
        return False

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
        or response.workspace_id != victim_id
        or response.state != "resources_paused"
        or response.fencing_epoch is None
    ):
        return False
    async with session_factory() as session:
        await _scheduler_lock(session)
        workspace = await session.scalar(
            select(ProjectCellWorkspace)
            .where(ProjectCellWorkspace.id == victim_id)
            .with_for_update()
        )
        later_effect_may_be_unknown = await session.scalar(
            select(ProjectCellOperation.id)
            .where(
                ProjectCellOperation.workspace_id == victim_id,
                ProjectCellOperation.fencing_epoch > response.fencing_epoch,
                ProjectCellOperation.status.not_in(("failed", "cancelled")),
            )
            .limit(1)
        )
        if (
            workspace is None
            or workspace.generation_run_id is not None
            or workspace.fencing_epoch < response.fencing_epoch
            or later_effect_may_be_unknown is not None
        ):
            await session.rollback()
            return False
        workspace.state = "stopped"
        workspace.updated_at = datetime.now(UTC)
        await session.commit()
    return True


async def _hibernate_victim_still_idle(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    workspace_id: UUID,
    pause_operation_id: UUID,
    client: ProjectCellOrchestratorClient,
) -> bool:
    """Recheck durable activity after selection and before the pause side effect."""
    async with session_factory() as session:
        workspace = await session.scalar(
            select(ProjectCellWorkspace)
            .where(ProjectCellWorkspace.id == workspace_id)
            .with_for_update()
        )
        active_activity = await session.scalar(
            select(ProjectCellActivityLease)
            .where(
                ProjectCellActivityLease.workspace_id == workspace_id,
                ProjectCellActivityLease.state == "active",
            )
            .order_by(ProjectCellActivityLease.started_at.desc())
            .limit(1)
        )
        if active_activity is not None:
            status_method = getattr(client, "agent_operation_status", None)
            if not callable(status_method):
                await session.rollback()
                return False
            try:
                status = await status_method(workspace_id, active_activity.operation_id)
            except Exception:
                await session.rollback()
                return False
            if status.state in {"starting", "running"}:
                await session.rollback()
                return False
            active_activity.state = (
                "completed"
                if status.state == "completed"
                else "timed_out"
                if status.state == "timed_out"
                else "cancelled"
                if status.state == "cancelled"
                else "failed"
            )
            active_activity.finished_at = max(
                status.heartbeat_at,
                active_activity.heartbeat_at,
            )
            active_activity.heartbeat_at = active_activity.finished_at
            active_activity.log_bytes = max(active_activity.log_bytes, status.log_bytes)
            await session.flush()
            active_activity = None
        other_operation = await session.scalar(
            select(ProjectCellOperation.id)
            .where(
                ProjectCellOperation.workspace_id == workspace_id,
                ProjectCellOperation.id != pause_operation_id,
                ProjectCellOperation.status.in_(ACTIVE_OPERATION_STATUSES),
            )
            .limit(1)
        )
        if (
            workspace is not None
            and workspace.state == "ready"
            and workspace.generation_run_id is None
            and active_activity is None
            and other_operation is None
        ):
            # Preserve any terminal activity reconciliation performed above;
            # otherwise the partial unique index keeps a ghost active lease.
            await session.commit()
            return True
        operation = await session.get(ProjectCellOperation, pause_operation_id)
        if operation is not None and operation.status in {"pending", "waiting_capacity"}:
            operation.status = "cancelled"
            operation.finished_at = datetime.now(UTC)
            operation.error = "hibernation vetoed by active workspace work"
            await session.commit()
        else:
            await session.rollback()
        return False


async def wait_for_capacity(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    run_id: UUID,
    operation_id: UUID,
    client: ProjectCellOrchestratorClient,
    emit: Callable[[dict[str, object]], Awaitable[None]],
    dispatch_token: UUID | None = None,
    initial_attempt: Callable[[], Awaitable[ProjectCellOperationOutcome]] | None = None,
) -> ProjectCellOperationOutcome:
    """Bound the entire queue, including slow reclamation/provider calls."""
    async with session_factory() as session:
        run = await session.get(GenerationRun, run_id)
        if run is None:
            raise RuntimeError("queued generation run disappeared")
        if run.status == "cancel_requested" or run.status not in ACTIVE_GENERATION_STATUSES:
            return await _cancel_waiting_operation(session, operation_id)
        remaining = (
            get_settings().project_cell_capacity_wait_seconds
            - (datetime.now(UTC) - run.created_at).total_seconds()
        )
    try:
        async with asyncio.timeout(max(0, remaining)):
            if initial_attempt is not None:
                initial = await initial_attempt()
                if initial.status not in {"waiting_capacity", "indeterminate", "running"}:
                    return initial
            return await _wait_for_capacity(
                session_factory,
                run_id=run_id,
                operation_id=operation_id,
                client=client,
                emit=emit,
                dispatch_token=dispatch_token,
            )
    except TimeoutError:
        async with session_factory() as session:
            # Recovery can replace the original ensure with a new durable one.
            # Cancel every undispatched admission for this run, not only its first ID.
            pending = list(
                (
                    await session.scalars(
                        select(ProjectCellOperation)
                        .where(
                            ProjectCellOperation.generation_run_id == run_id,
                            ProjectCellOperation.kind.in_(("ensure", "reconcile")),
                            ProjectCellOperation.status.in_(("pending", "waiting_capacity")),
                        )
                        .with_for_update()
                    )
                ).all()
            )
            for item in pending:
                item.status = "cancelled"
                item.finished_at = datetime.now(UTC)
                item.next_attempt_at = None
                item.capacity_reason = None
            await session.commit()
            await _cancel_waiting_operation(session, operation_id)
        raise TimeoutError("Project Cell capacity queue deadline exceeded") from None


async def _wait_for_capacity(
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
    ensure_anchor_id = operation_id
    while True:
        async with session_factory() as session:
            run = await session.scalar(
                select(GenerationRun).where(GenerationRun.id == run_id).with_for_update()
            )
            if run is None:
                raise RuntimeError("queued generation run disappeared")
            if run.status == "cancel_requested" or run.status not in ACTIVE_GENERATION_STATUSES:
                return await _cancel_waiting_operation(session, operation_id)
            if (datetime.now(UTC) - run.created_at).total_seconds() >= (
                get_settings().project_cell_capacity_wait_seconds
            ):
                # Starts at durable admission, before runtime bootstrap/watchdog.
                # Never mark an unknown controller effect as a cancelled effect.
                await _cancel_waiting_operation(session, operation_id)
                raise TimeoutError("Project Cell capacity queue deadline exceeded")
            run.status = "queued_for_capacity"
            if dispatch_token is not None:
                write_capacity_dispatch_claim(
                    run,
                    token=dispatch_token,
                    lease_seconds=30,
                )
            turn = await claim_capacity_turn(session, run_id)
            operation_status = await session.scalar(
                select(ProjectCellOperation.status).where(
                    ProjectCellOperation.id == operation_id,
                )
            )
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
        if operation_status == "indeterminate":
            outcome = await recover_ensure_operation(
                session_factory,
                ensure_anchor_id,
                client,
            )
        else:
            outcome = await execute_cell_operation(session_factory, operation_id, client)
        operation_id = outcome.operation_id
        if outcome.status == "waiting_capacity":
            async with session_factory() as session:
                operation = await session.get(ProjectCellOperation, operation_id)
                delay = 1
                if operation is not None and operation.next_attempt_at is not None:
                    delay = max(
                        1,
                        min(
                            10,
                            int((operation.next_attempt_at - datetime.now(UTC)).total_seconds()),
                        ),
                    )
            await asyncio.sleep(delay)
            continue
        if outcome.status in {"indeterminate", "running"}:
            await asyncio.sleep(1)
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
