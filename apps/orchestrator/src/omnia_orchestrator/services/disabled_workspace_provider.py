"""Default-off workspace provider."""

from __future__ import annotations

from uuid import UUID

from omnia_orchestrator.core.cell_resources import LifecycleMutation
from omnia_orchestrator.core.workspace_provider import (
    ControlAction,
    WorkspaceHandle,
    WorkspaceProviderUnavailable,
    WorkspaceResourceStatus,
    WorkspaceSpec,
    WorkspaceStatus,
)


class DisabledWorkspaceProvider:
    async def ensure(
        self,
        spec: WorkspaceSpec,
        mutation: LifecycleMutation,
    ) -> WorkspaceHandle:
        _ = spec, mutation
        raise WorkspaceProviderUnavailable("workspace provider is disabled")

    async def wake(
        self,
        workspace_id: UUID,
        mutation: LifecycleMutation,
    ) -> WorkspaceHandle:
        _ = workspace_id, mutation
        raise WorkspaceProviderUnavailable("workspace provider is disabled")

    async def pause(
        self,
        workspace_id: UUID,
        checkpoint_ref: str,
        mutation: LifecycleMutation,
    ) -> None:
        _ = workspace_id, checkpoint_ref, mutation
        raise WorkspaceProviderUnavailable("workspace provider is disabled")

    async def destroy(self, workspace_id: UUID, mutation: LifecycleMutation) -> None:
        _ = workspace_id, mutation
        raise WorkspaceProviderUnavailable("workspace provider is disabled")

    async def status(self, project_id: UUID) -> WorkspaceStatus:
        return WorkspaceStatus(
            project_id=project_id,
            provider="disabled",
            enabled=False,
            ready=False,
            state="disabled",
            detail="workspace provider is disabled",
        )

    async def inspect_resources(self, workspace_id: UUID) -> WorkspaceResourceStatus:
        _ = workspace_id
        raise WorkspaceProviderUnavailable("workspace provider is disabled")

    async def observe_resources(
        self,
        workspace_id: UUID,
        mutation: LifecycleMutation,
    ) -> WorkspaceResourceStatus:
        _ = workspace_id, mutation
        raise WorkspaceProviderUnavailable("workspace provider is disabled")

    async def execute_control(
        self,
        workspace_id: UUID,
        action: ControlAction,
        mutation: LifecycleMutation,
    ) -> WorkspaceResourceStatus:
        _ = workspace_id, action, mutation
        raise WorkspaceProviderUnavailable("workspace provider is disabled")
