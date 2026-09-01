"""Default-off workspace provider."""

from __future__ import annotations

from uuid import UUID

from omnia_orchestrator.core.workspace_provider import (
    ControlAction,
    ControlResult,
    WorkspaceHandle,
    WorkspaceProviderUnavailable,
    WorkspaceSpec,
    WorkspaceStatus,
)


class DisabledWorkspaceProvider:
    async def ensure(self, spec: WorkspaceSpec) -> WorkspaceHandle:
        raise WorkspaceProviderUnavailable("workspace provider is disabled")

    async def wake(self, workspace_id: UUID) -> WorkspaceHandle:
        raise WorkspaceProviderUnavailable("workspace provider is disabled")

    async def pause(self, workspace_id: UUID, checkpoint_ref: str) -> None:
        raise WorkspaceProviderUnavailable("workspace provider is disabled")

    async def destroy(self, workspace_id: UUID) -> None:
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

    async def execute_control(
        self,
        workspace_id: UUID,
        action: ControlAction,
    ) -> ControlResult:
        raise WorkspaceProviderUnavailable("workspace provider is disabled")
