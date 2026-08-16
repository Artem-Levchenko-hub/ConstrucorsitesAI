from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class SnapshotPublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    project_id: UUID
    commit_sha: str
    prompt_text: str | None = None
    model_id: str | None = None
    parent_id: UUID | None = None
    preview_url: str | None = Field(default=None, alias="preview_url")
    is_rollback_target: bool
    created_at: datetime


class SnapshotWithFiles(SnapshotPublic):
    files: dict[str, str]


class RollbackRequest(BaseModel):
    snapshot_id: UUID
