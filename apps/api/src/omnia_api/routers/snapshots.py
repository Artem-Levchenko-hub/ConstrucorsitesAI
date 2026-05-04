import asyncio
from typing import Any
from uuid import UUID

from fastapi import APIRouter, status
from sqlalchemy import select

from omnia_api.core.deps import CurrentUserDep, SessionDep
from omnia_api.core.errors import ApiError
from omnia_api.core.minio import preview_public_url
from omnia_api.models.project import Project
from omnia_api.models.snapshot import Snapshot
from omnia_api.schemas.snapshot import SnapshotPublic, SnapshotWithFiles
from omnia_api.services import repo as repo_svc

router = APIRouter(prefix="/api/projects", tags=["snapshots"])


async def _project_owned_by(
    session: Any, project_id: UUID, user_id: UUID
) -> Project:
    project = await session.get(Project, project_id)
    if project is None or project.owner_id != user_id:
        raise ApiError("not_found", "project not found", status.HTTP_404_NOT_FOUND)
    return project


def _public_dict(s: Snapshot) -> dict[str, Any]:
    return {
        "id": s.id,
        "project_id": s.project_id,
        "commit_sha": s.commit_sha,
        "prompt_text": s.prompt_text,
        "model_id": s.model_id,
        "parent_id": s.parent_id,
        "preview_url": preview_public_url(s.preview_key),
        "is_rollback_target": s.is_rollback_target,
        "created_at": s.created_at,
    }


@router.get("/{project_id}/snapshots", response_model=list[SnapshotPublic])
async def list_snapshots(
    project_id: UUID, session: SessionDep, current_user: CurrentUserDep
) -> list[SnapshotPublic]:
    await _project_owned_by(session, project_id, current_user.id)
    res = await session.execute(
        select(Snapshot)
        .where(Snapshot.project_id == project_id)
        .order_by(Snapshot.created_at.desc())
    )
    return [SnapshotPublic.model_validate(_public_dict(s)) for s in res.scalars().all()]


@router.get("/{project_id}/snapshots/{snapshot_id}", response_model=SnapshotWithFiles)
async def get_snapshot(
    project_id: UUID,
    snapshot_id: UUID,
    session: SessionDep,
    current_user: CurrentUserDep,
) -> SnapshotWithFiles:
    await _project_owned_by(session, project_id, current_user.id)
    snapshot = await session.get(Snapshot, snapshot_id)
    if snapshot is None or snapshot.project_id != project_id:
        raise ApiError("not_found", "snapshot not found", status.HTTP_404_NOT_FOUND)
    files = await asyncio.to_thread(repo_svc.read_files, project_id, snapshot.commit_sha)
    payload = _public_dict(snapshot) | {"files": files}
    return SnapshotWithFiles.model_validate(payload)
