"""Replaceable Project Cell workspace-provider contract."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol
from uuid import UUID

from omnia_orchestrator.core.cell_resources import LifecycleMutation, validate_checkpoint_ref


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


@dataclass(frozen=True, slots=True)
class ControlAction:
    kind: Literal["wake", "pause", "stop", "destroy", "restore", "reconcile", "release"]
    checkpoint_ref: str | None = None

    def __post_init__(self) -> None:
        if self.kind in {"pause", "stop", "restore"}:
            if self.checkpoint_ref is None:
                raise ValueError(f"checkpoint_ref is required for {self.kind!r}")
            validate_checkpoint_ref(self.checkpoint_ref)
            return
        if self.checkpoint_ref is not None:
            raise ValueError(f"checkpoint_ref is forbidden for {self.kind!r}")


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
