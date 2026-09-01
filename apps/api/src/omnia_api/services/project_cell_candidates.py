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
_CONTENT_ADDRESS_KIND_BY_FIELD = {
    "database_backup_ref": "database-backup",
    "build_ref": "build",
    "verification_ref": "verification",
}
_CONTENT_ADDRESS_PATTERN_BY_FIELD = {
    field: re.compile(rf"{kind}/sha256/[0-9a-f]{{64}}\Z")
    for field, kind in _CONTENT_ADDRESS_KIND_BY_FIELD.items()
}
_RUNNING_CANDIDATE_RUN_STATUSES = frozenset({"running"})
_CANCELLABLE_CANDIDATE_RUN_STATUSES = frozenset({"running", "cancel_requested"})


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
    workspace = await _locked_workspace(session, workspace_id)
    _require_workspace_lease(workspace, generation_run_id, fencing_epoch)
    await _require_generation_status(
        session,
        workspace,
        generation_run_id,
        allowed_statuses=_RUNNING_CANDIDATE_RUN_STATUSES,
    )
    if _HEX_REVISION.fullmatch(source_revision) is None:
        raise ProjectCellValidationError("source_revision must be a lowercase Git hash")
    if _SHA256.fullmatch(migration_digest) is None:
        raise ProjectCellValidationError("migration_digest must be a lowercase sha256")
    for name, value in (
        ("database_backup_ref", database_backup_ref),
        ("build_ref", build_ref),
        ("verification_ref", verification_ref),
    ):
        if _CONTENT_ADDRESS_PATTERN_BY_FIELD[name].fullmatch(value) is None:
            kind = _CONTENT_ADDRESS_KIND_BY_FIELD[name]
            raise ProjectCellValidationError(
                f"{name} must be a content-addressed {kind}/sha256/<64hex> reference"
            )

    accepted_id = await _accepted_candidate_id(session, workspace_id)
    existing = await _matching_candidate(
        session,
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
    if existing is not None:
        return existing
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
    workspace_id = await _candidate_workspace_id(session, candidate_id)
    await _lock_workspace(session, workspace_id)
    candidate = await _locked_candidate(session, candidate_id)
    workspace = await _locked_workspace(session, candidate.workspace_id)
    if candidate.status != "prepared" or candidate.cancelled:
        raise ProjectCellStateConflict("candidate is not promotable")
    if candidate.generation_run_id != generation_run_id:
        raise ProjectCellStateConflict("candidate belongs to another generation run")
    _require_workspace_lease(workspace, generation_run_id, fencing_epoch)
    if candidate.fencing_epoch != fencing_epoch:
        raise ProjectCellStateConflict("candidate lease or fencing epoch is stale")
    await _require_generation_status(
        session,
        workspace,
        generation_run_id,
        allowed_statuses=_RUNNING_CANDIDATE_RUN_STATUSES,
    )

    accepted_id = await _accepted_candidate_id(session, candidate.workspace_id)
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


async def cancel_candidate(
    session: AsyncSession,
    *,
    candidate_id: UUID,
    generation_run_id: UUID,
    fencing_epoch: int,
) -> None:
    workspace_id = await _candidate_workspace_id(session, candidate_id)
    await _lock_workspace(session, workspace_id)
    candidate = await _locked_candidate(session, candidate_id)
    workspace = await _locked_workspace(session, candidate.workspace_id)
    if candidate.status == "accepted":
        raise ProjectCellStateConflict("accepted candidate cannot be cancelled")
    if candidate.generation_run_id != generation_run_id:
        raise ProjectCellStateConflict("candidate belongs to another generation run")
    _require_workspace_lease(workspace, generation_run_id, fencing_epoch)
    if candidate.fencing_epoch != fencing_epoch:
        raise ProjectCellStateConflict("candidate lease or fencing epoch is stale")
    await _require_generation_status(
        session,
        workspace,
        generation_run_id,
        allowed_statuses=_CANCELLABLE_CANDIDATE_RUN_STATUSES,
    )
    if candidate.status == "cancelled":
        return
    if candidate.status != "prepared":
        raise ProjectCellStateConflict("candidate is not cancellable")
    candidate.cancelled = True
    candidate.status = "cancelled"
    await session.flush()


async def _lock_workspace(session: AsyncSession, workspace_id: UUID) -> None:
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:workspace_id))"),
        {"workspace_id": str(workspace_id)},
    )


async def _candidate_workspace_id(session: AsyncSession, candidate_id: UUID) -> UUID:
    workspace_id = await session.scalar(
        select(ProjectCellCandidate.workspace_id).where(
            ProjectCellCandidate.id == candidate_id
        )
    )
    if workspace_id is None:
        raise ProjectCellNotFound("Project Cell candidate not found")
    return workspace_id


async def _locked_candidate(
    session: AsyncSession,
    candidate_id: UUID,
) -> ProjectCellCandidate:
    candidate = await session.scalar(
        select(ProjectCellCandidate)
        .where(ProjectCellCandidate.id == candidate_id)
        .execution_options(populate_existing=True)
        .with_for_update()
    )
    if candidate is None:
        raise ProjectCellNotFound("Project Cell candidate not found")
    return candidate


async def _locked_workspace(
    session: AsyncSession,
    workspace_id: UUID,
) -> ProjectCellWorkspace:
    workspace = await session.scalar(
        select(ProjectCellWorkspace)
        .where(ProjectCellWorkspace.id == workspace_id)
        .execution_options(populate_existing=True)
        .with_for_update()
    )
    if workspace is None:
        raise ProjectCellNotFound("Project Cell workspace not found")
    return workspace


async def _accepted_candidate_id(
    session: AsyncSession,
    workspace_id: UUID,
) -> UUID | None:
    accepted_id: UUID | None = await session.scalar(
        select(ProjectCellCandidate.id).where(
            ProjectCellCandidate.workspace_id == workspace_id,
            ProjectCellCandidate.status == "accepted",
        )
    )
    return accepted_id


async def _matching_candidate(
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
    expected_accepted_candidate_id: UUID | None,
) -> ProjectCellCandidate | None:
    statement = select(ProjectCellCandidate).where(
        ProjectCellCandidate.workspace_id == workspace_id,
        ProjectCellCandidate.status == "prepared",
        ProjectCellCandidate.generation_run_id == generation_run_id,
        ProjectCellCandidate.fencing_epoch == fencing_epoch,
        ProjectCellCandidate.source_revision == source_revision,
        ProjectCellCandidate.migration_digest == migration_digest,
        ProjectCellCandidate.database_backup_ref == database_backup_ref,
        ProjectCellCandidate.build_ref == build_ref,
        ProjectCellCandidate.verification_ref == verification_ref,
    )
    if expected_accepted_candidate_id is None:
        statement = statement.where(
            ProjectCellCandidate.expected_accepted_candidate_id.is_(None)
        )
    else:
        statement = statement.where(
            ProjectCellCandidate.expected_accepted_candidate_id
            == expected_accepted_candidate_id
        )
    candidate: ProjectCellCandidate | None = await session.scalar(
        statement
        .order_by(ProjectCellCandidate.created_at.desc(), ProjectCellCandidate.id.desc())
        .execution_options(populate_existing=True)
        .limit(1)
    )
    return candidate


def _require_workspace_lease(
    workspace: ProjectCellWorkspace,
    generation_run_id: UUID,
    fencing_epoch: int,
) -> None:
    if workspace.state != "ready":
        raise ProjectCellStateConflict("Project Cell workspace is not ready")
    if workspace.generation_run_id != generation_run_id:
        raise ProjectCellStateConflict("generation run does not hold the workspace lease")
    if workspace.fencing_epoch != fencing_epoch or fencing_epoch <= 0:
        raise ProjectCellStateConflict("stale Project Cell fencing epoch")


async def _require_generation_status(
    session: AsyncSession,
    workspace: ProjectCellWorkspace,
    generation_run_id: UUID,
    *,
    allowed_statuses: frozenset[str],
) -> GenerationRun:
    run = await session.scalar(
        select(GenerationRun)
        .where(GenerationRun.id == generation_run_id)
        .execution_options(populate_existing=True)
        .with_for_update()
    )
    if run is None:
        raise ProjectCellNotFound("generation run not found")
    if run.project_id != workspace.project_id or run.user_id != workspace.owner_id:
        raise ProjectCellStateConflict("generation run does not belong to the workspace owner")
    if run.status not in allowed_statuses:
        raise ProjectCellStateConflict(
            "generation run is not running"
            if allowed_statuses == _RUNNING_CANDIDATE_RUN_STATUSES
            else "generation run is not active for candidate cancellation"
        )
    return run
