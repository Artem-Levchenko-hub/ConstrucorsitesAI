"""Startup entry point for safe Project Cell reservation recovery."""

from __future__ import annotations

from omnia_orchestrator.core.config import Settings
from omnia_orchestrator.services.docker_owner_canary_provider import DockerOwnerCanaryProvider
from omnia_orchestrator.services.workspace_provider_factory import build_workspace_provider


async def recover_workspace_provider_capacity(settings: Settings) -> int:
    """Reclaim only expired, proven-orphaned provisional reservations."""

    provider = build_workspace_provider(settings)
    if not isinstance(provider, DockerOwnerCanaryProvider):
        return 0
    manager = provider.resource_manager
    if manager is None:
        return 0
    return await manager.recover_capacity_reservations()
