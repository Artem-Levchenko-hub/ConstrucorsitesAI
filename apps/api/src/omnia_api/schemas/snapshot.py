from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:
    from omnia_api.models.snapshot import Snapshot


def snapshot_public_dict(s: Snapshot) -> dict[str, object]:
    from omnia_api.core.minio import preview_public_url

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
