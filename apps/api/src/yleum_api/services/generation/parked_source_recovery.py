"""Recognize one known parked repair without authorizing new product effects."""

from __future__ import annotations

import asyncio
import json

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from yleum_api.models.generation_recovery_effect import GenerationRecoveryEffect
from yleum_api.models.generation_run import GenerationRun
from yleum_api.models.project import Project
from yleum_api.models.project_cell import (
    ProjectCellActivityLease,
    ProjectCellOperation,
    ProjectCellWorkspace,
)
from yleum_api.models.provider_call import ProviderCall
from yleum_api.models.snapshot import Snapshot
from yleum_api.services.generation import source_checkpoint
from yleum_api.services.generation.recovery_contract import RecoveryStateV2, RecoveryStateV3
from yleum_api.services.max_management_generation import (
    PARKED_THROW_DIAGNOSTIC_PREFIX,
    matches_parked_throw_diagnostic,
)

PARKED_SOURCE_REDRIVE_KEY = "parked_source_redrive_v1"


async def _only_cancelled_preparation(
    session: AsyncSession, run: GenerationRun, state: RecoveryStateV3,
    workspace: ProjectCellWorkspace, release: ProjectCellOperation,
) -> bool:
    """Verify claim-only progress and absence of dispatch since the owned release."""
    from yleum_api.services.project_cells import _stored_request_payload

    marker = run.agent_state.get(PARKED_SOURCE_REDRIVE_KEY)
    claim = state.continuation_claim
    prefix = f"generation:{run.id}:release:"
    stop = release.finished_at
    if (
        type(marker) is not int or marker <= 0 or claim is None or claim.run_id != run.id
        or state.next_check_at is not None or claim.attempt_seq != state.attempt_seq
        or stop is None or not release.idempotency_key.startswith(prefix)
    ):
        return False
    release_key = release.idempotency_key.removeprefix(prefix)
    release_profile, separator, initial = release_key.rpartition(":cycle:")
    if (
        not release_profile or not separator or not initial.isascii()
        or not initial.isdecimal() or len(initial) > 12
    ):
        return False
    original_cycle = int(initial)
    cycle = state.execution.lease_cycle_seq
    additional_claims = cycle - original_cycle
    original_attempt = state.attempt_seq - additional_claims
    if (
        additional_claims <= 0 or original_attempt < 0
        or state.state_revision - marker != additional_claims
    ):
        return False
    effect = await session.scalar(select(GenerationRecoveryEffect.effect_id).where(
        GenerationRecoveryEffect.run_id == run.id,
        GenerationRecoveryEffect.attempt_seq > original_attempt,
    ).limit(1))
    call = await session.scalar(select(ProviderCall.id).where(
        ProviderCall.run_id == run.id, ProviderCall.created_at >= stop,
    ).limit(1))
    activity = await session.scalar(select(ProjectCellActivityLease.operation_id).where(
        ProjectCellActivityLease.workspace_id == workspace.id,
        ProjectCellActivityLease.started_at >= stop,
    ).limit(1))
    if effect is not None or call is not None or activity is not None:
        return False
    operations = (await session.scalars(select(ProjectCellOperation).where(
        ProjectCellOperation.created_at >= stop,
        or_(
            ProjectCellOperation.workspace_id == workspace.id,
            ProjectCellOperation.generation_run_id == run.id,
            ProjectCellOperation.execution_run_id == run.id,
        ),
    ))).all()
    abandoned: set[int] = set()
    for operation in operations:
        if (
            operation.kind != "ensure" or operation.status != "cancelled"
            or operation.workspace_id != workspace.id or operation.generation_run_id != run.id
            or operation.attempt_count != 0 or operation.started_at is not None
            or operation.execution_run_id is not None or operation.fencing_epoch is not None
            or operation.result_payload is not None or operation.error is not None
            or operation.next_attempt_at is not None or operation.capacity_reason is not None
            or operation.finished_at is None
        ):
            return False
        request = _stored_request_payload(operation)
        profile = request.get("profile_version")
        if type(profile) is not str or set(request) != {"profile_version"}:
            return False
        stem = f"generation:{run.id}:ensure:{profile}:cycle:"
        raw = operation.idempotency_key.removeprefix(stem)
        if not raw.isascii() or not raw.isdecimal() or len(raw) > 12:
            return False
        number = int(raw)
        if not original_cycle < number <= cycle or number in abandoned:
            return False
        abandoned.add(number)
    return bool(abandoned) and (workspace.generation_run_id is None or cycle in abandoned)


async def parked_source_repair(
    session: AsyncSession, run: GenerationRun, state: RecoveryStateV2, *,
    claimed_preparation: bool = False,
) -> bool:
    from yleum_api.services.generation.recovery_execution import (
        _owned_release_receipt,
        load_execution_context,
    )
    from yleum_api.services.generation.recovery_pipeline import (
        CompletedAgentTurn,
        FinalizationProgress,
    )
    from yleum_api.services.generation.recovery_quiescence import _provider_stopped

    execution, binding = state.execution, state.binding
    metrics = run.agent_state.get("max_finalization")
    terminal = metrics.get("terminal_reason") if isinstance(metrics, dict) else None
    if (
        not isinstance(state, RecoveryStateV3)
        or run.status != "running"
        or run.execution_backend != "worker"
        or run.response_mode not in {"build", "edit"}
        or not isinstance(terminal, str)
        or not terminal.startswith(PARKED_THROW_DIAGNOSTIC_PREFIX)
        or state.phase != ("working" if claimed_preparation else "needs_input")
        or state.next_check_at is not None
        or state.clock.stage != "edit"
        or (not claimed_preparation and (
            state.clock.interval_open or PARKED_SOURCE_REDRIVE_KEY in run.agent_state
        ))
        or state.clock.repair_granted
        or state.clock.wait_dependency is not None
        or state.unresolved_operation_ids
        or execution.stage != "finalization"
        or execution.substep != "checkpoint_ready"
        or execution.physical_state != "released"
        or execution.pending_effect_id is not None
        or execution.budget.repair_calls != 0
        or execution.release_operation_id is None
        or execution.control_workspace_id is None
        or binding is None
        or binding.workspace_id != str(execution.control_workspace_id)
        or execution.control_fencing_epoch != binding.fencing_epoch + 1
        or state.checkpoint is None
        or state.context_ref is None
        or state.context_ref != execution.execution_context_ref
    ):
        return False

    project = await session.get(Project, run.project_id, populate_existing=True)
    workspace = await session.get(
        ProjectCellWorkspace, execution.control_workspace_id, populate_existing=True,
    )
    operation = await session.get(
        ProjectCellOperation, execution.release_operation_id, populate_existing=True,
    )
    if (
        project is None or project.owner_id != run.user_id or project.template != "max_miniapp"
        or project.current_snapshot_id != state.identity.accepted_snapshot_id
        or operation is None or workspace is None or workspace.owner_id != run.user_id
        or workspace.project_id != project.id
        or workspace.deleted_at is not None
        or workspace.generation_run_id not in ({None, run.id} if claimed_preparation else {None})
        or workspace.fencing_epoch != execution.control_fencing_epoch
        or not _owned_release_receipt(
            operation, run_id=run.id, workspace_id=workspace.id, fence=workspace.fencing_epoch,
        )
        or not await _provider_stopped(session, run)
    ):
        return False

    if claimed_preparation and not await _only_cancelled_preparation(
        session, run, state, workspace, operation,
    ):
        return False
    snapshot_id = state.identity.accepted_snapshot_id
    baseline = await session.get(Snapshot, snapshot_id) if snapshot_id is not None else None
    if snapshot_id is not None and (baseline is None or baseline.project_id != project.id):
        return False
    baseline_sha = baseline.commit_sha if baseline is not None else None
    try:
        async with asyncio.timeout(8):
            context = await load_execution_context(
                state.identity, state.context_ref, binding=binding,
            )
            data = await source_checkpoint.load_checkpoint_object(
                project.id, run.id, object_key=state.checkpoint.object_key,
                digest=state.checkpoint.sha256,
            )
        completed = context["completed"]
        if data is None or type(completed) is not dict:
            return False
        if completed.get("source_repair") is not None:
            return False
        progress = FinalizationProgress.model_validate_json(
            json.dumps(completed["finalization_progress"], allow_nan=False),
        )
        turn = CompletedAgentTurn.from_json(completed["agent_turn"])
        checkpoint = source_checkpoint.decode_checkpoint(
            data, project_id=project.id, user_id=run.user_id, run_id=run.id,
            baseline_sha=baseline_sha,
        )
        return (
            checkpoint is not None
            and progress.pending_detail == terminal
            and progress.pending_fast_check
            and not progress.pending_targeted
            and not progress.no_progress
            and turn.result.stop_reason == "source_deadline"
            and not turn.result.done and bool(turn.result.files) and not turn.provider_failure
            and matches_parked_throw_diagnostic(terminal, checkpoint["files"])
        )
    except (KeyError, TypeError, ValueError, TimeoutError):
        return False
