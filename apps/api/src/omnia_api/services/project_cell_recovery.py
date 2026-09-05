"""Bounded recovery for uncertain Project Cell ensure operations."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import case, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from omnia_api.core.config import get_settings
from omnia_api.models.project_cell import ProjectCellOperation, ProjectCellWorkspace
from omnia_api.services.orchestrator_client import ProjectCellOrchestratorClient
from omnia_api.services.project_cell_lifecycle import (
    ProjectCellOperationOutcome,
    _load_operation_outcome,
    execute_cell_operation,
    reconcile_indeterminate_cell_operation,
)
from omnia_api.services.project_cells import (
    ACTIVE_OPERATION_STATUSES,
    ProjectCellNotFound,
    ProjectCellStateConflict,
    ProjectCellValidationError,
    _advisory_lock,
    _stored_request_payload,
    reserve_cell_operation,
    resolve_workspace_profile,
)

_CHAIN_KINDS = ("ensure", "reconcile")
_REPAIRABLE_STATES = frozenset({"partial", "degraded", "retained", "resources_paused"})
_MAX_CHAIN_DEPTH = 32


@dataclass(frozen=True, slots=True)
class _RecoveryContext:
    workspace_id: UUID
    generation_run_id: UUID


@dataclass(frozen=True, slots=True)
class _ReservationDecision:
    operation_id: UUID
    should_dispatch: bool


async def recover_ensure_operation(
    session_factory: async_sessionmaker[AsyncSession],
    operation_id: UUID,
    client: ProjectCellOrchestratorClient,
) -> ProjectCellOperationOutcome:
    """Advance one durable ensure/reconcile chain by at most two remote effects."""

    async with session_factory() as session:
        requested = await _require_operation(session, operation_id)
        context = await _resolve_context(session, requested)
        chain = await _load_chain(session, context)
    latest = chain[0]

    if latest.status in {"waiting_capacity", "running"}:
        return await _load_operation_outcome(session_factory, latest.id)
    if latest.status == "pending":
        outcome = await _dispatch_pending_recovery(
            session_factory,
            latest,
            client,
        )
        return await _repair_after_observation(
            session_factory,
            context,
            outcome,
            client,
        )
    if latest.status == "indeterminate":
        reconciliation = await _reserve_reconciliation(
            session_factory,
            context,
            expected_tail=latest,
            target=latest,
        )
        if not reconciliation.should_dispatch:
            return await _load_operation_outcome(
                session_factory,
                reconciliation.operation_id,
            )
        outcome = await reconcile_indeterminate_cell_operation(
            session_factory,
            latest.id,
            reconciliation.operation_id,
            client,
        )
        return await _repair_after_observation(
            session_factory,
            context,
            outcome,
            client,
        )
    if latest.status == "completed":
        outcome = await _load_operation_outcome(session_factory, latest.id)
        return await _repair_after_observation(
            session_factory,
            context,
            outcome,
            client,
        )
    if latest.status in {"failed", "cancelled"}:
        retry = await _retry_after_terminal_recovery(
            session_factory,
            context,
            latest,
            client,
        )
        if retry is not None:
            return retry
    return await _load_operation_outcome(session_factory, latest.id)


async def _dispatch_pending_recovery(
    session_factory: async_sessionmaker[AsyncSession],
    operation: ProjectCellOperation,
    client: ProjectCellOrchestratorClient,
) -> ProjectCellOperationOutcome:
    if operation.kind == "ensure":
        return await execute_cell_operation(session_factory, operation.id, client)
    if operation.kind == "reconcile":
        return await reconcile_indeterminate_cell_operation(
            session_factory,
            _reconcile_target_id(operation),
            operation.id,
            client,
        )
    raise ProjectCellValidationError("recovery operation kind is unsupported")


async def _repair_after_observation(
    session_factory: async_sessionmaker[AsyncSession],
    context: _RecoveryContext,
    outcome: ProjectCellOperationOutcome,
    client: ProjectCellOrchestratorClient,
) -> ProjectCellOperationOutcome:
    response = outcome.response
    if (
        outcome.status != "completed"
        or response is None
        or response.state == "resources_ready"
        or outcome.kind != "reconcile"
        or response.state not in _REPAIRABLE_STATES
    ):
        return outcome
    repair = await _reserve_repair_ensure(
        session_factory,
        context,
        expected_tail_id=outcome.operation_id,
        observation_id=outcome.operation_id,
    )
    if not repair.should_dispatch:
        return await _load_operation_outcome(session_factory, repair.operation_id)
    return await execute_cell_operation(session_factory, repair.operation_id, client)


async def _retry_after_terminal_recovery(
    session_factory: async_sessionmaker[AsyncSession],
    context: _RecoveryContext,
    latest: ProjectCellOperation,
    client: ProjectCellOrchestratorClient,
) -> ProjectCellOperationOutcome | None:
    if latest.kind == "reconcile":
        async with session_factory() as session:
            target = await _require_operation(session, _reconcile_target_id(latest))
        if target.status != "indeterminate":
            return None
        reconciliation = await _reserve_reconciliation(
            session_factory,
            context,
            expected_tail=latest,
            target=target,
        )
        if not reconciliation.should_dispatch:
            return await _load_operation_outcome(
                session_factory,
                reconciliation.operation_id,
            )
        outcome = await reconcile_indeterminate_cell_operation(
            session_factory,
            target.id,
            reconciliation.operation_id,
            client,
        )
        return await _repair_after_observation(
            session_factory,
            context,
            outcome,
            client,
        )
    if latest.kind != "ensure":
        return None
    observation = await _latest_completed_observation(session_factory, context)
    if observation is None:
        return None
    response = observation.response
    if response is None or response.state not in _REPAIRABLE_STATES:
        return None
    repair = await _reserve_repair_ensure(
        session_factory,
        context,
        expected_tail_id=latest.id,
        observation_id=observation.operation_id,
    )
    if not repair.should_dispatch:
        return await _load_operation_outcome(session_factory, repair.operation_id)
    return await execute_cell_operation(session_factory, repair.operation_id, client)


async def _latest_completed_observation(
    session_factory: async_sessionmaker[AsyncSession],
    context: _RecoveryContext,
) -> ProjectCellOperationOutcome | None:
    async with session_factory() as session:
        operation_id = await session.scalar(
            select(ProjectCellOperation.id)
            .where(
                ProjectCellOperation.workspace_id == context.workspace_id,
                ProjectCellOperation.generation_run_id == context.generation_run_id,
                ProjectCellOperation.kind == "reconcile",
                ProjectCellOperation.status == "completed",
            )
            .order_by(
                ProjectCellOperation.fencing_epoch.desc().nullslast(),
                ProjectCellOperation.created_at.desc(),
                ProjectCellOperation.id.desc(),
            )
            .limit(1)
        )
    if operation_id is None:
        return None
    return await _load_operation_outcome(session_factory, operation_id)


async def _reserve_reconciliation(
    session_factory: async_sessionmaker[AsyncSession],
    context: _RecoveryContext,
    *,
    expected_tail: ProjectCellOperation,
    target: ProjectCellOperation,
) -> _ReservationDecision:
    prefix = f"cell-recovery:reconcile:{target.id}"
    async with session_factory() as session:
        current = await _lock_and_validate_tail(
            session,
            context,
        )
        if current.id != expected_tail.id:
            await session.rollback()
            return _ReservationDecision(current.id, False)
        locked_target = await _require_operation(session, target.id)
        if locked_target.status != "indeterminate":
            await session.rollback()
            return _ReservationDecision(current.id, False)
        attempt = await _next_attempt(session, context.workspace_id, prefix)
        operation, _ = await reserve_cell_operation(
            session,
            workspace_id=context.workspace_id,
            generation_run_id=context.generation_run_id,
            kind="reconcile",
            idempotency_key=f"{prefix}:{attempt}",
            request={"indeterminate_operation_id": str(target.id)},
        )
        await session.commit()
        return _ReservationDecision(operation.id, True)


async def _reserve_repair_ensure(
    session_factory: async_sessionmaker[AsyncSession],
    context: _RecoveryContext,
    *,
    expected_tail_id: UUID,
    observation_id: UUID,
) -> _ReservationDecision:
    prefix = f"cell-recovery:repair:{context.workspace_id}:{context.generation_run_id}"
    async with session_factory() as session:
        current = await _lock_and_validate_tail(
            session,
            context,
        )
        if current.id != expected_tail_id:
            await session.rollback()
            return _ReservationDecision(current.id, False)
        observation = await _require_operation(session, observation_id)
        if (
            observation.workspace_id != context.workspace_id
            or observation.generation_run_id != context.generation_run_id
            or observation.kind != "reconcile"
            or observation.status != "completed"
        ):
            raise ProjectCellStateConflict("repair observation is no longer recoverable")
        result = observation.result_payload
        if type(result) is not dict or result.get("state") not in _REPAIRABLE_STATES:
            raise ProjectCellStateConflict("repair observation is not recoverable")
        workspace = await _require_workspace(session, context.workspace_id)
        default_profile = (
            "docker-owner-cell-resources-v2"
            if get_settings().use_cell_resource_profile_v2
            else "docker-owner-cell-resources-v1"
        )
        profile = await resolve_workspace_profile(session, workspace, default_profile)
        attempt = await _next_attempt(session, context.workspace_id, prefix)
        operation, _ = await reserve_cell_operation(
            session,
            workspace_id=context.workspace_id,
            generation_run_id=context.generation_run_id,
            kind="ensure",
            idempotency_key=f"{prefix}:{attempt}",
            request={"profile_version": profile},
        )
        await session.commit()
        return _ReservationDecision(operation.id, True)


async def _lock_and_validate_tail(
    session: AsyncSession,
    context: _RecoveryContext,
) -> ProjectCellOperation:
    await _advisory_lock(session, context.workspace_id)
    workspace = await _require_workspace(session, context.workspace_id)
    if workspace.generation_run_id != context.generation_run_id:
        raise ProjectCellStateConflict("Project Cell generation lease changed during recovery")
    current = (await _load_chain(session, context))[0]
    return current


async def _require_workspace(
    session: AsyncSession,
    workspace_id: UUID,
) -> ProjectCellWorkspace:
    workspace = await session.scalar(
        select(ProjectCellWorkspace)
        .where(ProjectCellWorkspace.id == workspace_id)
        .with_for_update()
    )
    if workspace is None:
        raise ProjectCellNotFound("Project Cell workspace was not found")
    return workspace


async def _next_attempt(
    session: AsyncSession,
    workspace_id: UUID,
    prefix: str,
) -> int:
    operation_ids = await session.scalars(
        select(ProjectCellOperation.id).where(
            ProjectCellOperation.workspace_id == workspace_id,
            ProjectCellOperation.idempotency_key.startswith(f"{prefix}:"),
        )
    )
    return len(tuple(operation_ids)) + 1


async def _load_chain(
    session: AsyncSession,
    context: _RecoveryContext,
) -> tuple[ProjectCellOperation, ...]:
    active_first = case(
        (ProjectCellOperation.status.in_(ACTIVE_OPERATION_STATUSES), 1),
        else_=0,
    )
    operations = tuple(
        await session.scalars(
            select(ProjectCellOperation)
            .where(
                ProjectCellOperation.workspace_id == context.workspace_id,
                ProjectCellOperation.generation_run_id == context.generation_run_id,
                ProjectCellOperation.kind.in_(_CHAIN_KINDS),
            )
            .order_by(
                active_first.desc(),
                ProjectCellOperation.fencing_epoch.desc().nullslast(),
                ProjectCellOperation.created_at.desc(),
                ProjectCellOperation.id.desc(),
            )
        )
    )
    if not operations:
        raise ProjectCellStateConflict("ensure recovery chain is empty")
    return operations


async def _resolve_context(
    session: AsyncSession,
    requested: ProjectCellOperation,
) -> _RecoveryContext:
    current = requested
    workspace_id = requested.workspace_id
    generation_run_id: UUID | None = None
    seen: set[UUID] = set()
    for _ in range(_MAX_CHAIN_DEPTH):
        if current.id in seen:
            raise ProjectCellStateConflict("ensure recovery chain contains a cycle")
        seen.add(current.id)
        if current.kind not in _CHAIN_KINDS:
            raise ProjectCellValidationError("operation is not an ensure recovery operation")
        if current.generation_run_id is not None:
            if generation_run_id is not None and generation_run_id != current.generation_run_id:
                raise ProjectCellStateConflict("ensure recovery chain changes generation run")
            generation_run_id = current.generation_run_id
        if current.kind == "ensure":
            break
        if current.kind != "reconcile":
            raise ProjectCellValidationError("operation is not an ensure recovery operation")
        target = await _require_operation(session, _reconcile_target_id(current))
        if target.workspace_id != workspace_id:
            raise ProjectCellStateConflict("ensure recovery chain changes workspace")
        current = target
    else:
        raise ProjectCellStateConflict("ensure recovery chain is too deep")
    if generation_run_id is None:
        raise ProjectCellStateConflict("ensure recovery chain has no generation run")
    return _RecoveryContext(workspace_id, generation_run_id)


async def _require_operation(
    session: AsyncSession,
    operation_id: UUID,
) -> ProjectCellOperation:
    operation = await session.get(ProjectCellOperation, operation_id)
    if operation is None:
        raise ProjectCellNotFound("Project Cell operation was not found")
    return operation


def _reconcile_target_id(operation: ProjectCellOperation) -> UUID:
    if operation.kind != "reconcile":
        raise ProjectCellValidationError("operation is not a reconciliation")
    request = _stored_request_payload(operation)
    raw_target = request.get("indeterminate_operation_id")
    if type(raw_target) is not str:
        raise ProjectCellValidationError("reconciliation target is invalid")
    try:
        return UUID(raw_target)
    except ValueError as exc:
        raise ProjectCellValidationError("reconciliation target is invalid") from exc
