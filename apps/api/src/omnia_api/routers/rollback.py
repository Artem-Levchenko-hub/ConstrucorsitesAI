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
from omnia_api.schemas.snapshot import RollbackRequest, SnapshotPublic
from omnia_api.services import orchestrator_client
from omnia_api.services import repo as repo_svc
from omnia_api.services.queue import enqueue_preview

router = APIRouter(prefix="/api/projects", tags=["rollback"])

# Container-backed Next.js/Vite templates serve the live preview from a running
# dev container (`omnia-dev-<slug>`), NOT from a re-rendered static file. A git
# checkout alone reverts the repo but leaves the container serving the *old* code
# (the build / edit / style-patch paths all push files into the container via
# `hot_reload`; rollback must do the same or "вернуться назад" is a visible no-op
# on the live preview). Static templates (blank/landing/portfolio/blog) have no
# persistent container — their preview re-renders from repo files, so they roll
# back correctly without this. Kept in sync with messages.py `CONTAINER_NEXT`.
_CONTAINER_NEXT = ("fullstack", "nextjs_entities", "spa", "realtime")


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


@router.post("/{project_id}/rollback", response_model=SnapshotPublic)
async def post_rollback(
    project_id: UUID,
    payload: RollbackRequest,
    session: SessionDep,
    current_user: CurrentUserDep,
) -> SnapshotPublic:
    project = await session.get(Project, project_id)
    if project is None or project.owner_id != current_user.id:
        raise ApiError("not_found", "project not found", status.HTTP_404_NOT_FOUND)

    target = await session.get(Snapshot, payload.snapshot_id)
    if target is None or target.project_id != project_id:
        raise ApiError("not_found", "snapshot not found", status.HTTP_404_NOT_FOUND)

    new_sha = await asyncio.to_thread(repo_svc.checkout, project_id, target.commit_sha)

    # Container apps: push the rolled-back tree into the live dev container so the
    # preview actually reverts (parity with build / edit / style-patch). Without
    # this the git repo reverts but `omnia-dev-<slug>` keeps serving the post-edit
    # code → the flagship "вернуться назад" is a no-op on the live preview.
    # Best-effort (R-10): git + snapshot are already the canonical state, so a
    # momentarily-down orchestrator only delays the live revert, never loses it.
    if project.template in _CONTAINER_NEXT:
        try:
            reverted_files = await asyncio.to_thread(
                repo_svc.read_files, project_id, new_sha
            )
            await orchestrator_client.hot_reload(
                project_id=project_id,
                slug=project.slug,
                files=reverted_files,
            )
        except Exception:
            # Preview refresh must never block the rollback; it's already committed.
            pass

    new_snapshot = Snapshot(
        project_id=project_id,
        commit_sha=new_sha,
        prompt_text=None,
        model_id=None,
        parent_id=project.current_snapshot_id,
    )
    session.add(new_snapshot)
    target.is_rollback_target = True
    await session.flush()
    project.current_snapshot_id = new_snapshot.id
    await session.commit()
    await session.refresh(new_snapshot)

    await asyncio.to_thread(enqueue_preview, new_snapshot.id)

    payload_dict = _snapshot_dict(new_snapshot)
    payload_dict_str_keys = {
        "id": str(new_snapshot.id),
        "project_id": str(new_snapshot.project_id),
        "commit_sha": new_snapshot.commit_sha,
        "prompt_text": new_snapshot.prompt_text,
        "model_id": new_snapshot.model_id,
        "parent_id": str(new_snapshot.parent_id) if new_snapshot.parent_id else None,
        "preview_url": preview_public_url(new_snapshot.preview_key),
        "is_rollback_target": new_snapshot.is_rollback_target,
        "created_at": new_snapshot.created_at.isoformat(),
    }
    await publish_event(project_id, "snapshot.created", {"snapshot": payload_dict_str_keys})

    return SnapshotPublic.model_validate(payload_dict)
