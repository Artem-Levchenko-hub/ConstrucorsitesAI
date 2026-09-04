from __future__ import annotations

from types import SimpleNamespace
from uuid import UUID

import pytest

from omnia_orchestrator.core.cell_resources import (
    CellResourceNames,
    CellResourceProfile,
    LifecycleMutation,
    identity_labels,
)
from omnia_orchestrator.core.config import Settings
from omnia_orchestrator.core.workspace_provider import WorkspaceSpec


def _settings(**overrides: object) -> SimpleNamespace:
    base = {
        "workspace_provider": "disabled",
        "docker_owner_canary_enabled": False,
        "cell_profile_version": "docker-owner-cell-resources-v1",
        "cell_postgres_image": "",
        "cell_redis_image": "",
        "cell_backup_image": "",
        "cell_bundle_cpu_cores": 2.0,
        "cell_bundle_memory_bytes": 4 * 1024**3,
        "cell_active_machine_cpu_cores": 2.0,
        "cell_active_machine_memory_bytes": 2 * 1024**3,
        "cell_project_postgres_cpu_cores": 0.15,
        "cell_project_postgres_memory_bytes": 256 * 1024**2,
        "cell_helper_cpu_cores": 0.2,
        "cell_helper_memory_bytes": 128 * 1024**2,
        "cell_managed_core_cpu_cores": 0.35,
        "cell_managed_core_memory_bytes": 768 * 1024**2,
        "cell_host_cpu_reserve_cores": 2.0,
        "cell_host_memory_reserve_bytes": 4 * 1024**3,
        "cell_required_free_disk_bytes": 20 * 1024**3,
        "cell_host_disk_reserve_bytes": 10 * 1024**3,
        "cell_required_free_inodes": 100_000,
        "cell_host_inode_reserve": 50_000,
        "cell_state_path": "/opt/omnia-runtime/state/project-cells.json",
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def test_resource_identity_is_deterministic_and_secret_free() -> None:
    spec = WorkspaceSpec(
        workspace_id=UUID("00000000-0000-0000-0000-000000000001"),
        project_id=UUID("00000000-0000-0000-0000-000000000002"),
        owner_id=UUID("00000000-0000-0000-0000-000000000003"),
        profile_version="docker-owner-cell-resources-v1",
    )

    names = CellResourceNames.for_workspace(spec.workspace_id)
    assert names.internal_network == "omnia-cell-00000000000000000000000000000001-internal"
    assert names.checkpoint_volume.endswith("-checkpoints")
    assert names.postgres_container == names.postgres_volume
    assert CellResourceNames.for_workspace(
        spec.workspace_id, namespace="test"
    ).internal_network == ("omnia-cell-test-00000000000000000000000000000001-internal")

    labels = identity_labels(spec, "postgres")
    assert labels["omnia.workspace_id"] == str(spec.workspace_id)
    assert labels["omnia.resource_kind"] == "postgres"
    assert all("token" not in key and "password" not in key for key in labels)


def test_profile_defaults_are_dark_but_enabled_provider_requires_digest_images() -> None:
    profile = CellResourceProfile.from_settings(_settings())
    assert profile.postgres_image == ""

    with pytest.raises(ValueError):
        CellResourceProfile.from_settings(
            _settings(
                workspace_provider="docker_owner_canary",
                docker_owner_canary_enabled=True,
                cell_postgres_image="postgres:16",
                cell_redis_image="redis@sha256:" + "1" * 64,
                cell_backup_image="alpine@sha256:" + "2" * 64,
            )
        )


def test_capacity_profile_has_no_numerical_bundle_gate() -> None:
    profile = CellResourceProfile.from_settings(_settings())

    assert "cell_max_active_bundles" not in Settings.model_fields
    assert "max_active_bundles" not in profile.__dataclass_fields__


def test_v2_full_quota_sums_every_component_once() -> None:
    profile = CellResourceProfile.from_settings(
        _settings(cell_profile_version="docker-owner-cell-resources-v2")
    )

    assert profile.active_machine_quota.cpu_cores == 2.0
    assert profile.active_machine_quota.memory_bytes == 2 * 1024**3
    assert profile.full_quota.cpu_cores == sum(
        quota.cpu_cores for quota in profile.component_quotas()
    )
    assert profile.full_quota.memory_bytes == sum(
        quota.memory_bytes for quota in profile.component_quotas()
    )
    assert profile.full_quota.cpu_cores == 4.2
    assert profile.full_quota.memory_bytes == 6 * 1024**3 + 128 * 1024**2


def test_lifecycle_mutation_requires_sha256_digest() -> None:
    LifecycleMutation(UUID(int=1), 1, "a" * 64)

    with pytest.raises(ValueError):
        LifecycleMutation(UUID(int=1), 1, "not-a-digest")
    with pytest.raises(ValueError):
        LifecycleMutation(UUID(int=1), 0, "a" * 64)
