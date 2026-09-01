"""Internal Project Cell workspace schemas."""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class WorkspaceEnsureRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: UUID
    project_id: UUID
    owner_id: UUID
    generation_run_id: UUID
    profile_version: str = Field(min_length=1)
    operation_id: UUID
    fencing_epoch: int = Field(gt=0)
    request_digest: str = Field(pattern=r"^[0-9a-f]{64}$")


class WorkspaceControlRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: UUID
    kind: Literal["wake", "pause", "stop", "destroy", "restore", "reconcile"]
    checkpoint_ref: str | None = None
    operation_id: UUID
    fencing_epoch: int = Field(gt=0)
    request_digest: str = Field(pattern=r"^[0-9a-f]{64}$")


class WorkspaceObserveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: UUID
    operation_id: UUID
    fencing_epoch: int = Field(gt=0)
    request_digest: str = Field(pattern=r"^[0-9a-f]{64}$")


class WorkspaceCapabilityResponse(BaseModel):
    project_id: UUID
    provider: Literal["disabled", "docker_owner_canary"]
    enabled: bool
    ready: bool
    state: Literal["disabled", "unsupported", "ready"]
    detail: str


class WorkspaceResourceResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: UUID
    state: Literal[
        "resources_ready",
        "resources_paused",
        "retained",
        "partial",
        "degraded",
        "conflict",
    ]
    provider_ref: str | None
    fencing_epoch: int | None
    checkpoint_ref: str | None
    has_workspace: bool
    has_agent_home: bool
    has_postgres: bool
    has_redis: bool


class WorkspaceAgentBootstrapResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    files: dict[str, str]
    seeded_from_project: bool
    generation_run_id: UUID
    fencing_epoch: int
    workspace_revision: str = Field(pattern=r"^[0-9a-f]{64}$")


class WorkspaceAgentBootstrapRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    generation_run_id: UUID
    fencing_epoch: int = Field(gt=0)


class WorkspaceAgentWriteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    generation_run_id: UUID
    fencing_epoch: int = Field(gt=0)
    expected_revision: str = Field(pattern=r"^[0-9a-f]{64}$")
    files: dict[str, str]
    deletes: list[str] = Field(default_factory=list)


class WorkspaceAgentWriteResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    written: int
    deleted: int
    workspace_revision: str = Field(pattern=r"^[0-9a-f]{64}$")


class WorkspaceAgentExecRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    generation_run_id: UUID
    fencing_epoch: int = Field(gt=0)
    expected_revision: str = Field(pattern=r"^[0-9a-f]{64}$")
    cmd: str = Field(min_length=1)
    timeout_seconds: int = Field(default=180, ge=1, le=900)


class WorkspaceAgentExecResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ok: bool
    exit_code: int
    detail: str
    timed_out: bool = False
    workspace_revision: str = Field(pattern=r"^[0-9a-f]{64}$")
