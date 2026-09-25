"""Owner actions on unsaved draft edits."""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class DraftSaveResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version_id: UUID
    number: int
    snapshot_id: UUID


class DraftDiscardResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    written: int = Field(ge=0)
    deleted: int = Field(ge=0)
    workspace_revision: str = Field(pattern=r"^[0-9a-f]{64}$")
