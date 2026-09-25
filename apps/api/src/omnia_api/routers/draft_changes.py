"""Owner actions on unsaved draft edits (see services/draft_changes.py)."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter

from omnia_api.core.deps import CurrentUserDep, SessionDep
from omnia_api.schemas.draft_changes import DraftDiscardResponse, DraftSaveResponse
from omnia_api.services import draft_changes

router = APIRouter(prefix="/api/projects", tags=["draft"])


@router.post("/{project_id}/draft/save-version", response_model=DraftSaveResponse)
async def save_draft_version(
    project_id: UUID,
    session: SessionDep,
    current_user: CurrentUserDep,
) -> DraftSaveResponse:
    version = await draft_changes.save_draft_as_version(session, project_id, current_user.id)
    assert version.snapshot_id is not None
    return DraftSaveResponse(
        version_id=version.id,
        number=version.number,
        snapshot_id=version.snapshot_id,
    )


@router.post("/{project_id}/draft/discard", response_model=DraftDiscardResponse)
async def discard_draft(
    project_id: UUID,
    session: SessionDep,
    current_user: CurrentUserDep,
) -> DraftDiscardResponse:
    result = await draft_changes.discard_draft_changes(session, project_id, current_user.id)
    return DraftDiscardResponse(
        written=result.written,
        deleted=result.deleted,
        workspace_revision=result.workspace_revision,
    )
