"""Fail-closed workspace-provider selection and live dependency assembly."""

from __future__ import annotations

import os
from pathlib import Path
from typing import cast

from omnia_orchestrator.core.cell_resources import CellResourceProfile, CellResourceSettings
from omnia_orchestrator.core.config import Settings
from omnia_orchestrator.core.workspace_provider import WorkspaceProvider
from omnia_orchestrator.services.cell_admission import CellAdmissionGate, DockerHostCapacityReader
from omnia_orchestrator.services.cell_checkpoint import CellCheckpointManager
from omnia_orchestrator.services.cell_lock import WorkspaceOperationLock
from omnia_orchestrator.services.cell_state import CellCredentialStore, CellStateStore
from omnia_orchestrator.services.disabled_workspace_provider import DisabledWorkspaceProvider
from omnia_orchestrator.services.docker_cell_resources import DockerCellResourceManager
from omnia_orchestrator.services.docker_owner_canary_provider import DockerOwnerCanaryProvider
from omnia_orchestrator.services.docker_py_cell_backend import DockerPyCellBackend


def build_workspace_provider(settings: Settings) -> WorkspaceProvider:
    if (
        settings.workspace_provider != "docker_owner_canary"
        or settings.docker_owner_canary_enabled is not True
    ):
        return DisabledWorkspaceProvider()
    if _host_supports_live_docker_provider() is False:
        return DockerOwnerCanaryProvider()

    profile = CellResourceProfile.from_settings(cast(CellResourceSettings, settings))
    state_store = CellStateStore(settings.cell_state_path)
    state_root = state_store.root.parent
    credential_store = CellCredentialStore(state_root / f"{state_store.root.name}-credentials")
    docker_backend = DockerPyCellBackend(
        docker_host=settings.docker_host,
        helper_image=profile.backup_image,
        # PostgreSQL reserves half the bundle, Redis one quarter. The agent
        # gets only the remainder; compilation must not use the 256 MiB
        # filesystem-helper limit or exceed admission's bundle reservation.
        exec_memory_limit_bytes=(
            profile.bundle_memory_bytes
            - profile.bundle_memory_bytes // 2
            - profile.bundle_memory_bytes // 4
        ),
        exec_cpu_cores=(
            profile.bundle_cpu_cores
            - max(profile.bundle_cpu_cores / 2.0, 0.5)
            - max(profile.bundle_cpu_cores / 4.0, 0.25)
        ),
    )
    resource_manager = DockerCellResourceManager(
        profile=profile,
        docker=docker_backend,
        admission_gate=CellAdmissionGate(profile),
        capacity_reader=DockerHostCapacityReader(
            docker=docker_backend,
            docker_host=settings.docker_host,
            state_path=profile.state_path,
            active_bundle_counter=lambda: _active_bundle_count(state_store),
        ),
        credential_store=credential_store,
        state_store=state_store,
        operation_lock=WorkspaceOperationLock(state_root),
        namespace="test" if _is_test_namespace(state_store.root) else "prod",
    )
    checkpoint_manager = CellCheckpointManager(
        profile_version=profile.profile_version,
        postgres_image=profile.postgres_image,
        docker=docker_backend,
        credential_store=credential_store,
        state_store=state_store,
    )
    return DockerOwnerCanaryProvider(
        resource_manager=resource_manager,
        checkpoint_manager=checkpoint_manager,
    )


def _active_bundle_count(state_store: CellStateStore) -> int:
    return sum(1 for state in state_store.all_states() if state.bundle_state != "retained")


def _is_test_namespace(state_root: Path) -> bool:
    lowered = str(state_root).casefold().replace("\\", "/")
    return "pytest-" in lowered or "/temp/" in lowered or "/tmp/" in lowered


def _host_supports_live_docker_provider() -> bool:
    return os.name != "nt"
