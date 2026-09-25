"""Two deliberate owner actions on unsaved draft edits: save as a version, or discard.

Why they exist: a rollback honestly refuses when the draft's files differ from the
saved version (the orchestrator's verify_source_inventory) — restoring over unsaved
work would lose it silently. But the owner had no way to resolve that state: an
ordinary generation on such a draft fails with unsafe_changes_rolled_back, and the
agent endpoints require an active generation lease. So the check stays; the owner
gets a way to satisfy it.

Both actions run BETWEEN generations, under the same admission lock restorations
use (no active restoration, no active generation, workspace lease free).
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from yleum_api.core.errors import ApiError
from yleum_api.models.project import Project
from yleum_api.models.project_cell import ProjectCellWorkspace
from yleum_api.models.project_version import ProjectVersion
from yleum_api.models.snapshot import Snapshot
from yleum_api.services import orchestrator_client, repo
from yleum_api.services.orchestrator_client import OrchestratorBadRequest
from yleum_api.services.project_versions import record_manual_version
from yleum_api.services.restorations import lock_restoration_admission

SAVED_BY_OWNER = "Правки сохранены владельцем"
DRAFT_CLEAN = "draft_clean"


@dataclass(frozen=True)
class DraftPatch:
    """What has to change in the draft to make it equal the saved version."""

    writes: dict[str, str]
    deletes: tuple[str, ...]

    @property
    def empty(self) -> bool:
        return not self.writes and not self.deletes


@dataclass(frozen=True)
class DraftDiscardResult:
    written: int
    deleted: int
    workspace_revision: str


def draft_reset_patch(current: Mapping[str, str], saved: Mapping[str, str]) -> DraftPatch:
    """Writes for files that differ or are missing, deletes for files the version lacks."""
    writes = {path: content for path, content in saved.items() if current.get(path) != content}
    deletes = tuple(sorted(path for path in current if path not in saved))
    return DraftPatch(writes=writes, deletes=deletes)


async def _owned_idle_workspace(
    session: AsyncSession, project_id: UUID, owner_id: UUID
) -> tuple[Project, ProjectCellWorkspace, Snapshot]:
    project = await lock_restoration_admission(session, project_id, owner_id)
    workspace = await session.scalar(
        select(ProjectCellWorkspace)
        .where(ProjectCellWorkspace.project_id == project_id)
        .with_for_update()
    )
    if workspace is None:
        raise ApiError(
            "conflict",
            "У проекта ещё нет среды. Откройте предпросмотр и повторите.",
            409,
        )
    if workspace.generation_run_id is not None:
        raise ApiError(
            "generation_active",
            "Сейчас идёт сборка. Дождитесь её завершения и повторите.",
            409,
            details={"active_run_id": str(workspace.generation_run_id)},
        )
    head = (
        await session.get(Snapshot, project.current_snapshot_id)
        if project.current_snapshot_id is not None
        else None
    )
    if head is None or head.project_id != project.id:
        raise ApiError(
            "conflict",
            "У проекта нет сохранённой версии, с которой можно сравнить черновик.",
            409,
        )
    return project, workspace, head


def _owner_facing(exc: OrchestratorBadRequest) -> ApiError:
    message = str(exc)
    if "resources are not ready" in message:
        return ApiError(
            "conflict",
            "Среда проекта сейчас спит. Откройте предпросмотр, чтобы разбудить её, и повторите.",
            409,
            details={"reason": "workspace_asleep"},
        )
    if "generation lease is active" in message:
        return ApiError(
            "generation_active",
            "Сейчас идёт сборка. Дождитесь её завершения и повторите.",
            409,
        )
    if "stale" in message or exc.status_code == 409:
        return ApiError(
            "conflict",
            "Черновик изменился во время действия. Обновите страницу и повторите.",
            409,
            details={"reason": "draft_changed"},
        )
    return exc


async def _draft_files(workspace_id: UUID) -> orchestrator_client.ProjectCellDraftFiles:
    try:
        return await orchestrator_client.project_cell_draft_files(workspace_id)
    except OrchestratorBadRequest as exc:
        raise _owner_facing(exc) from exc


async def save_draft_as_version(
    session: AsyncSession, project_id: UUID, owner_id: UUID
) -> ProjectVersion:
    """Commit the draft exactly as it is on disk as the new head version."""
    project, workspace, head = await _owned_idle_workspace(session, project_id, owner_id)
    draft = await _draft_files(workspace.id)
    saved_files = await asyncio.to_thread(repo.read_files, project.id, head.commit_sha)
    if draft_reset_patch(draft.files, saved_files).empty:
        raise ApiError(
            "conflict",
            "Черновик совпадает с сохранённой версией — сохранять нечего.",
            409,
            details={"reason": DRAFT_CLEAN},
        )
    commit_sha = await asyncio.to_thread(
        repo.commit_files,
        project.id,
        dict(draft.files),
        SAVED_BY_OWNER,
        head.commit_sha,
        exact_tree=True,
    )
    snapshot = Snapshot(
        project_id=project.id,
        commit_sha=commit_sha,
        prompt_text=SAVED_BY_OWNER,
        model_id=None,
        parent_id=head.id,
    )
    session.add(snapshot)
    await session.flush()
    project.current_snapshot_id = snapshot.id
    version = await record_manual_version(session, project, snapshot, label=SAVED_BY_OWNER)
    await session.commit()
    return version


async def discard_draft_changes(
    session: AsyncSession, project_id: UUID, owner_id: UUID
) -> DraftDiscardResult:
    """Rewrite the draft to the saved head version; nothing in the database changes."""
    project, workspace, head = await _owned_idle_workspace(session, project_id, owner_id)
    saved_files = await asyncio.to_thread(repo.read_files, project.id, head.commit_sha)
    draft = await _draft_files(workspace.id)
    patch = draft_reset_patch(draft.files, saved_files)
    if patch.empty:
        raise ApiError(
            "conflict",
            "Черновик уже совпадает с сохранённой версией — отбрасывать нечего.",
            409,
            details={"reason": DRAFT_CLEAN},
        )
    try:
        result = await orchestrator_client.project_cell_draft_reset(
            workspace.id,
            expected_revision=draft.workspace_revision,
            files=patch.writes,
            deletes=list(patch.deletes),
        )
    except OrchestratorBadRequest as exc:
        raise _owner_facing(exc) from exc
    await session.commit()
    return DraftDiscardResult(
        written=result.written,
        deleted=result.deleted,
        workspace_revision=result.workspace_revision,
    )
