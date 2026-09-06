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
from omnia_api.services import repo as repo_svc
from omnia_api.services.project_versions import record_restored_version
from omnia_api.services.queue import enqueue_preview
from omnia_api.services.snapshot_restore import apply_legacy_restore, ensure_restore_supported

router = APIRouter(prefix="/api/projects", tags=["rollback"])

# Container-backed Next.js/Vite templates serve the live preview from a running
# dev container (`omnia-dev-<slug>`), NOT from a re-rendered static file. A git
# checkout alone reverts the repo but leaves the container serving the *old* code
# (the build / edit / style-patch paths all push files into the container via
# `hot_reload`; rollback must do the same or "вернуться назад" is a visible no-op
# on the live preview). Static templates (blank/landing/portfolio/blog) have no
# persistent container — their preview re-renders from repo files, so they roll
# back correctly without this. Kept in sync with messages.py `CONTAINER_NEXT`.
_CONTAINER_NEXT = ("fullstack", "nextjs_entities", "spa", "realtime", "max_miniapp")


def with_rollback_deletions(
    target_files: dict[str, str], old_files: dict[str, str]
) -> dict[str, str]:
    """Extend the rolled-back tree with delete-intents for orphaned files.

    ``old_files`` is the tree the container serves now; any path present there
    but absent from ``target_files`` must be DELETED in the container, or the
    rollback is a lie for created-after-target files (the container keeps them,
    the build keeps failing on them). ``write_files`` treats empty content as
    "rm -f", so the delete-intent is simply ``path: ""``. Target content always
    wins over a same-path delete (dict update order). Pure — unit-tested.
    """
    out = {p: "" for p in old_files if p not in target_files}
    out.update(target_files)
    return out


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

    # The tree the container is serving RIGHT NOW (pre-rollback HEAD) — needed
    # to compute files the rollback must DELETE from the live container below.
    old_sha: str | None = None
    if project.current_snapshot_id is not None:
        _cur = await session.get(Snapshot, project.current_snapshot_id)
        old_sha = _cur.commit_sha if _cur is not None else None

    # Unsupported MAX activation must fail before touching Git, the Cell or data.
    ensure_restore_supported(project.template)
    if project.template in _CONTAINER_NEXT:
        target_files = await asyncio.to_thread(repo_svc.read_files, project_id, target.commit_sha)
        old_files = (
            await asyncio.to_thread(repo_svc.read_files, project_id, old_sha) if old_sha else {}
        )
        await apply_legacy_restore(project_id, project.slug, target_files, old_files)

    # Only acknowledge canonical restoration after the runtime accepted the source.
    new_sha = await asyncio.to_thread(repo_svc.checkout, project_id, target.commit_sha)

    new_snapshot = Snapshot(
        project_id=project_id,
        commit_sha=new_sha,
        # Rollback is a deliberate user action and therefore a real version.
        # Technical MAX snapshots use prompt_text=None and are collapsed by the
        # version rail; this semantic label keeps rollback distinct from them.
        prompt_text="Восстановление версии",
        model_id=None,
        parent_id=project.current_snapshot_id,
    )
    session.add(new_snapshot)
    target.is_rollback_target = True
    await session.flush()
    project.current_snapshot_id = new_snapshot.id
    await record_restored_version(session, project, new_snapshot, target)
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
