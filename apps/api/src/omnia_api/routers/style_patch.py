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
from sqlalchemy import select, text, update

from omnia_api.core.deps import CurrentUserDep, SessionDep
from omnia_api.core.errors import ApiError
from omnia_api.core.minio import preview_public_url
from omnia_api.core.redis import publish_event
from omnia_api.models.generation_run import GenerationRun
from omnia_api.models.project import Project
from omnia_api.models.snapshot import Snapshot
from omnia_api.routers.public import _INDEX_CANDIDATES
from omnia_api.schemas.snapshot import SnapshotPublic
from omnia_api.schemas.style_patch import StylePatchRequest
from omnia_api.services import orchestrator_client
from omnia_api.services import overrides as ov
from omnia_api.services import repo as repo_svc
from omnia_api.services.deployment_state import (
    current_snapshot_id_fresh,
    deployment_is_active,
)
from omnia_api.services.fonts import css_stack_for, href_for, is_known_family
from omnia_api.services.generation_runs import ACTIVE_GENERATION_STATUSES
from omnia_api.services.palette_guard import BANNED_HEXES
from omnia_api.services.queue import enqueue_preview
from omnia_api.services.runtime_sync import (
    mark_runtime_sync_required,
)

router = APIRouter(prefix="/api/projects", tags=["style-patch"])

# Container browser stacks render React rather than the snapshot's static
# index.html. Keep direct edits in the stylesheet each runtime actually imports.
_CONTAINER_STYLE_PATH = {
    "fullstack": "src/app/globals.css",
    "nextjs_entities": "src/app/globals.css",
    "realtime": "src/app/globals.css",
    "max_miniapp": "src/app/globals.css",
    "spa": "src/index.css",
}


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

    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:project_id))"),
        {"project_id": str(project_id)},
    )
    await session.refresh(project, with_for_update=True)
    if project.owner_id != current_user.id:
        raise ApiError("not_found", "project not found", status.HTTP_404_NOT_FOUND)
    active_generation = (
        await session.execute(
            select(GenerationRun.id).where(
                GenerationRun.project_id == project_id,
                GenerationRun.status.in_(ACTIVE_GENERATION_STATUSES),
            )
        )
    ).scalar_one_or_none()
    if active_generation is not None:
        raise ApiError(
            "conflict",
            "Дождитесь завершения или отмените текущую генерацию перед ручной правкой",
            status.HTTP_409_CONFLICT,
        )
    try:
        deployment = await orchestrator_client.get_deploy(project_id)
    except Exception as exc:
        raise ApiError(
            "deployment_state_unavailable",
            "Не удалось безопасно проверить публикацию проекта. Повторите позже.",
            status.HTTP_503_SERVICE_UNAVAILABLE,
        ) from exc
    if deployment_is_active(deployment):
        raise ApiError(
            "conflict",
            "Дождитесь завершения публикации перед ручной правкой",
            status.HTTP_409_CONFLICT,
        )

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
        raise ApiError("no_snapshot", "current snapshot missing", status.HTTP_400_BAD_REQUEST)
    expected_snapshot_id = project.current_snapshot_id
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
    is_container = project.template in _CONTAINER_STYLE_PATH
    if is_container:
        target_path = _CONTAINER_STYLE_PATH[project.template]
        # Container apps only commit AI-generated files to git; the fixed
        # globals.css lives in the image. Read the committed copy if a prior
        # edit already persisted it, else fetch the live one from the container.
        src = files.get(target_path)
        from_container = False
        if src is None:
            src = await orchestrator_client.read_container_file(
                project_id, project.slug, target_path
            )
            from_container = True
        if src is None:
            raise ApiError(
                "no_index",
                f"this app has no {target_path} to style-edit",
                status.HTTP_400_BAD_REQUEST,
            )
        new_content = ov.apply_css_overrides(src, tokens=tokens, element_rules=element_rules)
        # First container-sourced edit: there is no prior repo copy of
        # globals.css, so the unchanged-content guard below would never trip;
        # the merge always adds the managed block on a fresh read.
        if from_container and new_content == src:
            raise ApiError("empty_patch", "no effective changes", status.HTTP_400_BAD_REQUEST)
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
    # No-op guard. For a container app whose globals.css was just read live (not
    # in `files`), the fresh-read case is already handled above; `.get` keeps
    # this from KeyError-ing on that path.
    if new_content == files.get(target_path):
        raise ApiError("empty_patch", "no effective changes", status.HTTP_400_BAD_REQUEST)

    new_sha = await repo_svc.commit_files_async(
        project_id,
        {target_path: new_content},
        "style: прямое редактирование",
        parent_sha,
    )

    new_snapshot = Snapshot(
        project_id=project_id,
        commit_sha=new_sha,
        prompt_text="(прямое редактирование стиля)",
        model_id=None,
        parent_id=expected_snapshot_id,
    )
    session.add(new_snapshot)
    await session.flush()
    advanced = (
        await session.execute(
            update(Project)
            .where(
                Project.id == project_id,
                Project.current_snapshot_id == expected_snapshot_id,
            )
            .values(current_snapshot_id=new_snapshot.id)
            .returning(Project.id)
            .execution_options(synchronize_session="fetch")
        )
    ).scalar_one_or_none()
    if advanced is None:
        raise ApiError(
            "conflict",
            "Проект уже изменился; обновите страницу перед ручной правкой",
            status.HTTP_409_CONFLICT,
        )
    commit_confirmed_after_error = False
    if is_container:
        mark_runtime_sync_required(project, (target_path,))
    try:
        await session.commit()
    except Exception as exc:
        await session.rollback()
        try:
            canonical_snapshot_id = await current_snapshot_id_fresh(project_id)
        except Exception as state_exc:
            raise ApiError(
                "deployment_state_unavailable",
                "Не удалось подтвердить результат ручной правки. Повторите позже.",
                status.HTTP_503_SERVICE_UNAVAILABLE,
            ) from state_exc
        if canonical_snapshot_id == new_snapshot.id:
            commit_confirmed_after_error = True
        elif canonical_snapshot_id != expected_snapshot_id:
            raise ApiError(
                "conflict",
                "Проект уже изменился; обновите страницу перед ручной правкой",
                status.HTTP_409_CONFLICT,
            ) from exc
        if not commit_confirmed_after_error:
            if isinstance(exc, ApiError):
                raise
            raise ApiError(
                "orchestrator_unavailable",
                "Ручная правка не применена.",
                status.HTTP_503_SERVICE_UNAVAILABLE,
            ) from exc
    if commit_confirmed_after_error:
        refreshed = await session.get(Snapshot, new_snapshot.id)
        if refreshed is None:
            raise ApiError(
                "deployment_state_unavailable",
                "Правка сохранена, но её состояние пока нельзя подтвердить.",
                status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        new_snapshot = refreshed
    else:
        await session.refresh(new_snapshot)
    if is_container:
        try:
            await session.execute(
                text("SELECT pg_advisory_xact_lock(hashtext(:project_id))"),
                {"project_id": str(project_id)},
            )
            await session.refresh(project, with_for_update=True)
            if project.current_snapshot_id != new_snapshot.id:
                raise ApiError(
                    "conflict",
                    "Проект уже изменился; обновите страницу перед ручной правкой",
                    status.HTTP_409_CONFLICT,
                )
            await orchestrator_client.hot_reload_exact(
                project_id=project_id,
                slug=project.slug,
                files={target_path: new_content},
            )
            project.runtime_sync_required = False
            project.runtime_sync_paths = []
            await session.commit()
        except Exception as sync_exc:
            raise ApiError(
                "orchestrator_unavailable",
                "Правка сохранена; превью будет восстановлено перед следующим запуском.",
                status.HTTP_503_SERVICE_UNAVAILABLE,
            ) from sync_exc

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
                "parent_id": (str(new_snapshot.parent_id) if new_snapshot.parent_id else None),
                "preview_url": preview_public_url(new_snapshot.preview_key),
                "is_rollback_target": new_snapshot.is_rollback_target,
                "created_at": new_snapshot.created_at.isoformat(),
            }
        },
    )

    return SnapshotPublic.model_validate(_snapshot_dict(new_snapshot))
