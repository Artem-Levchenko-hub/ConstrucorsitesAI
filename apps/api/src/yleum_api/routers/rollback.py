from __future__ import annotations

import asyncio
from uuid import UUID

from fastapi import APIRouter, status

from yleum_api.core.deps import CurrentUserDep, SessionDep
from yleum_api.core.errors import ApiError
from yleum_api.core.redis import publish_event
from yleum_api.models.project import Project
from yleum_api.models.snapshot import Snapshot
from yleum_api.schemas.snapshot import RollbackRequest, SnapshotPublic, snapshot_event_dict
from yleum_api.schemas.snapshot import snapshot_public_dict as _snapshot_dict
from yleum_api.services import repo as repo_svc
from yleum_api.services.project_versions import record_restored_version
from yleum_api.services.snapshot_restore import ensure_restore_supported

router = APIRouter(prefix="/api/projects", tags=["rollback"])



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

    # Unsupported MAX activation must fail before touching Git, the Cell or data.
    ensure_restore_supported(project.template)

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

    await publish_event(
        project_id, "snapshot.created", {"snapshot": snapshot_event_dict(new_snapshot)}
    )

    return SnapshotPublic.model_validate(_snapshot_dict(new_snapshot))
