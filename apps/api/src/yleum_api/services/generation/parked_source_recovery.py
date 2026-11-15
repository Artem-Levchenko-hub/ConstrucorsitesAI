"""Recognize one known parked repair without authorizing new product effects."""

from __future__ import annotations

import asyncio
import json

from sqlalchemy.ext.asyncio import AsyncSession

from yleum_api.models.generation_run import GenerationRun
from yleum_api.models.project import Project
from yleum_api.models.project_cell import ProjectCellOperation, ProjectCellWorkspace
from yleum_api.models.snapshot import Snapshot
from yleum_api.services.generation import source_checkpoint
from yleum_api.services.generation.recovery_contract import RecoveryStateV2, RecoveryStateV3
from yleum_api.services.max_management_generation import (
    PARKED_THROW_DIAGNOSTIC_PREFIX,
    matches_parked_throw_diagnostic,
)

PARKED_SOURCE_REDRIVE_KEY = "parked_source_redrive_v1"


async def parked_source_repair(
    session: AsyncSession, run: GenerationRun, state: RecoveryStateV2,
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
        or PARKED_SOURCE_REDRIVE_KEY in run.agent_state
        or not isinstance(terminal, str)
        or not terminal.startswith(PARKED_THROW_DIAGNOSTIC_PREFIX)
        or state.phase != "needs_input"
        or state.next_check_at is not None
        or state.clock.stage != "edit"
        or state.clock.interval_open
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
        or workspace is None or workspace.owner_id != run.user_id
        or workspace.project_id != project.id
        or workspace.deleted_at is not None or workspace.generation_run_id is not None
        or workspace.fencing_epoch != execution.control_fencing_epoch
        or not _owned_release_receipt(
            operation, run_id=run.id, workspace_id=workspace.id, fence=workspace.fencing_epoch,
        )
        or not await _provider_stopped(session, run)
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
