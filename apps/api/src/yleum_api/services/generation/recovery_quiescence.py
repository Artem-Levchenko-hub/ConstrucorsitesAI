"""Read-only deployment evidence for individually stopped durable executions."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from yleum_api.models.generation_provider_attempt import GenerationProviderAttempt
from yleum_api.models.generation_recovery_effect import GenerationRecoveryEffect
from yleum_api.models.generation_run import GenerationRun
from yleum_api.models.project import Project
from yleum_api.models.project_cell import ProjectCellOperation, ProjectCellWorkspace
from yleum_api.models.provider_call import ProviderCall
from yleum_api.services.generation.recovery_contract import CheckpointRef, RecoveryStateV2


@dataclass(frozen=True, slots=True)
class PhysicalQuiescenceEvidence:
    run_id: UUID
    checkpoint: CheckpointRef | None
    attempt_seq: int
    unresolved_operation_ids: tuple[UUID, ...]
    no_physical_writer: bool = True
    lease_released_or_fenced: bool = True


@dataclass(frozen=True, slots=True)
class QuiescenceEvidence:
    physical: PhysicalQuiescenceEvidence
    drain_release_sha: str
    reader_snapshot_digest: str


async def _provider_stopped(session: AsyncSession, run: GenerationRun) -> bool:
    attempts = (
        await session.scalars(
            select(GenerationProviderAttempt).where(
                GenerationProviderAttempt.run_id == run.id,
            )
        )
    ).all()
    for attempt in attempts:
        if (
            attempt.phase != "closed"
            or attempt.provider_call_id is None
            or (attempt.project_id, attempt.user_id, attempt.prompt_hash)
            != (run.project_id, run.user_id, run.prompt_hash)
        ):
            return False
        call = await session.get(ProviderCall, attempt.provider_call_id)
        if (
            call is None
            or call.finished_at is None
            or call.status == "started"
            or (call.run_id, call.project_id, call.user_id) != (run.id, run.project_id, run.user_id)
        ):
            return False
        # A terminal provider journal alone cannot prove the gateway receiver
        # stopped storing bytes. Keep a still-uploading or unknown body fenced.
        state = await session.scalar(
            text("SELECT state FROM generation_responses WHERE attempt_id=:attempt"),
            {"attempt": attempt.attempt_id},
        )
        if state not in {"complete", "deleted"}:
            return False
    return True


async def _adaptation_held(
    session: AsyncSession,
    run: GenerationRun,
    state: RecoveryStateV2,
) -> bool:
    from yleum_api.services.generation import recovery_adaptation as adaptation

    request, original = await adaptation._prepared(session, run)
    cycle = state.execution.lease_cycle_seq
    row = await session.get(GenerationRecoveryEffect, adaptation._id(run.id, "hold", cycle))
    if row is None:
        return False
    command = adaptation._check_effect(row, run, "hold")
    receipt = adaptation._receipt(
        adaptation._confirmed(row),
        request,
        original,
        "hold",
        cycle,
        command,
    )
    # A later rebind is a live writer even if an older held receipt survives.
    newer = await session.get(GenerationRecoveryEffect, adaptation._id(run.id, "rebind", cycle + 1))
    return (
        newer is None
        and receipt.state == "held"
        and receipt.workspace_id == state.execution.control_workspace_id
        # The owning terminal release claims exactly the next SQL fence.
        # Candidate hold remains bound to its original pre-release fence.
        and receipt.current_control_fencing_epoch + 1 == state.execution.control_fencing_epoch
        and state.binding is not None
        and str(receipt.candidate_workspace_id) == state.binding.workspace_id
        and receipt.current_candidate_fencing_epoch == state.binding.fencing_epoch
    )


async def _quiesced_physical_run(
    session: AsyncSession,
    run: GenerationRun,
) -> PhysicalQuiescenceEvidence | None:
    from yleum_api.services.generation.recovery_execution import (
        _initial_quiescent,
        _owned_release_receipt,
        recovery_state,
    )

    try:
        state = recovery_state(run)
        if (
            run.status != "running"
            or not isinstance(state, RecoveryStateV2)
            or state.phase not in {"waiting_dependency", "reconciling", "needs_input"}
        ):
            return None
        project = await session.get(Project, run.project_id)
        if project is None or project.owner_id != run.user_id:
            return None
        held = await session.scalar(
            text(
                "SELECT EXISTS(SELECT 1 FROM pg_locks WHERE locktype='advisory' "
                "AND classid=1195724366 AND objid=:key AND objsubid=2 AND granted)"
            ),
            {"key": int.from_bytes(run.id.bytes[-4:], "big")},
        )
        if held:
            return None
        execution = state.execution
        if execution.physical_state == "no_effects":
            if not await _initial_quiescent(session, run.id):
                return None
        else:
            if (
                execution.physical_state != "released"
                or state.checkpoint is None
                or state.context_ref is None
                or execution.execution_context_ref is None
                or state.binding is None
                or execution.release_operation_id is None
                or execution.control_workspace_id is None
                or execution.control_fencing_epoch is None
                or execution.substep == "publication_pending"
            ):
                return None
            workspace = await session.get(ProjectCellWorkspace, execution.control_workspace_id)
            operation = await session.get(ProjectCellOperation, execution.release_operation_id)
            if (
                workspace is None
                or workspace.project_id != run.project_id
                or workspace.owner_id != run.user_id
                or workspace.deleted_at is not None
                or workspace.generation_run_id is not None
                or workspace.fencing_epoch != execution.control_fencing_epoch
                or not _owned_release_receipt(
                    operation,
                    run_id=run.id,
                    workspace_id=workspace.id,
                    fence=workspace.fencing_epoch,
                )
            ):
                return None
            if state.binding.workspace_id != str(workspace.id):
                if not await _adaptation_held(session, run, state):
                    return None
            elif state.binding.fencing_epoch != workspace.fencing_epoch:
                return None
            if not await _provider_stopped(session, run):
                return None
        return PhysicalQuiescenceEvidence(
            run.id,
            state.checkpoint,
            state.attempt_seq,
            state.unresolved_operation_ids,
        )
    except (ValueError, TypeError, KeyError):
        # Unsupported or corrupt state stays byte-for-byte intact and blocks.
        return None


async def quiesced_recovery_run(
    session: AsyncSession,
    run: GenerationRun,
    *,
    release_sha: str,
    snapshot_digest: str,
) -> QuiescenceEvidence | None:
    physical = await _quiesced_physical_run(session, run)
    return (
        QuiescenceEvidence(physical, release_sha, snapshot_digest)
        if physical is not None else None
    )
