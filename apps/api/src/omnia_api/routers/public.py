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


_HTML_MIMES = {"text/html", "application/xhtml+xml"}

# Locked-down CSP for AI-generated content. Key guarantees:
# - `connect-src 'none'`     — generated JS can't fetch/XHR/WS attacker.com (SSRF/exfil shield).
# - `frame-ancestors 'self'` — only our workspace iframe can embed previews.
# - `script-src 'unsafe-inline'` is unavoidable: the model writes JS inline into HTML.
#   The DOM is still sandboxed from the parent (different docs, can't reach our cookies).
# - `style-src 'unsafe-inline'` — same reason for CSS.
# - Google Fonts whitelisted because LLMs reach for it constantly.
_CSP = (
    "default-src 'self'; "
    "img-src 'self' data: blob: https:; "
    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
    "font-src 'self' data: https://fonts.gstatic.com; "
    "script-src 'self' 'unsafe-inline'; "
    "connect-src 'none'; "
    "frame-ancestors 'self'; "
    "base-uri 'self'; "
    "form-action 'self'"
)

_SECURITY_HEADERS = {
    "X-Frame-Options": "SAMEORIGIN",
    "Cache-Control": "no-cache",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "geolocation=(), microphone=(), camera=(), payment=(), usb=()",
}


async def _serve_file(project: Project, snapshot: Snapshot, path: str) -> Response:
    content = await asyncio.to_thread(
        repo_svc.read_file, project.id, snapshot.commit_sha, path
    )
    if content is None:
        raise ApiError("not_found", f"file {path} not found", status.HTTP_404_NOT_FOUND)
    mime, _ = guess_type(path)
    headers = dict(_SECURITY_HEADERS)
    # CSP only applies meaningfully to HTML documents; assets (CSS, JS, images)
    # don't need it and adding it bloats responses with no security gain.
    if (mime or "").lower() in _HTML_MIMES:
        headers["Content-Security-Policy"] = _CSP
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
