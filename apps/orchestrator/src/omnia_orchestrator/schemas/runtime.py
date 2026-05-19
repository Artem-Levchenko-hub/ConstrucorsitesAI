"""Pydantic DTOs for the internal orchestrator API.

These shapes are consumed by apps/api (which forwards from web). Keep in
sync with docs/01-api-contract.md V2 section.
"""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

Tier = Literal["free", "pro", "business"]
RuntimeState = Literal["provisioning", "running", "paused", "stopped", "failed"]


_SLUG_PATTERN = r"^[a-z0-9](?:[a-z0-9-]{1,61}[a-z0-9])?$"


class ProvisionRequest(BaseModel):
    project_id: UUID
    slug: str = Field(min_length=3, max_length=63, pattern=_SLUG_PATTERN)
    template: str  # e.g. "nextjs-postgres-drizzle"
    tier: Tier = "free"
    initial_env: dict[str, str] = Field(default_factory=dict)


class ProvisionResponse(BaseModel):
    project_id: UUID
    container_name: str
    port: int
    dev_url: str  # https://<slug>.preview.<base_domain>
    state: RuntimeState


class WakeRequest(BaseModel):
    project_id: UUID


class WakeResponse(BaseModel):
    project_id: UUID
    state: RuntimeState
    ready_in_seconds: int  # estimated wake time


class StopRequest(BaseModel):
    project_id: UUID
    pause: bool = True  # True = docker pause (keep memory), False = docker stop


class HotReloadRequest(BaseModel):
    project_id: UUID
    # files: dict path → content. Same shape as `<file path="...">...</file>` extraction
    # from apps/api/src/omnia_api/services/file_extractor.py.
    files: dict[str, str]


class StatusResponse(BaseModel):
    project_id: UUID
    state: RuntimeState
    container_name: str | None = None
    port: int | None = None
    dev_url: str | None = None
    last_activity_at: str | None = None  # ISO8601
    cpu_pct: float | None = None
    memory_mb: int | None = None
    logs_tail_url: str | None = None  # signed url for last 200 lines


class DeployRequest(BaseModel):
    project_id: UUID
    commit_sha: str = Field(min_length=40, max_length=40)


class DeployResponse(BaseModel):
    project_id: UUID
    image_tag: str
    state: Literal["building", "pushing", "running", "healthy", "failed"]
    prod_url: str | None = None
