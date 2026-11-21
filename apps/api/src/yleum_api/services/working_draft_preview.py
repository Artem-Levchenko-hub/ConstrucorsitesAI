"""Owner access to an isolated, interactive preview of a stopped generation."""

from __future__ import annotations

import re
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from yleum_api.core.errors import ApiError
from yleum_api.models.generation_run import GenerationRun
from yleum_api.models.message import Message
from yleum_api.models.project import Project
from yleum_api.models.snapshot import Snapshot
from yleum_api.models.user import User
from yleum_api.services import orchestrator_client as orch
from yleum_api.services import project_cell_runtime
from yleum_api.services.generation.source_checkpoint import (
    _object_key,
    decode_checkpoint,
    load_checkpoint_object,
)
from yleum_api.services.generation_deployment_drain import require_generation_admission
from yleum_api.services.max_project_kit import MAX_SECURITY_LOCKED_FILES


@dataclass(frozen=True)
class _Draft:
    run_id: UUID
    project_id: UUID
    owner_id: UUID
    digest: str
    baseline: str | None


def _unavailable() -> ApiError:
    return ApiError("conflict", "Для этой попытки пока нет рабочего предпросмотра.", 409)


async def _current_draft(
    session: AsyncSession, project_id: UUID, owner_id: UUID, run_id: UUID,
) -> _Draft:
    # Join the current owner and accepted baseline in the same DB read as the
    # latest attempt. Never accept a checkpoint digest, path or URL from the browser.
    row = (await session.execute(
        select(GenerationRun, Project.current_snapshot_id, Snapshot.commit_sha, Message.id,
               Message.snapshot_id)
        .join(Project, GenerationRun.project_id == Project.id)
        .outerjoin(Snapshot, (Snapshot.id == Project.current_snapshot_id)
                   & (Snapshot.project_id == Project.id))
        .outerjoin(Message, (Message.id == GenerationRun.assistant_message_id)
                   & (Message.project_id == Project.id))
        .where(Project.id == project_id, Project.owner_id == owner_id,
               Project.template == "max_miniapp")
        .order_by(GenerationRun.created_at.desc(), GenerationRun.id.desc())
        .limit(1).execution_options(populate_existing=True)
    )).one_or_none()
    if row is None:
        raise _unavailable()
    run, accepted_id, baseline, message_id, result_id = row
    if (run.id != run_id or run.user_id != owner_id
            or run.status not in {"failed", "cancelled", "completed"}
            or message_id is None or result_id is not None
            or (accepted_id is not None and baseline is None)):
        raise _unavailable()
    checkpoint = (run.agent_state or {}).get("source_checkpoint")
    if not isinstance(checkpoint, dict) or checkpoint.get("baseline_sha") != baseline:
        raise _unavailable()
    digest = checkpoint.get("digest")
    if not isinstance(digest, str) or not re.fullmatch(r"[a-f0-9]{64}", digest):
        raise _unavailable()
    return _Draft(run.id, project_id, owner_id, digest, baseline)


async def _source(draft: _Draft, workspace_id: UUID) -> dict[str, str]:
    try:
        data = await load_checkpoint_object(
            draft.project_id, draft.run_id, digest=draft.digest,
            object_key=_object_key(draft.project_id, draft.run_id, draft.digest),
        )
    except Exception as exc:
        raise ApiError("unavailable", "Не удалось прочитать сохранённый черновик.", 503) from exc
    saved = decode_checkpoint(
        data, project_id=draft.project_id, user_id=draft.owner_id,
        run_id=draft.run_id, baseline_sha=draft.baseline,
    ) if data is not None else None
    if saved is None:
        raise _unavailable()
    # Checkpoints carry every editable file. Take only platform-managed paths
    # from the same project's retained workspace, never its rolled-back pages,
    # environment, database or integration credentials.
    retained = await orch.project_cell_draft_files(workspace_id)
    core = {
        path: value for path, value in retained.files.items() if path in MAX_SECURITY_LOCKED_FILES
    }
    if not core:
        raise _unavailable()
    return core | saved["files"]


async def open_checkpoint_preview(
    session: AsyncSession, project: Project, owner: User, run_id: UUID,
) -> orch.ProjectCellPreviewSession:
    draft = await _current_draft(session, project.id, owner.id, run_id)
    selection = await project_cell_runtime.resolve_project_cell_public_selection(
        session, project, owner=owner,
    )
    if not selection.selected or selection.workspace is None:
        raise _unavailable()
    project_cell_runtime._require_workspace_identity(project, selection)
    workspace_id = selection.workspace.id
    # Even a lookup can wake a sleeping candidate. Hold the existing deploy
    # barrier until the orchestrator has durably claimed any preparation.
    await require_generation_admission(session)

    async def preview(files: dict[str, str] | None = None) -> orch.ProjectCellPreviewSession:
        return await orch.project_cell_checkpoint_preview(
            workspace_id, project_id=draft.project_id, owner_id=draft.owner_id,
            run_id=draft.run_id, checkpoint_digest=draft.digest, files=files,
        )

    try:
        try:
            result = await preview()
        except orch.OrchestratorBadRequest as exc:
            if exc.status_code != 409 or exc.details != {"reason": "draft_preview_source_required"}:
                raise
            files = await _source(draft, workspace_id)
            if await _current_draft(session, project.id, owner.id, run_id) != draft:
                raise _unavailable() from None
            result = await preview(files)
    except orch.OrchestratorBadRequest as exc:
        if exc.status_code == 409 and exc.details == {"reason": "draft_preview_preparing"}:
            raise ApiError(
                "conflict", "Запускаем сохранённый черновик.", 409, details=exc.details,
            ) from exc
        raise _unavailable() from exc
    except orch.OrchestratorUnavailable as exc:
        raise ApiError("unavailable", "Среда предпросмотра временно недоступна.", 503) from exc
    if await _current_draft(session, project.id, owner.id, run_id) != draft:
        raise _unavailable()
    return result
