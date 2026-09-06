import asyncio
import re
from collections.abc import Iterator
from typing import Any
from uuid import UUID

from fastapi import APIRouter, status
from fastapi.responses import StreamingResponse
from minio.error import S3Error
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from omnia_api.core.config import get_settings
from omnia_api.core.deps import CurrentUserDep, SessionDep
from omnia_api.core.errors import ApiError
from omnia_api.core.minio import get_minio_client, preview_public_url
from omnia_api.models.project import Project
from omnia_api.models.snapshot import Snapshot
from omnia_api.schemas.snapshot import SnapshotPublic, SnapshotWithFiles
from omnia_api.services import repo as repo_svc

router = APIRouter(prefix="/api/projects", tags=["snapshots"])


async def _project_owned_by(
    session: AsyncSession, project_id: UUID, user_id: UUID
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


@router.get("/{project_id}/snapshots/{snapshot_id}/previews/{image_index}")
async def get_snapshot_preview_image(
    project_id: UUID,
    snapshot_id: UUID,
    image_index: int,
    session: SessionDep,
    current_user: CurrentUserDep,
    v: str | None = None,
) -> StreamingResponse:
    """Read a private, source-verified image. Viewing never starts rendering."""
    await _project_owned_by(session, project_id, current_user.id)
    snapshot = await session.get(Snapshot, snapshot_id)
    if (
        snapshot is None or snapshot.project_id != project_id
        or snapshot.preview_status != "ready"
        or snapshot.preview_commit_sha != snapshot.commit_sha
        or not 0 <= image_index < len(snapshot.preview_manifest)
    ):
        raise ApiError("not_found", "version image not found", status.HTTP_404_NOT_FOUND)
    item = snapshot.preview_manifest[image_index]
    key = item.get("key") if isinstance(item, dict) else None
    prefix = f"snapshot-previews/{project_id}/{snapshot_id}/"
    if not isinstance(key, str):
        raise ApiError("not_found", "version image not found", status.HTTP_404_NOT_FOUND)
    match = re.fullmatch(re.escape(prefix) + r"([0-9a-f]{64})\.png", key)
    if match is None or (v is not None and v != match[1]):
        raise ApiError("not_found", "version image not found", status.HTTP_404_NOT_FOUND)
    try:
        stream = await asyncio.to_thread(
            get_minio_client().get_object, get_settings().minio_bucket_projects, key,
        )
    except S3Error as exc:
        if exc.code in {"NoSuchKey", "NoSuchBucket"}:
            raise ApiError("not_found", "version image not found", 404) from exc
        raise ApiError("internal_error", "Не удалось загрузить изображение версии.", 503) from exc

    def chunks() -> Iterator[bytes]:
        try:
            while data := stream.read(64 * 1024):
                yield data
        finally:
            stream.close()
            stream.release_conn()

    return StreamingResponse(
        chunks(), media_type="image/png",
        headers={
            "Cache-Control": "private, max-age=31536000, immutable" if v else "private, no-cache",
            "X-Content-Type-Options": "nosniff",
            "Referrer-Policy": "no-referrer",
        },
    )
