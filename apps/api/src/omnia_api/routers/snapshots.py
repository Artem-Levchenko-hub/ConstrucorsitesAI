import asyncio
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Query, Response, status
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from omnia_api.core.deps import CurrentUserDep, SessionDep
from omnia_api.core.errors import ApiError
from omnia_api.core.minio import preview_public_url
from omnia_api.models.max_project_config import MaxProjectConfig
from omnia_api.models.project import Project
from omnia_api.models.snapshot import Snapshot
from omnia_api.schemas.max_studio import MaxProjectConfigPayload
from omnia_api.schemas.snapshot import SnapshotPublic, SnapshotWithFiles
from omnia_api.services import orchestrator_client
from omnia_api.services import repo as repo_svc
from omnia_api.services.max_project_kit import (
    default_max_project_config,
    max_legacy_snapshot_incompatibility,
    max_project_config_from_files,
    render_max_history_files,
)
from omnia_api.services.queue import enqueue_preview

router = APIRouter(prefix="/api/projects", tags=["snapshots"])


async def _project_owned_by(session: AsyncSession, project_id: UUID, user_id: UUID) -> Project:
    project = await session.get(Project, project_id)
    if project is None or project.owner_id != user_id:
        raise ApiError("not_found", "project not found", status.HTTP_404_NOT_FOUND)
    return project


def _public_dict(s: Snapshot, *, version_number: int | None = None) -> dict[str, Any]:
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
        "version_number": version_number,
    }


@router.get("/{project_id}/snapshots", response_model=list[SnapshotPublic])
async def list_snapshots(
    project_id: UUID,
    session: SessionDep,
    current_user: CurrentUserDep,
    limit: int | None = Query(default=None, ge=1, le=100),
) -> list[SnapshotPublic]:
    await _project_owned_by(session, project_id, current_user.id)
    query = (
        select(Snapshot)
        .where(Snapshot.project_id == project_id)
        .order_by(Snapshot.created_at.desc())
    )
    if limit is not None:
        query = query.limit(limit)
    productive = or_(Snapshot.prompt_text.is_not(None), Snapshot.parent_id.is_not(None))
    total = int(
        (
            await session.execute(
                select(func.count(Snapshot.id)).where(Snapshot.project_id == project_id, productive)
            )
        ).scalar_one()
    )
    res = await session.execute(query)
    version_number = total
    output: list[SnapshotPublic] = []
    for snapshot in res.scalars().all():
        is_productive = snapshot.prompt_text is not None or snapshot.parent_id is not None
        output.append(
            SnapshotPublic.model_validate(
                _public_dict(
                    snapshot,
                    version_number=version_number if is_productive else None,
                )
            )
        )
        if is_productive:
            version_number -= 1
    return output


@router.post(
    "/{project_id}/snapshots/{snapshot_id}/preview",
    response_model=SnapshotPublic,
    status_code=status.HTTP_202_ACCEPTED,
)
async def prepare_snapshot_preview(
    project_id: UUID,
    snapshot_id: UUID,
    response: Response,
    session: SessionDep,
    current_user: CurrentUserDep,
) -> SnapshotPublic:
    """Ensure one immutable history preview is queued, without duplicates."""
    project = await _project_owned_by(session, project_id, current_user.id)
    snapshot = await session.get(Snapshot, snapshot_id)
    if snapshot is None or snapshot.project_id != project_id:
        raise ApiError("not_found", "snapshot not found", status.HTTP_404_NOT_FOUND)
    if snapshot.preview_key:
        response.status_code = status.HTTP_200_OK
    else:
        if project.template == "max_miniapp":
            snapshot_files = await asyncio.to_thread(
                repo_svc.read_files, project_id, snapshot.commit_sha
            )
            if max_legacy_snapshot_incompatibility(snapshot_files):
                raise ApiError(
                    "conflict",
                    (
                        "Эта старая версия использует серверную структуру, которую нельзя "
                        "безопасно открыть для создания превью."
                    ),
                    status.HTTP_409_CONFLICT,
                )
        await asyncio.to_thread(enqueue_preview, snapshot.id)
    return SnapshotPublic.model_validate(_public_dict(snapshot))


@router.post("/{project_id}/snapshots/{snapshot_id}/session")
async def start_snapshot_session(
    project_id: UUID,
    snapshot_id: UUID,
    session: SessionDep,
    current_user: CurrentUserDep,
) -> dict[str, Any]:
    """Open one historical product version in an isolated interactive sandbox."""
    project = await _project_owned_by(session, project_id, current_user.id)
    snapshot = await session.get(Snapshot, snapshot_id)
    if snapshot is None or snapshot.project_id != project_id:
        raise ApiError("not_found", "snapshot not found", status.HTTP_404_NOT_FOUND)
    if project.template != "max_miniapp":
        raise ApiError(
            "conflict",
            "interactive history is available for MAX Mini Apps",
            status.HTTP_409_CONFLICT,
        )
    snapshot_files = await asyncio.to_thread(repo_svc.read_files, project_id, snapshot.commit_sha)
    incompatibility = max_legacy_snapshot_incompatibility(snapshot_files)
    if incompatibility:
        raise ApiError(
            "conflict",
            (
                "Эта старая версия использует серверную структуру, которую нельзя "
                "безопасно открыть в интерактивном просмотре."
            ),
            status.HTTP_409_CONFLICT,
        )
    record = await session.get(MaxProjectConfig, project_id)
    fallback_config = (
        MaxProjectConfigPayload.model_validate(record.config)
        if record is not None
        else default_max_project_config(project.name)
    )
    config = max_project_config_from_files(snapshot_files) or fallback_config
    files = render_max_history_files(
        snapshot_files,
        config,
        project_id,
    )
    return await orchestrator_client.start_history_preview_session(project_id, snapshot_id, files)


@router.delete(
    "/{project_id}/snapshots/{snapshot_id}/session",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def stop_snapshot_session(
    project_id: UUID,
    snapshot_id: UUID,
    session: SessionDep,
    current_user: CurrentUserDep,
    session_id: UUID = Query(),
) -> None:
    """Close a historical sandbox without changing the project's HEAD."""
    await _project_owned_by(session, project_id, current_user.id)
    snapshot = await session.get(Snapshot, snapshot_id)
    if snapshot is None or snapshot.project_id != project_id:
        raise ApiError("not_found", "snapshot not found", status.HTTP_404_NOT_FOUND)
    await orchestrator_client.stop_history_preview_session(project_id, snapshot_id, session_id)


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
