"""Replaceable Project Cell workspace-provider contract."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol
from uuid import UUID


class WorkspaceProviderUnavailable(Exception):
    """The selected foundation provider cannot mutate workspaces."""


@dataclass(frozen=True, slots=True)
class WorkspaceSpec:
    workspace_id: UUID
    project_id: UUID
    owner_id: UUID
    profile_version: str


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
    state: Literal["disabled", "unsupported"]
    detail: str


@dataclass(frozen=True, slots=True)
class ControlAction:
    kind: str


@dataclass(frozen=True, slots=True)
class ControlResult:
    ok: bool
    detail: str


class WorkspaceProvider(Protocol):
    async def ensure(self, spec: WorkspaceSpec) -> WorkspaceHandle: ...

    async def wake(self, workspace_id: UUID) -> WorkspaceHandle: ...

    async def pause(self, workspace_id: UUID, checkpoint_ref: str) -> None: ...

    async def destroy(self, workspace_id: UUID) -> None: ...

    async def status(self, project_id: UUID) -> WorkspaceStatus: ...

    async def execute_control(
        self,
        workspace_id: UUID,
        action: ControlAction,
    ) -> ControlResult: ...
