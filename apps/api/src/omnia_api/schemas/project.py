from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

Template = Literal["blank", "landing", "portfolio", "blog"]


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    template: Template = "blank"


class ProjectPublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    owner_id: UUID
    name: str
    slug: str
    template: Template
    current_snapshot_id: UUID | None
    created_at: datetime
    updated_at: datetime
