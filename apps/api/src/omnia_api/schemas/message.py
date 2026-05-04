from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class MessagePublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    project_id: UUID
    snapshot_id: UUID | None = None
    role: Literal["user", "assistant", "system"]
    content: str
    model_id: str | None = None
    tokens_in: int | None = None
    tokens_out: int | None = None
    created_at: datetime


class PromptRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=10_000)
    model_id: str = Field(min_length=1)


class PromptResponse(BaseModel):
    message_id: UUID
    snapshot_id: UUID | None = None
