"""Fail-closed workspace-provider selection."""

from __future__ import annotations

from omnia_orchestrator.core.config import Settings
from omnia_orchestrator.core.workspace_provider import WorkspaceProvider
from omnia_orchestrator.services.disabled_workspace_provider import DisabledWorkspaceProvider
from omnia_orchestrator.services.docker_owner_canary_provider import (
    DockerOwnerCanaryProvider,
)


def build_workspace_provider(settings: Settings) -> WorkspaceProvider:
    if (
        settings.workspace_provider == "docker_owner_canary"
        and settings.docker_owner_canary_enabled is True
    ):
        return DockerOwnerCanaryProvider()
    return DisabledWorkspaceProvider()
