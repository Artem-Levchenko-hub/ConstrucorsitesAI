from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel

VersionStatus = Literal["queued", "running", "ready", "failed", "cancelled", "unchanged"]
PreviewStatus = Literal["pending", "ready", "failed", "missing"]


class VersionPreview(BaseModel):
    url: str
    width: int
    height: int
    route: str
    reconstructed: bool = False


class ProjectVersionPublic(BaseModel):
    id: UUID
    number: int
    project_id: UUID
    source_message_id: UUID | None
    generation_run_id: UUID | None
    snapshot_id: UUID | None
    commit_sha: str | None
    prompt_text: str
    model_id: str | None
    created_at: datetime
    status: VersionStatus
    preview_status: PreviewStatus
    previews: list[VersionPreview]
    is_current: bool
    can_restore: bool


class ProjectVersionsPage(BaseModel):
    versions: list[ProjectVersionPublic]
    next_cursor: int | None
