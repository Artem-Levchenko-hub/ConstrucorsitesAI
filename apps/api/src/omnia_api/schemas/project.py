from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

# V1 templates ship static HTML; "fullstack" ships a Next.js 15 + Drizzle
# project that runs in an orchestrator-managed dev container. The two stacks
# share the `<file path="...">` AI contract but live on different preview
# surfaces — V1 on `/p/<slug>`, fullstack on `runtime.dev_url`.
Template = Literal["blank", "landing", "portfolio", "blog", "fullstack"]


def is_fullstack(template: str) -> bool:
    """Single source of truth: which templates run inside a dev container."""
    return template == "fullstack"


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
