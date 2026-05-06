import asyncio
from mimetypes import guess_type
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, Response, status
from sqlalchemy import select

from omnia_api.core.deps import SessionDep
from omnia_api.core.errors import ApiError
from omnia_api.models.project import Project
from omnia_api.models.snapshot import Snapshot
from omnia_api.services import repo as repo_svc

router = APIRouter(prefix="/p", tags=["public"], include_in_schema=False)


async def _resolve_snapshot(
    session: SessionDep,
    slug: str,
    snapshot_id: UUID | None = None,
) -> tuple[Project, Snapshot]:
    """Look up project by slug and return (project, snapshot).

    If `snapshot_id` is given, return that specific historical snapshot —
    but only if it belongs to the project. Otherwise return HEAD.
    """
    res = await session.execute(select(Project).where(Project.slug == slug))
    project = res.scalar_one_or_none()
    if project is None:
        raise ApiError("not_found", "project not found", status.HTTP_404_NOT_FOUND)

    target_id = snapshot_id if snapshot_id is not None else project.current_snapshot_id
    if target_id is None:
        raise ApiError("not_found", "snapshot not found", status.HTTP_404_NOT_FOUND)

    snapshot = await session.get(Snapshot, target_id)
    if snapshot is None or snapshot.project_id != project.id:
        raise ApiError("not_found", "snapshot not found", status.HTTP_404_NOT_FOUND)
    return project, snapshot


async def _serve_file(project: Project, snapshot: Snapshot, path: str) -> Response:
    content = await asyncio.to_thread(
        repo_svc.read_file, project.id, snapshot.commit_sha, path
    )
    if content is None:
        raise ApiError("not_found", f"file {path} not found", status.HTTP_404_NOT_FOUND)
    mime, _ = guess_type(path)
    headers = {
        # Allow the workspace iframe (same origin) to embed this preview.
        "X-Frame-Options": "SAMEORIGIN",
        # Don't let stale HEADs linger — every navigation re-fetches.
        "Cache-Control": "no-cache",
    }
    return Response(
        content=content,
        media_type=mime or "application/octet-stream",
        headers=headers,
    )


@router.get("/{slug}", response_class=Response)
async def get_index(
    slug: str,
    session: SessionDep,
    snapshot: Annotated[UUID | None, Query()] = None,
) -> Response:
    project, snap = await _resolve_snapshot(session, slug, snapshot)
    return await _serve_file(project, snap, "index.html")


@router.get("/{slug}/{file_path:path}", response_class=Response)
async def get_file(
    slug: str,
    file_path: str,
    session: SessionDep,
    snapshot: Annotated[UUID | None, Query()] = None,
) -> Response:
    project, snap = await _resolve_snapshot(session, slug, snapshot)
    return await _serve_file(project, snap, file_path)
