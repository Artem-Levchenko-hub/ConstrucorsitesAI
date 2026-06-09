"""Direct style-patch endpoint — applies an in-preview color/font edit as a
snapshot WITHOUT the LLM. Mirrors ``rollback.py``'s commit→snapshot→event flow.

The user's edits live in a managed ``<style id="omnia-overrides">`` block (see
``services/overrides.py``). Generation guards are intentionally skipped (the
override is authoritative), but banned generic-AI hexes and unknown font families
are rejected at this boundary, so the no-generic-color / known-fonts invariants
hold.
"""

from __future__ import annotations

import asyncio
from uuid import UUID

from fastapi import APIRouter, status

from omnia_api.core.deps import CurrentUserDep, SessionDep
from omnia_api.core.errors import ApiError
from omnia_api.core.minio import preview_public_url
from omnia_api.core.redis import publish_event
from omnia_api.models.project import Project
from omnia_api.models.snapshot import Snapshot
from omnia_api.routers.public import _INDEX_CANDIDATES
from omnia_api.schemas.snapshot import SnapshotPublic
from omnia_api.schemas.style_patch import StylePatchRequest
from omnia_api.services import orchestrator_client
from omnia_api.services import overrides as ov
from omnia_api.services import repo as repo_svc
from omnia_api.services.fonts import css_stack_for, href_for, is_known_family
from omnia_api.services.palette_guard import BANNED_HEXES
from omnia_api.services.queue import enqueue_preview

router = APIRouter(prefix="/api/projects", tags=["style-patch"])

# Container-backed Next.js templates render React, not a static index.html, so
# their direct-style edits persist in ``globals.css`` (mirrors messages.py).
_CONTAINER_NEXT = ("fullstack", "nextjs_entities")


def _expand_hex(h: str) -> str:
    h = h.lower()
    if len(h) == 4:  # #rgb → #rrggbb
        return "#" + "".join(c * 2 for c in h[1:])
    return h


def _is_banned(h: str) -> bool:
    return _expand_hex(h) in BANNED_HEXES


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


@router.post("/{project_id}/style-patch", response_model=SnapshotPublic)
async def post_style_patch(
    project_id: UUID,
    payload: StylePatchRequest,
    session: SessionDep,
    current_user: CurrentUserDep,
) -> SnapshotPublic:
    project = await session.get(Project, project_id)
    if project is None or project.owner_id != current_user.id:
        raise ApiError("not_found", "project not found", status.HTTP_404_NOT_FOUND)

    if not payload.tokens and not payload.elements:
        raise ApiError("empty_patch", "no changes provided", status.HTTP_400_BAD_REQUEST)

    # Reject generic-AI hexes (mirror palette_guard's invariant) + unknown fonts.
    colors = [t.value for t in payload.tokens]
    for e in payload.elements:
        colors += [c for c in (e.color, e.background_color, e.border_color) if c]
    for c in colors:
        if _is_banned(c):
            raise ApiError(
                "banned_color",
                f"{c} is a generic-AI color — pick another shade",
                status.HTTP_400_BAD_REQUEST,
            )
    for e in payload.elements:
        if e.font_family and not is_known_family(e.font_family):
            raise ApiError(
                "invalid_font",
                f"unknown font family: {e.font_family}",
                status.HTTP_400_BAD_REQUEST,
            )

    if project.current_snapshot_id is None:
        raise ApiError(
            "no_snapshot", "project has no snapshot to edit", status.HTTP_400_BAD_REQUEST
        )
    current = await session.get(Snapshot, project.current_snapshot_id)
    if current is None:
        raise ApiError(
            "no_snapshot", "current snapshot missing", status.HTTP_400_BAD_REQUEST
        )
    parent_sha = current.commit_sha

    files = await asyncio.to_thread(repo_svc.read_files, project_id, parent_sha)

    tokens = [(t.var, t.value) for t in payload.tokens]
    element_rules: list[tuple[str, dict[str, str]]] = []
    font_links: list[tuple[str, str]] = []
    for e in payload.elements:
        decls: dict[str, str] = {}
        if e.color:
            decls["color"] = e.color
        if e.background_color:
            decls["background-color"] = e.background_color
        if e.border_color:
            decls["border-color"] = e.border_color
        if e.font_family:
            stack = css_stack_for(e.font_family)
            if stack:
                decls["font-family"] = stack
            href = href_for(e.font_family)
            if href:
                font_links.append((e.font_family, href))
        if e.hidden:
            # "Remove element" = hide it (display:none !important via the
            # overrides block). Reversible, selector-targeted, no HTML surgery.
            decls["display"] = "none"
        if decls:
            element_rules.append((e.selector, decls))

    # Two persistence targets. Static (V1) apps own a real ``index.html`` whose
    # ``<head>`` carries the managed ``<style>`` block. Container apps (Next.js)
    # render React — no index.html — so the same edits go into a managed block
    # appended to the already-imported ``src/app/globals.css`` and are pushed
    # into the live dev container via hot-reload below.
    is_container = project.template in _CONTAINER_NEXT
    if is_container:
        target_path = "src/app/globals.css"
        src = files.get(target_path)
        if src is None:
            raise ApiError(
                "no_index",
                "this app has no globals.css to style-edit",
                status.HTTP_400_BAD_REQUEST,
            )
        new_content = ov.apply_css_overrides(
            src, tokens=tokens, element_rules=element_rules
        )
    else:
        index_path = next((c for c in _INDEX_CANDIDATES if c in files), None)
        if index_path is None:
            raise ApiError(
                "no_index",
                "this project has no static index.html to style-edit",
                status.HTTP_400_BAD_REQUEST,
            )
        target_path = index_path
        new_content = ov.apply_overrides(
            files[index_path],
            tokens=tokens,
            element_rules=element_rules,
            font_links=font_links,
        )
    if new_content == files[target_path]:
        raise ApiError(
            "empty_patch", "no effective changes", status.HTTP_400_BAD_REQUEST
        )

    new_sha = await asyncio.to_thread(
        repo_svc.commit_files,
        project_id,
        {target_path: new_content},
        "style: прямое редактирование",
        parent_sha,
    )

    # Container apps: push the edited globals.css into the live dev container so
    # the change shows immediately via HMR (parity with the build path). Best-
    # effort (R-10): the canonical state is already committed to git, and the
    # snapshot is created regardless, so a momentarily-down orchestrator only
    # delays the live preview, never loses the edit.
    if is_container:
        try:
            await orchestrator_client.hot_reload(
                project_id=project_id,
                slug=project.slug,
                files={target_path: new_content},
            )
        except Exception:
            # Preview refresh must never block save; edit is already committed.
            pass

    new_snapshot = Snapshot(
        project_id=project_id,
        commit_sha=new_sha,
        prompt_text="(прямое редактирование стиля)",
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
