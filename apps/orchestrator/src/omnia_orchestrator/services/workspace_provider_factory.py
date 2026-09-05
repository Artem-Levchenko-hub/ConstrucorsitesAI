"""Fail-closed workspace-provider selection and live dependency assembly."""

from __future__ import annotations

import os
from pathlib import Path
from typing import cast
from uuid import UUID

from pydantic import ValidationError

from omnia_orchestrator.core.cell_resources import CellResourceProfile, CellResourceSettings
from omnia_orchestrator.core.config import Settings
from omnia_orchestrator.core.workspace_provider import (
    WorkspaceProvider,
    WorkspaceProviderUnavailable,
)
from omnia_orchestrator.services.cell_admission import CellAdmissionGate, DockerHostCapacityReader
from omnia_orchestrator.services.cell_checkpoint import CellCheckpointManager
from omnia_orchestrator.services.cell_lock import WorkspaceOperationLock
from omnia_orchestrator.services.cell_reservations import CellCapacityReservationStore
from omnia_orchestrator.services.cell_state import CellCredentialStore, CellStateStore
from omnia_orchestrator.services.disabled_workspace_provider import DisabledWorkspaceProvider
from omnia_orchestrator.services.docker_cell_resources import DockerCellResourceManager
from omnia_orchestrator.services.docker_owner_canary_provider import DockerOwnerCanaryProvider
from omnia_orchestrator.services.docker_py_cell_backend import DockerPyCellBackend
from omnia_orchestrator.services.machine_adapter import MachineAdapter


def settings_for_workspace(settings: Settings, workspace_id: UUID) -> Settings:
    """Retain a cell's immutable quota/identity across default-profile rollouts.

    The deployment default applies only to new cells. Never relabel an existing
    cell, or account its containers with the new default's different quota.
    """
    if (
        settings.workspace_provider != "docker_owner_canary"
        or settings.docker_owner_canary_enabled is not True
    ):
        return settings
    state = CellStateStore(settings.cell_state_path).load(workspace_id)
    if state is None or state.profile_version == settings.cell_profile_version:
        return settings
    if state.profile_version not in {
        "docker-owner-cell-resources-v1",
        "docker-owner-cell-resources-v2",
    }:
        raise WorkspaceProviderUnavailable("stored workspace profile is unsupported")
    # Revalidate cross-field CPU constraints too: a v2 deployment may have a
    # legacy bundle budget that is invalid for v1. Never under-account a cell.
    try:
        return Settings.model_validate(
            {
                **settings.model_dump(),
                "cell_profile_version": state.profile_version,
            }
        )
    except ValidationError:
        # Settings validation can contain connection data; do not log its input.
        raise WorkspaceProviderUnavailable(
            "stored workspace profile is incompatible with deployment quotas"
        ) from None


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
        exec_memory_limit_bytes=profile.executor_memory_bytes,
        exec_cpu_cores=profile.executor_cpu_cores,
        network_pool=settings.cell_network_pool,
    )
    operation_lock = WorkspaceOperationLock(state_root)
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
        operation_lock=operation_lock,
        capacity_lock=operation_lock,
        capacity_reservations=CellCapacityReservationStore(
            state_root / f"{state_store.root.name}-capacity-reservations"
        ),
        namespace="test" if _is_test_namespace(state_store.root) else "prod",
    )
    checkpoint_manager = CellCheckpointManager(
        profile_version=profile.profile_version,
        postgres_image=profile.postgres_image,
        docker=docker_backend,
        credential_store=credential_store,
        state_store=state_store,
    )
    if settings.cell_machine_enabled:
        machine_runtime = MachineAdapter(resource_manager, settings)
        machine_runtime.validate_available()
        resource_manager.machine_runtime = machine_runtime
        checkpoint_manager.machine_runtime = machine_runtime
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
