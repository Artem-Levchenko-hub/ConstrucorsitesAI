"""The live executor consumes only the admitted bundle's remaining budget."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from omnia_orchestrator.core.config import Settings
from omnia_orchestrator.services import workspace_provider_factory
from omnia_orchestrator.services.docker_owner_canary_provider import DockerOwnerCanaryProvider
from omnia_orchestrator.services.docker_py_cell_backend import DockerPyCellBackend


@pytest.mark.parametrize("cpu,memory", [(2.0, 4 * 1024**3), (1.0, 1024**3)])
def test_factory_reserves_executor_within_bundle(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, cpu: float, memory: int
) -> None:
    monkeypatch.setattr(
        workspace_provider_factory, "_host_supports_live_docker_provider", lambda: True
    )
    settings = Settings(
        _env_file=None,
        database_url="postgresql://test:test@localhost/test",
        internal_token="test-internal-token-not-a-real-secret",
        workspace_provider="docker_owner_canary",
        docker_owner_canary_enabled=True,
        cell_state_path=str(tmp_path / "project-cells.json"),
        cell_postgres_image="postgres@sha256:" + "1" * 64,
        cell_redis_image="redis@sha256:" + "2" * 64,
        cell_backup_image="alpine@sha256:" + "3" * 64,
        cell_bundle_cpu_cores=cpu,
        cell_bundle_memory_bytes=memory,
    )

    provider = workspace_provider_factory.build_workspace_provider(settings)

    assert isinstance(provider, DockerOwnerCanaryProvider)
    assert provider.resource_manager is not None
    backend = provider.resource_manager.docker
    assert isinstance(backend, DockerPyCellBackend)
    assert backend.exec_memory_limit_bytes + memory // 2 + memory // 4 == memory
    assert backend.exec_cpu_cores + max(cpu / 2, 0.5) + max(cpu / 4, 0.25) == cpu
    assert backend.exec_memory_limit_bytes == memory // 4
    assert backend.exec_cpu_cores == cpu / 4


@pytest.mark.parametrize("cpu,memory", [(0.0, 1024**3), (0.5, 0), (0.001, 1024**3)])
def test_executor_rejects_unusable_resource_budget(cpu: float, memory: int) -> None:
    with pytest.raises(ValueError, match="reserved resource budget"):
        DockerPyCellBackend(
            docker_host="unix:///unused.sock",
            helper_image="alpine@sha256:" + "3" * 64,
            exec_cpu_cores=cpu,
            exec_memory_limit_bytes=memory,
        )


@pytest.mark.parametrize("cpu", [0.01, 0.5, 0.75, 0.99])
def test_enabled_cell_rejects_under_reserved_cpu_at_configuration(cpu: float) -> None:
    with pytest.raises(ValidationError, match=r"cell_bundle_cpu_cores >= 1\.0"):
        Settings(
            _env_file=None,
            database_url="postgresql://test:test@localhost/test",
            internal_token="test-internal-token-not-a-real-secret",
            workspace_provider="docker_owner_canary",
            docker_owner_canary_enabled=True,
            cell_bundle_cpu_cores=cpu,
        )


def test_disabled_cell_keeps_legacy_cpu_configuration_compatible() -> None:
    settings = Settings(
        _env_file=None,
        database_url="postgresql://test:test@localhost/test",
        internal_token="test-internal-token-not-a-real-secret",
        workspace_provider="docker_owner_canary",
        docker_owner_canary_enabled=False,
        cell_bundle_cpu_cores=0.5,
    )
    assert settings.cell_bundle_cpu_cores == 0.5
