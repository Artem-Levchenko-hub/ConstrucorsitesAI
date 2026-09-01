"""Internal workspace-capability response schema."""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel


class WorkspaceCapabilityResponse(BaseModel):
    project_id: UUID
    provider: Literal["disabled", "docker_owner_canary"]
    enabled: bool
    ready: bool
    state: Literal["disabled", "unsupported"]
    detail: str
