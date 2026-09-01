"""Dark Docker-owner canary provider; foundation status only."""

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

_UNAVAILABLE_DETAIL = "docker owner canary is unsupported in the foundation"


class DockerOwnerCanaryProvider:
    async def ensure(self, spec: WorkspaceSpec) -> WorkspaceHandle:
        raise WorkspaceProviderUnavailable(_UNAVAILABLE_DETAIL)

    async def wake(self, workspace_id: UUID) -> WorkspaceHandle:
        raise WorkspaceProviderUnavailable(_UNAVAILABLE_DETAIL)

    async def pause(self, workspace_id: UUID, checkpoint_ref: str) -> None:
        raise WorkspaceProviderUnavailable(_UNAVAILABLE_DETAIL)

    async def destroy(self, workspace_id: UUID) -> None:
        raise WorkspaceProviderUnavailable(_UNAVAILABLE_DETAIL)

    async def status(self, project_id: UUID) -> WorkspaceStatus:
        return WorkspaceStatus(
            project_id=project_id,
            provider="docker_owner_canary",
            enabled=True,
            ready=False,
            state="unsupported",
            detail=_UNAVAILABLE_DETAIL,
        )

    async def execute_control(
        self,
        workspace_id: UUID,
        action: ControlAction,
    ) -> ControlResult:
        raise WorkspaceProviderUnavailable(_UNAVAILABLE_DETAIL)
