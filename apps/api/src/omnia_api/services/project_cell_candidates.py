"""Fenced, compare-and-swap release candidate persistence."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from omnia_api.models.generation_run import GenerationRun
from omnia_api.models.project_cell import ProjectCellCandidate, ProjectCellWorkspace
from omnia_api.services.project_cells import (
    ProjectCellNotFound,
    ProjectCellStateConflict,
    ProjectCellValidationError,
)

_HEX_REVISION = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_SAFE_REF = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,511}\Z")


async def prepare_candidate(
    session: AsyncSession,
    *,
    workspace_id: UUID,
    generation_run_id: UUID,
    fencing_epoch: int,
    source_revision: str,
    migration_digest: str,
    database_backup_ref: str,
    build_ref: str,
    verification_ref: str,
) -> ProjectCellCandidate:
    """Store immutable evidence only for the workspace's current writable lease."""
    await _lock_workspace(session, workspace_id)
    workspace = await session.get(ProjectCellWorkspace, workspace_id)
    if workspace is None:
        raise ProjectCellNotFound("Project Cell workspace not found")
    if workspace.state != "ready":
        raise ProjectCellStateConflict("Project Cell workspace is not ready")
    if workspace.generation_run_id != generation_run_id:
        raise ProjectCellStateConflict("generation run does not hold the workspace lease")
    if workspace.fencing_epoch != fencing_epoch or fencing_epoch <= 0:
        raise ProjectCellStateConflict("stale Project Cell fencing epoch")
    await _require_running_generation(session, workspace, generation_run_id)
    if _HEX_REVISION.fullmatch(source_revision) is None:
        raise ProjectCellValidationError("source_revision must be a lowercase Git hash")
    if _SHA256.fullmatch(migration_digest) is None:
        raise ProjectCellValidationError("migration_digest must be a lowercase sha256")
    for name, value in (
        ("database_backup_ref", database_backup_ref),
        ("build_ref", build_ref),
        ("verification_ref", verification_ref),
    ):
        if _SAFE_REF.fullmatch(value) is None:
            raise ProjectCellValidationError(f"{name} is not a safe immutable reference")

    accepted_id = await session.scalar(
        select(ProjectCellCandidate.id).where(
            ProjectCellCandidate.workspace_id == workspace_id,
            ProjectCellCandidate.status == "accepted",
        )
    )
    candidate = ProjectCellCandidate(
        workspace_id=workspace_id,
        generation_run_id=generation_run_id,
        fencing_epoch=fencing_epoch,
        source_revision=source_revision,
        migration_digest=migration_digest,
        database_backup_ref=database_backup_ref,
        build_ref=build_ref,
        verification_ref=verification_ref,
        expected_accepted_candidate_id=accepted_id,
    )
    session.add(candidate)
    await session.flush()
    return candidate


async def promote_candidate(
    session: AsyncSession,
    *,
    candidate_id: UUID,
    generation_run_id: UUID,
    fencing_epoch: int,
) -> ProjectCellCandidate:
    """Atomically replace the accepted candidate when its captured base still matches."""
    candidate = await session.get(ProjectCellCandidate, candidate_id)
    if candidate is None:
        raise ProjectCellNotFound("Project Cell candidate not found")
    await _lock_workspace(session, candidate.workspace_id)
    await session.refresh(candidate)
    workspace = await session.get(ProjectCellWorkspace, candidate.workspace_id)
    if workspace is None:
        raise ProjectCellNotFound("Project Cell workspace not found")
    if candidate.status != "prepared" or candidate.cancelled:
        raise ProjectCellStateConflict("candidate is not promotable")
    if candidate.generation_run_id != generation_run_id:
        raise ProjectCellStateConflict("candidate belongs to another generation run")
    if (
        workspace.state != "ready"
        or workspace.generation_run_id != generation_run_id
        or workspace.fencing_epoch != fencing_epoch
        or candidate.fencing_epoch != fencing_epoch
    ):
        raise ProjectCellStateConflict("candidate lease or fencing epoch is stale")
    await _require_running_generation(session, workspace, generation_run_id)

    accepted_id = await session.scalar(
        select(ProjectCellCandidate.id).where(
            ProjectCellCandidate.workspace_id == candidate.workspace_id,
            ProjectCellCandidate.status == "accepted",
        )
    )
    if accepted_id != candidate.expected_accepted_candidate_id:
        raise ProjectCellStateConflict("accepted candidate changed after preparation")
    if accepted_id is not None:
        await session.execute(
            update(ProjectCellCandidate)
            .where(ProjectCellCandidate.id == accepted_id)
            .values(status="rejected")
        )
    candidate.status = "accepted"
    candidate.promoted_at = datetime.now(UTC)
    await session.flush()
    return candidate


async def cancel_candidate(session: AsyncSession, candidate_id: UUID) -> None:
    candidate = await session.get(ProjectCellCandidate, candidate_id)
    if candidate is None:
        raise ProjectCellNotFound("Project Cell candidate not found")
    await _lock_workspace(session, candidate.workspace_id)
    await session.refresh(candidate)
    if candidate.status == "accepted":
        raise ProjectCellStateConflict("accepted candidate cannot be cancelled")
    candidate.cancelled = True
    candidate.status = "cancelled"
    await session.flush()


async def _lock_workspace(session: AsyncSession, workspace_id: UUID) -> None:
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:workspace_id))"),
        {"workspace_id": str(workspace_id)},
    )


async def _require_running_generation(
    session: AsyncSession,
    workspace: ProjectCellWorkspace,
    generation_run_id: UUID,
) -> GenerationRun:
    run = await session.scalar(
        select(GenerationRun)
        .where(GenerationRun.id == generation_run_id)
        .with_for_update()
    )
    if run is None:
        raise ProjectCellNotFound("generation run not found")
    if run.project_id != workspace.project_id or run.user_id != workspace.owner_id:
        raise ProjectCellStateConflict("generation run does not belong to the workspace owner")
    if run.status != "running":
        raise ProjectCellStateConflict("generation run is not running")
    return run
