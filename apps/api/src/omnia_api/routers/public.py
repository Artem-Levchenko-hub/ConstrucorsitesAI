import asyncio
from mimetypes import guess_type

from fastapi import APIRouter, Response, status
from sqlalchemy import select

from omnia_api.core.deps import SessionDep
from omnia_api.core.errors import ApiError
from omnia_api.models.project import Project
from omnia_api.models.snapshot import Snapshot
from omnia_api.services import repo as repo_svc

router = APIRouter(prefix="/p", tags=["public"], include_in_schema=False)


async def _resolve_snapshot(session: SessionDep, slug: str) -> tuple[Project, Snapshot]:
    res = await session.execute(select(Project).where(Project.slug == slug))
    project = res.scalar_one_or_none()
    if project is None or project.current_snapshot_id is None:
        raise ApiError("not_found", "project not found", status.HTTP_404_NOT_FOUND)
    snapshot = await session.get(Snapshot, project.current_snapshot_id)
    if snapshot is None:
        raise ApiError("not_found", "snapshot not found", status.HTTP_404_NOT_FOUND)
    return project, snapshot


async def _serve_file(project: Project, snapshot: Snapshot, path: str) -> Response:
    content = await asyncio.to_thread(
        repo_svc.read_file, project.id, snapshot.commit_sha, path
    )
    if content is None:
        raise ApiError("not_found", f"file {path} not found", status.HTTP_404_NOT_FOUND)
    mime, _ = guess_type(path)
    return Response(content=content, media_type=mime or "application/octet-stream")


@router.get("/{slug}", response_class=Response)
async def get_index(slug: str, session: SessionDep) -> Response:
    project, snapshot = await _resolve_snapshot(session, slug)
    return await _serve_file(project, snapshot, "index.html")


@router.get("/{slug}/{file_path:path}", response_class=Response)
async def get_file(slug: str, file_path: str, session: SessionDep) -> Response:
    project, snapshot = await _resolve_snapshot(session, slug)
    return await _serve_file(project, snapshot, file_path)
