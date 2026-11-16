"""Confirm an interrupted Project Cell release without starting generation work."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from yleum_api.models.generation_run import GenerationRun
from yleum_api.models.project_cell import (
    ProjectCellActivityLease,
    ProjectCellOperation,
    ProjectCellWorkspace,
)
from yleum_api.services.generation import recovery_execution
from yleum_api.services.generation.recovery_clock import next_dependency_check
from yleum_api.services.generation.recovery_contract import RecoveryState, RecoveryStateV2
from yleum_api.services.generation.recovery_execution import (
    RECOVERY_KEY,
    _owned_release_receipt,
    recovery_state,
)
from yleum_api.services.generation.recovery_store import _locked_owner
from yleum_api.services.generation_deadline import is_restoration_adaptation
from yleum_api.services.orchestrator_client import HttpProjectCellOrchestratorClient
from yleum_api.services.project_cell_lifecycle import (
    execute_cell_operation,
    replay_indeterminate_cell_operation,
)
from yleum_api.services.project_cells import (
    ACTIVE_OPERATION_STATUSES,
    ProjectCellValidationError,
    _hashed_error,
    _stored_request_payload,
    lock_workspace_operations,
    mark_cell_operation_indeterminate,
)

NONRETRYABLE_RELEASE_ERRORS = frozenset(_hashed_error(reason) for reason in (
    "recovery_release_source_changed",
    "recovery_release_manifest_changed",
    "recovery_release_environment_changed",
))


def pending_recovery_release(run: GenerationRun, state: RecoveryState | None) -> bool:
    return (
        isinstance(state, RecoveryStateV2)
        and state.binding is not None
        and state.execution.physical_state == "release_pending"
        and not is_restoration_adaptation(run)
    )


async def _locked_release(
    session: AsyncSession, run_id: UUID, state: RecoveryStateV2,
    *, confirmed_agent_build: bool = False, reading_agent_build: bool = False,
) -> tuple[GenerationRun, ProjectCellWorkspace, ProjectCellOperation] | None:
    execution, binding = state.execution, state.binding
    workspace_id, prior_fence = execution.control_workspace_id, execution.control_fencing_epoch
    if (
        workspace_id is None or prior_fence is None or execution.release_operation_id is None
        or binding is None or binding.workspace_id != str(workspace_id)
        or prior_fence not in {binding.fencing_epoch, binding.fencing_epoch + 1}
    ):
        return None
    await lock_workspace_operations(session, workspace_id)
    identity = state.identity
    run, project = await _locked_owner(
        session, run_id=run_id, project_id=identity.project_id, user_id=identity.user_id,
    )
    if (
        recovery_state(run) != state or not pending_recovery_release(run, state)
        or project.template != "max_miniapp" or run.execution_backend != "worker"
    ):
        return None
    workspace = await session.get(
        ProjectCellWorkspace, workspace_id, with_for_update=True, populate_existing=True,
    )
    operation = await session.get(
        ProjectCellOperation, execution.release_operation_id,
        with_for_update=True, populate_existing=True,
    )
    if (
        workspace is None or operation is None or workspace.project_id != run.project_id
        or workspace.owner_id != run.user_id or workspace.deleted_at is not None
        or workspace.state != "ready"
        or operation.workspace_id != workspace.id or operation.generation_run_id != run.id
        or operation.kind != "release"
        or not operation.idempotency_key.startswith(f"generation:{run.id}:release:")
        or not operation.idempotency_key.endswith(f":cycle:{execution.lease_cycle_seq}")
    ):
        return None
    try:
        request = _stored_request_payload(operation)
    except ProjectCellValidationError:
        return None
    strict = request == {"require_environment_proof": {
        "source_sha256": binding.source_sha256, "environment_sha256": binding.environment_sha256,
    }}
    owned_request = strict or ((confirmed_agent_build or reading_agent_build) and request == {})
    if operation.status == "pending":
        if (
            not owned_request or workspace.generation_run_id != run.id
            or prior_fence != binding.fencing_epoch or workspace.fencing_epoch != prior_fence
            or operation.fencing_epoch is not None or operation.execution_run_id is not None
        ):
            return None
    elif (
        workspace.fencing_epoch != binding.fencing_epoch + 1
        or operation.fencing_epoch != workspace.fencing_epoch
    ):
        return None
    if operation.status == "completed":
        if (
            (not strict and request != {}) or workspace.generation_run_id not in {None, run.id}
            or not _owned_release_receipt(
                operation, run_id=run.id, workspace_id=workspace.id, fence=workspace.fencing_epoch,
            )
        ):
            return None
    elif operation.status != "pending" and (
        operation.status not in {"running", "indeterminate"} or not owned_request
        or workspace.generation_run_id != run.id or operation.execution_run_id != run.id
        or operation.error in NONRETRYABLE_RELEASE_ERRORS
    ):
        return None
    other_operation = select(ProjectCellOperation.id).where(
        ProjectCellOperation.workspace_id == workspace.id,
        ProjectCellOperation.id != operation.id,
        ProjectCellOperation.status.in_(ACTIVE_OPERATION_STATUSES),
    ).limit(1)
    activity = select(ProjectCellActivityLease.operation_id).where(
        ProjectCellActivityLease.workspace_id == workspace.id,
        ProjectCellActivityLease.state == "active",
    ).limit(1)
    if (
        await session.scalar(other_operation) is not None
        or (not reading_agent_build and await session.scalar(activity) is not None)
    ):
        return None
    return run, workspace, operation


def _record_release(
    run: GenerationRun, state: RecoveryStateV2, workspace: ProjectCellWorkspace,
) -> None:
    workspace.generation_run_id = None
    workspace.updated_at = datetime.now(UTC)
    released = state.model_copy(update={
        "execution": state.execution.model_copy(update={
            "physical_state": "released", "control_fencing_epoch": workspace.fencing_epoch,
        }),
        "state_revision": state.state_revision + 1,
    })
    run.agent_state = {**run.agent_state, RECOVERY_KEY: released.model_dump(mode="json")}


async def resume_recovery_release(
    factory: async_sessionmaker[AsyncSession], run_id: UUID, state: RecoveryStateV2,
) -> None:
    from yleum_api.services.project_cell_agent_release_command import confirm_agent_build_release

    await recovery_execution._require_session_owner(run_id)
    confirmed_agent_build = await confirm_agent_build_release(factory, run_id, state)
    async with factory() as session:
        owned = await _locked_release(
            session, run_id, state, confirmed_agent_build=confirmed_agent_build,
        )
        if owned is None:
            return
        run, workspace, operation = owned
        if operation.status == "completed":
            _record_release(run, state, workspace)
            await session.commit()
            return
        if operation.next_attempt_at is not None and operation.next_attempt_at > datetime.now(UTC):
            return
        if operation.status == "running":
            # Our worker lock proves interruption of the local dispatcher only.
            await mark_cell_operation_indeterminate(
                session, operation.id, "generation_executor_interrupted",
            )
        operation_id, fence = operation.id, workspace.fencing_epoch
        dispatch = execute_cell_operation if operation.status == "pending" else (
            replay_indeterminate_cell_operation
        )
        await session.commit()
    await dispatch(
        factory, operation_id, HttpProjectCellOrchestratorClient(), expected_workspace_fence=fence,
    )
    await recovery_execution._require_session_owner(run_id)
    async with factory() as session:
        owned = await _locked_release(
            session, run_id, state, confirmed_agent_build=confirmed_agent_build,
        )
        if owned is None:
            return
        run, workspace, operation = owned
        if operation.status == "completed":
            _record_release(run, state, workspace)
        elif operation.status == "indeterminate":
            operation.next_attempt_at = next_dependency_check(
                run_id, failures=max(1, operation.attempt_count), now=datetime.now(UTC),
            )
        await session.commit()
