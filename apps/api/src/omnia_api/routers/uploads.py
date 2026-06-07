"""User image upload + in-preview image replacement — NO LLM, free.

* ``POST /api/projects/{id}/uploads`` — raw image bytes in the request body
  (frontend sends the File/Blob directly; avoids the python-multipart dep).
  Sanitised + stored in MinIO → ``{"url": ...}``.
* ``POST /api/projects/{id}/image-patch`` — swap a generated ``<img src>`` for an
  uploaded one and commit a snapshot. Mirrors ``style_patch.py``'s
  commit → snapshot → preview → event flow so the timeline/rollback work.
"""

from __future__ import annotations

import asyncio
import html as _html
import re
from uuid import UUID

from fastapi import APIRouter, Request, status

from omnia_api.core.config import get_settings
from omnia_api.core.deps import CurrentUserDep, SessionDep
from omnia_api.core.errors import ApiError
from omnia_api.core.minio import preview_public_url
from omnia_api.core.redis import publish_event
from omnia_api.models.project import Project
from omnia_api.models.snapshot import Snapshot
from omnia_api.routers.public import _INDEX_CANDIDATES
from omnia_api.schemas.snapshot import SnapshotPublic
from omnia_api.schemas.upload import (
    ElementDeleteRequest,
    ElementMoveRequest,
    ImagePatchRequest,
    TextPatchRequest,
)
from omnia_api.services import repo as repo_svc
from omnia_api.services import user_uploads
from omnia_api.services.queue import enqueue_preview

router = APIRouter(prefix="/api/projects", tags=["uploads"])

# Hard cap on the raw request body we'll read into memory (matches the service
# ceiling). Prevents a giant body from ballooning the worker before validation.
_MAX_UPLOAD_BYTES = 6 * 1024 * 1024


async def _owned_project(session: SessionDep, project_id: UUID, user_id: UUID) -> Project:
    project = await session.get(Project, project_id)
    if project is None or project.owner_id != user_id:
        raise ApiError("not_found", "project not found", status.HTTP_404_NOT_FOUND)
    return project


def _snapshot_dict(s: Snapshot) -> dict[str, object]:
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


@router.post("/{project_id}/uploads")
async def upload_image(
    project_id: UUID,
    request: Request,
    session: SessionDep,
    current_user: CurrentUserDep,
) -> dict[str, str]:
    """Store a user-uploaded image. Body = raw image bytes. Returns its URL."""
    await _owned_project(session, project_id, current_user.id)
    raw = await request.body()
    if len(raw) > _MAX_UPLOAD_BYTES:
        raise ApiError(
            "too_large", "файл слишком большой (макс. 6 МБ)",
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
        )
    try:
        url = await asyncio.to_thread(
            user_uploads.sanitize_and_upload, raw, str(project_id)
        )
    except user_uploads.UploadRejected as exc:
        raise ApiError("bad_image", str(exc), status.HTTP_400_BAD_REQUEST) from exc
    except Exception as exc:  # noqa: BLE001
        raise ApiError(
            "upload_failed", "не удалось сохранить изображение",
            status.HTTP_502_BAD_GATEWAY,
        ) from exc
    return {"url": url}


@router.post("/{project_id}/image-patch", response_model=SnapshotPublic)
async def image_patch(
    project_id: UUID,
    payload: ImagePatchRequest,
    session: SessionDep,
    current_user: CurrentUserDep,
) -> SnapshotPublic:
    """Replace one image's ``src`` with an uploaded asset; commit a snapshot."""
    project = await _owned_project(session, project_id, current_user.id)

    # Security: only allow swapping IN one of our own MinIO assets (an uploaded
    # or generated image) — never an arbitrary external/script URL into the page.
    base = get_settings().minio_public_url.rstrip("/")
    if not payload.new_src.startswith(base):
        raise ApiError(
            "bad_src", "картинка должна быть загруженным ассетом",
            status.HTTP_400_BAD_REQUEST,
        )
    if payload.old_src == payload.new_src:
        raise ApiError("empty_patch", "no change", status.HTTP_400_BAD_REQUEST)

    if project.current_snapshot_id is None:
        raise ApiError(
            "no_snapshot", "project has no snapshot to edit",
            status.HTTP_400_BAD_REQUEST,
        )
    current = await session.get(Snapshot, project.current_snapshot_id)
    if current is None:
        raise ApiError(
            "no_snapshot", "current snapshot missing", status.HTTP_400_BAD_REQUEST
        )
    parent_sha = current.commit_sha

    files = await asyncio.to_thread(repo_svc.read_files, project_id, parent_sha)
    index_path = next((c for c in _INDEX_CANDIDATES if c in files), None)
    if index_path is None:
        raise ApiError(
            "no_index", "this project has no static index.html to edit",
            status.HTTP_400_BAD_REQUEST,
        )

    html = files[index_path]
    if payload.old_src not in html:
        raise ApiError(
            "src_not_found", "эта картинка не найдена на странице",
            status.HTTP_400_BAD_REQUEST,
        )
    new_html = html.replace(payload.old_src, payload.new_src)
    if new_html == html:
        raise ApiError("empty_patch", "no effective change", status.HTTP_400_BAD_REQUEST)

    new_sha = await asyncio.to_thread(
        repo_svc.commit_files,
        project_id,
        {index_path: new_html},
        "image: своя картинка",
        parent_sha,
    )

    new_snapshot = Snapshot(
        project_id=project_id,
        commit_sha=new_sha,
        prompt_text="(своя картинка)",
        model_id=None,
        parent_id=project.current_snapshot_id,
    )
    session.add(new_snapshot)
    await session.flush()
    project.current_snapshot_id = new_snapshot.id
    await session.commit()
    await session.refresh(new_snapshot)

    await asyncio.to_thread(enqueue_preview, new_snapshot.id)

    await publish_event(
        project_id,
        "snapshot.created",
        {
            "snapshot": {
                "id": str(new_snapshot.id),
                "project_id": str(new_snapshot.project_id),
                "commit_sha": new_snapshot.commit_sha,
                "prompt_text": new_snapshot.prompt_text,
                "model_id": new_snapshot.model_id,
                "parent_id": (
                    str(new_snapshot.parent_id) if new_snapshot.parent_id else None
                ),
                "preview_url": preview_public_url(new_snapshot.preview_key),
                "is_rollback_target": new_snapshot.is_rollback_target,
                "created_at": new_snapshot.created_at.isoformat(),
            }
        },
    )

    return SnapshotPublic.model_validate(_snapshot_dict(new_snapshot))


@router.post("/{project_id}/text-patch", response_model=SnapshotPublic)
async def text_patch(
    project_id: UUID,
    payload: TextPatchRequest,
    session: SessionDep,
    current_user: CurrentUserDep,
) -> SnapshotPublic:
    """Replace a text element's content directly (no LLM); commit a snapshot."""
    project = await _owned_project(session, project_id, current_user.id)

    if payload.old_text == payload.new_text:
        raise ApiError("empty_patch", "no change", status.HTTP_400_BAD_REQUEST)
    if project.current_snapshot_id is None:
        raise ApiError(
            "no_snapshot", "project has no snapshot to edit",
            status.HTTP_400_BAD_REQUEST,
        )
    current = await session.get(Snapshot, project.current_snapshot_id)
    if current is None:
        raise ApiError(
            "no_snapshot", "current snapshot missing", status.HTTP_400_BAD_REQUEST
        )
    parent_sha = current.commit_sha

    files = await asyncio.to_thread(repo_svc.read_files, project_id, parent_sha)
    index_path = next((c for c in _INDEX_CANDIDATES if c in files), None)
    if index_path is None:
        raise ApiError(
            "no_index", "this project has no static index.html to edit",
            status.HTTP_400_BAD_REQUEST,
        )

    html_src = files[index_path]
    # Match the text ONLY where it is the FULL content of an element (between a
    # `>` and the next `<`), whitespace-tolerant — never touches attribute values
    # or script strings. The frontend offers this only for pure-text elements, so
    # the occurrence index stays 1:1 with what the user sees.
    pattern = re.compile(r"(>)(\s*)" + re.escape(payload.old_text) + r"(\s*)(<)")
    matches = list(pattern.finditer(html_src))
    if not matches:
        raise ApiError(
            "text_not_found", "не нашёл этот текст на странице",
            status.HTTP_400_BAD_REQUEST,
        )
    idx = payload.index if payload.index < len(matches) else 0
    m = matches[idx]
    esc_new = _html.escape(payload.new_text, quote=False)
    new_html = (
        html_src[: m.start()]
        + m.group(1) + m.group(2) + esc_new + m.group(3) + m.group(4)
        + html_src[m.end() :]
    )
    if new_html == html_src:
        raise ApiError("empty_patch", "no effective change", status.HTTP_400_BAD_REQUEST)

    new_sha = await asyncio.to_thread(
        repo_svc.commit_files,
        project_id,
        {index_path: new_html},
        "text: правка текста",
        parent_sha,
    )
    new_snapshot = Snapshot(
        project_id=project_id,
        commit_sha=new_sha,
        prompt_text="(правка текста)",
        model_id=None,
        parent_id=project.current_snapshot_id,
    )
    session.add(new_snapshot)
    await session.flush()
    project.current_snapshot_id = new_snapshot.id
    await session.commit()
    await session.refresh(new_snapshot)

    await asyncio.to_thread(enqueue_preview, new_snapshot.id)

    await publish_event(
        project_id,
        "snapshot.created",
        {
            "snapshot": {
                "id": str(new_snapshot.id),
                "project_id": str(new_snapshot.project_id),
                "commit_sha": new_snapshot.commit_sha,
                "prompt_text": new_snapshot.prompt_text,
                "model_id": new_snapshot.model_id,
                "parent_id": (
                    str(new_snapshot.parent_id) if new_snapshot.parent_id else None
                ),
                "preview_url": preview_public_url(new_snapshot.preview_key),
                "is_rollback_target": new_snapshot.is_rollback_target,
                "created_at": new_snapshot.created_at.isoformat(),
            }
        },
    )

    return SnapshotPublic.model_validate(_snapshot_dict(new_snapshot))


@router.post("/{project_id}/element-delete", response_model=SnapshotPublic)
async def element_delete(
    project_id: UUID,
    payload: ElementDeleteRequest,
    session: SessionDep,
    current_user: CurrentUserDep,
) -> SnapshotPublic:
    """HARD delete — cut the element's exact source HTML out of index.html."""
    project = await _owned_project(session, project_id, current_user.id)

    if project.current_snapshot_id is None:
        raise ApiError(
            "no_snapshot", "project has no snapshot to edit",
            status.HTTP_400_BAD_REQUEST,
        )
    current = await session.get(Snapshot, project.current_snapshot_id)
    if current is None:
        raise ApiError(
            "no_snapshot", "current snapshot missing", status.HTTP_400_BAD_REQUEST
        )
    parent_sha = current.commit_sha

    files = await asyncio.to_thread(repo_svc.read_files, project_id, parent_sha)
    index_path = next((c for c in _INDEX_CANDIDATES if c in files), None)
    if index_path is None:
        raise ApiError(
            "no_index", "this project has no static index.html to edit",
            status.HTTP_400_BAD_REQUEST,
        )

    html_src = files[index_path]
    oh = payload.outer_html
    # Cut the requested exact occurrence of the element's source HTML.
    positions: list[int] = []
    i = html_src.find(oh)
    while i != -1:
        positions.append(i)
        i = html_src.find(oh, i + len(oh))
    if not positions:
        raise ApiError(
            "element_not_found",
            "не нашёл элемент в коде — попробуй «Убрать элемент»",
            status.HTTP_400_BAD_REQUEST,
        )
    idx = payload.index if payload.index < len(positions) else 0
    start = positions[idx]
    new_html = html_src[:start] + html_src[start + len(oh) :]
    if new_html == html_src:
        raise ApiError("empty_patch", "no effective change", status.HTTP_400_BAD_REQUEST)

    new_sha = await asyncio.to_thread(
        repo_svc.commit_files,
        project_id,
        {index_path: new_html},
        "element: жёсткое удаление",
        parent_sha,
    )
    new_snapshot = Snapshot(
        project_id=project_id,
        commit_sha=new_sha,
        prompt_text="(удаление элемента)",
        model_id=None,
        parent_id=project.current_snapshot_id,
    )
    session.add(new_snapshot)
    await session.flush()
    project.current_snapshot_id = new_snapshot.id
    await session.commit()
    await session.refresh(new_snapshot)

    await asyncio.to_thread(enqueue_preview, new_snapshot.id)

    await publish_event(
        project_id,
        "snapshot.created",
        {
            "snapshot": {
                "id": str(new_snapshot.id),
                "project_id": str(new_snapshot.project_id),
                "commit_sha": new_snapshot.commit_sha,
                "prompt_text": new_snapshot.prompt_text,
                "model_id": new_snapshot.model_id,
                "parent_id": (
                    str(new_snapshot.parent_id) if new_snapshot.parent_id else None
                ),
                "preview_url": preview_public_url(new_snapshot.preview_key),
                "is_rollback_target": new_snapshot.is_rollback_target,
                "created_at": new_snapshot.created_at.isoformat(),
            }
        },
    )

    return SnapshotPublic.model_validate(_snapshot_dict(new_snapshot))


@router.post("/{project_id}/element-move", response_model=SnapshotPublic)
async def element_move(
    project_id: UUID,
    payload: ElementMoveRequest,
    session: SessionDep,
    current_user: CurrentUserDep,
) -> SnapshotPublic:
    """Move up/down — swap two elements' exact source HTML in index.html."""
    project = await _owned_project(session, project_id, current_user.id)

    if project.current_snapshot_id is None:
        raise ApiError(
            "no_snapshot", "project has no snapshot to edit",
            status.HTTP_400_BAD_REQUEST,
        )
    current = await session.get(Snapshot, project.current_snapshot_id)
    if current is None:
        raise ApiError(
            "no_snapshot", "current snapshot missing", status.HTTP_400_BAD_REQUEST
        )
    parent_sha = current.commit_sha

    files = await asyncio.to_thread(repo_svc.read_files, project_id, parent_sha)
    index_path = next((c for c in _INDEX_CANDIDATES if c in files), None)
    if index_path is None:
        raise ApiError(
            "no_index", "this project has no static index.html to edit",
            status.HTTP_400_BAD_REQUEST,
        )

    html_src = files[index_path]

    def _nth(sub: str, n: int) -> int:
        last = 0
        pos = -1
        for _ in range(n + 1):
            pos = html_src.find(sub, last)
            if pos == -1:
                return -1
            last = pos + len(sub)
        return pos

    pa = _nth(payload.a_html, payload.a_index)
    pb = _nth(payload.b_html, payload.b_index)
    if pa == -1 or pb == -1:
        raise ApiError(
            "element_not_found",
            "не нашёл элементы для перемещения — обнови превью",
            status.HTTP_400_BAD_REQUEST,
        )
    if pa == pb:
        raise ApiError("empty_patch", "no change", status.HTTP_400_BAD_REQUEST)
    # Order by source position; swap the two blocks, keeping what's between them.
    if pa < pb:
        lo, lo_h, hi, hi_h = pa, payload.a_html, pb, payload.b_html
    else:
        lo, lo_h, hi, hi_h = pb, payload.b_html, pa, payload.a_html
    if lo + len(lo_h) > hi:
        raise ApiError(
            "overlap", "элементы вложены — переместить нельзя",
            status.HTTP_400_BAD_REQUEST,
        )
    mid = html_src[lo + len(lo_h) : hi]
    new_html = html_src[:lo] + hi_h + mid + lo_h + html_src[hi + len(hi_h) :]
    if new_html == html_src:
        raise ApiError("empty_patch", "no effective change", status.HTTP_400_BAD_REQUEST)

    new_sha = await asyncio.to_thread(
        repo_svc.commit_files,
        project_id,
        {index_path: new_html},
        "element: перемещение",
        parent_sha,
    )
    new_snapshot = Snapshot(
        project_id=project_id,
        commit_sha=new_sha,
        prompt_text="(перемещение элемента)",
        model_id=None,
        parent_id=project.current_snapshot_id,
    )
    session.add(new_snapshot)
    await session.flush()
    project.current_snapshot_id = new_snapshot.id
    await session.commit()
    await session.refresh(new_snapshot)

    await asyncio.to_thread(enqueue_preview, new_snapshot.id)

    await publish_event(
        project_id,
        "snapshot.created",
        {
            "snapshot": {
                "id": str(new_snapshot.id),
                "project_id": str(new_snapshot.project_id),
                "commit_sha": new_snapshot.commit_sha,
                "prompt_text": new_snapshot.prompt_text,
                "model_id": new_snapshot.model_id,
                "parent_id": (
                    str(new_snapshot.parent_id) if new_snapshot.parent_id else None
                ),
                "preview_url": preview_public_url(new_snapshot.preview_key),
                "is_rollback_target": new_snapshot.is_rollback_target,
                "created_at": new_snapshot.created_at.isoformat(),
            }
        },
    )

    return SnapshotPublic.model_validate(_snapshot_dict(new_snapshot))
