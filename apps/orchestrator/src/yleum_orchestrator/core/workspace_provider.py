"""Replaceable Project Cell workspace-provider contract."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Literal, Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from yleum_orchestrator.core.cell_resources import LifecycleMutation, validate_checkpoint_ref


class WorkspaceProviderUnavailable(Exception):
    """The selected foundation provider cannot mutate workspaces."""


@dataclass(frozen=True, slots=True)
class WorkspaceSpec:
    workspace_id: UUID
    project_id: UUID
    owner_id: UUID
    profile_version: str
    generation_run_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class WorkspaceHandle:
    workspace_id: UUID
    provider: str
    provider_ref: str


@dataclass(frozen=True, slots=True)
class WorkspaceStatus:
    project_id: UUID
    provider: Literal["disabled", "docker_owner_canary"]
    enabled: bool
    ready: bool
    state: Literal["disabled", "unsupported", "ready"]
    detail: str


WorkspaceResourceState = Literal[
    "resources_ready",
    "resources_paused",
    "retained",
    "partial",
    "degraded",
    "conflict",
]


class ReleaseEnvironmentExpectation(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    source_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    environment_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")

    def release_request_digest(self, workspace_id: UUID, run_id: UUID) -> str:
        # The API reserves the same canonical operation envelope in SQL.
        envelope = {
            "workspace_id": str(workspace_id),
            "generation_run_id": str(run_id),
            "kind": "release",
            "request": {"require_environment_proof": self.model_dump(mode="json")},
        }
        encoded = json.dumps(envelope, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True, slots=True)
class ControlAction:
    kind: Literal["wake", "pause", "stop", "destroy", "restore", "reconcile", "release"]
    checkpoint_ref: str | None = None
    require_environment_proof: ReleaseEnvironmentExpectation | None = None

    def __post_init__(self) -> None:
        if self.require_environment_proof is not None and self.kind != "release":
            raise ValueError("environment proof may only be required for generation release")
        if self.kind in {"pause", "stop", "restore"}:
            if self.checkpoint_ref is None:
                raise ValueError(f"checkpoint_ref is required for {self.kind!r}")
            validate_checkpoint_ref(self.checkpoint_ref)
            return
        if self.checkpoint_ref is not None:
            raise ValueError(f"checkpoint_ref is forbidden for {self.kind!r}")


class ProjectCellLeaseObservation(BaseModel):
    """Authenticated, fenced observation; missing evidence never means idle."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    version: Literal[1]
    workspace_id: UUID
    project_id: UUID
    owner_id: UUID
    operation_id: UUID
    request_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    fencing_epoch: int = Field(gt=0)
    active_generation_run_id: UUID | None
    active_generation_fencing_epoch: int | None = Field(gt=0)
    quiescent: bool

    @model_validator(mode="after")
    def coherent_lease(self) -> ProjectCellLeaseObservation:
        if (self.active_generation_run_id is None) != (
            self.active_generation_fencing_epoch is None
        ) or (self.quiescent and self.active_generation_run_id is not None):
            raise ValueError("incoherent lease observation")
        return self


@dataclass(frozen=True, slots=True)
class WorkspaceResourceStatus:
    workspace_id: UUID
    state: WorkspaceResourceState
    provider_ref: str | None
    fencing_epoch: int | None
    checkpoint_ref: str | None
    has_workspace: bool
    has_agent_home: bool
    has_postgres: bool
    has_redis: bool
    lease_observation: ProjectCellLeaseObservation | None = None

    def __post_init__(self) -> None:
        if self.provider_ref == "":
            raise ValueError("provider_ref must be non-empty when provided")
        if self.fencing_epoch is not None and self.fencing_epoch < 0:
            raise ValueError("fencing_epoch must be zero or positive when provided")
        if self.checkpoint_ref is not None:
            validate_checkpoint_ref(self.checkpoint_ref)


class WorkspaceProvider(Protocol):
    async def ensure(
        self,
        spec: WorkspaceSpec,
        mutation: LifecycleMutation,
    ) -> WorkspaceHandle: ...

    async def wake(
        self,
        workspace_id: UUID,
        mutation: LifecycleMutation,
    ) -> WorkspaceHandle: ...

    async def pause(
        self,
        workspace_id: UUID,
        checkpoint_ref: str,
        mutation: LifecycleMutation,
    ) -> None: ...

    async def destroy(self, workspace_id: UUID, mutation: LifecycleMutation) -> None: ...

    async def status(self, project_id: UUID) -> WorkspaceStatus: ...

    async def inspect_resources(self, workspace_id: UUID) -> WorkspaceResourceStatus: ...

    async def observe_resources(
        self,
        workspace_id: UUID,
        mutation: LifecycleMutation,
    ) -> WorkspaceResourceStatus: ...

    async def execute_control(
        self,
        workspace_id: UUID,
        action: ControlAction,
        mutation: LifecycleMutation,
    ) -> WorkspaceResourceStatus: ...
