import asyncio
import re
from collections.abc import Iterator
from uuid import UUID

from fastapi import APIRouter, Response, status
from fastapi.responses import StreamingResponse
from minio.error import S3Error
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from yleum_api.core.config import get_settings
from yleum_api.core.deps import CurrentUserDep, SessionDep
from yleum_api.core.errors import ApiError
from yleum_api.core.minio import get_minio_client
from yleum_api.models.generation_run import GenerationRun
from yleum_api.models.message import Message
from yleum_api.models.project import Project
from yleum_api.models.snapshot import Snapshot
from yleum_api.schemas.snapshot import SnapshotChangesPublic, SnapshotPublic, snapshot_public_dict
from yleum_api.services import repo
from yleum_api.services.generation.version_changes import summarize_version_changes
from yleum_api.services.max_source_workers import run_max_source_read

router = APIRouter(prefix="/api/projects", tags=["snapshots"])

_public_dict = snapshot_public_dict


async def _project_owned_by(
    session: AsyncSession, project_id: UUID, user_id: UUID
) -> Project:
    project = await session.get(Project, project_id)
    if project is None or project.owner_id != user_id:
        raise ApiError("not_found", "project not found", status.HTTP_404_NOT_FOUND)
    return project


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


@router.get("/{project_id}/snapshots/{snapshot_id}", response_model=SnapshotPublic)
async def get_snapshot(
    project_id: UUID,
    snapshot_id: UUID,
    session: SessionDep,
    current_user: CurrentUserDep,
) -> SnapshotPublic:
    """Owner-scoped version metadata; source files remain platform-internal."""
    await _project_owned_by(session, project_id, current_user.id)
    snapshot = await session.get(Snapshot, snapshot_id)
    if snapshot is None or snapshot.project_id != project_id:
        raise ApiError("not_found", "snapshot not found", status.HTTP_404_NOT_FOUND)
    return SnapshotPublic.model_validate(_public_dict(snapshot))


def _summarize_snapshot_changes(
    project_id: UUID, commit_sha: str, parent_sha: str | None,
) -> list[str] | None:
    pair = repo.read_commit_files_with_parent(
        project_id, commit_sha, expected_parent_sha=parent_sha,
    )
    return summarize_version_changes(*pair) if pair is not None else None


@router.get("/{project_id}/snapshots/{snapshot_id}/changes", response_model=SnapshotChangesPublic)
async def get_snapshot_changes(
    project_id: UUID,
    snapshot_id: UUID,
    response: Response,
    session: SessionDep,
    current_user: CurrentUserDep,
) -> SnapshotChangesPublic:
    """Describe the saved source change for the owner's successful MAX turn."""
    unavailable = ApiError("not_found", "Отчёт о версии недоступен.", status.HTTP_404_NOT_FOUND)
    parent = aliased(Snapshot)
    latest_run_status = (
        select(GenerationRun.status)
        .where(
            GenerationRun.assistant_message_id == Message.id,
            GenerationRun.project_id == project_id,
        )
        .order_by(GenerationRun.created_at.desc(), GenerationRun.id.desc())
        .limit(1)
        .scalar_subquery()
    )
    generation = (
        select(Message.id)
        .where(
            Message.project_id == project_id,
            Message.snapshot_id == snapshot_id,
            Message.role == "assistant",
            Message.chat_question.is_(False),
            latest_run_status == "completed",
        )
        .exists()
    )
    result = await session.execute(
        select(Snapshot.commit_sha, Snapshot.parent_id, parent.commit_sha)
        .select_from(Snapshot)
        .join(Project, Project.id == Snapshot.project_id)
        .outerjoin(parent, (parent.id == Snapshot.parent_id) & (parent.project_id == project_id))
        .where(
            Snapshot.id == snapshot_id,
            Snapshot.project_id == project_id,
            Project.owner_id == current_user.id,
            Project.template == "max_miniapp",
            generation,
        )
    )
    version = result.one_or_none()
    if version is None:
        raise unavailable
    commit_sha, parent_id, parent_sha = version
    if parent_id is not None and parent_sha is None:
        raise unavailable

    try:
        changes = await run_max_source_read(
            lambda: _summarize_snapshot_changes(project_id, commit_sha, parent_sha),
            historical=True,
        )
    except Exception as exc:
        raise ApiError(
            "unavailable",
            "Не удалось загрузить отчёт о версии.",
            status.HTTP_503_SERVICE_UNAVAILABLE,
        ) from exc
    if changes is None:
        raise unavailable
    response.headers["Cache-Control"] = "private, no-store"
    return SnapshotChangesPublic(changes=changes)


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
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
            "Referrer-Policy": "no-referrer",
        },
    )
