from __future__ import annotations

from dataclasses import FrozenInstanceError, fields
from types import ModuleType
from uuid import UUID, uuid4

import pytest

from omnia_orchestrator.core.config import Settings
from omnia_orchestrator.core.workspace_provider import (
    ControlAction,
    ControlResult,
    WorkspaceHandle,
    WorkspaceProviderUnavailable,
    WorkspaceSpec,
)
from omnia_orchestrator.services import (
    disabled_workspace_provider,
    docker_owner_canary_provider,
    workspace_provider_factory,
)
from omnia_orchestrator.services.disabled_workspace_provider import DisabledWorkspaceProvider
from omnia_orchestrator.services.docker_owner_canary_provider import (
    DockerOwnerCanaryProvider,
)
from omnia_orchestrator.services.workspace_provider_factory import build_workspace_provider


def _settings(**overrides: object) -> Settings:
    return Settings(
        _env_file=None,
        database_url="postgresql://test:test@127.0.0.1:5432/test",
        internal_token="test-internal-token-not-a-real-secret",
        **overrides,
    )


async def test_default_provider_is_disabled_and_project_scoped() -> None:
    provider = build_workspace_provider(_settings())
    project_id = uuid4()

    status = await provider.status(project_id)

    assert isinstance(provider, DisabledWorkspaceProvider)
    assert status.project_id == project_id
    assert status.provider == "disabled"
    assert status.enabled is False
    assert status.ready is False
    assert status.state == "disabled"
    assert status.detail == "workspace provider is disabled"


async def test_selected_enabled_docker_owner_provider_remains_unsupported() -> None:
    provider = build_workspace_provider(
        _settings(
            workspace_provider="docker_owner_canary",
            docker_owner_canary_enabled=True,
        )
    )
    project_id = uuid4()

    status = await provider.status(project_id)

    assert isinstance(provider, DockerOwnerCanaryProvider)
    assert status.project_id == project_id
    assert status.provider == "docker_owner_canary"
    assert status.enabled is True
    assert status.ready is False
    assert status.state == "unsupported"
    assert status.detail == "docker owner canary is unsupported in the foundation"


@pytest.mark.parametrize(
    ("workspace_provider", "docker_owner_canary_enabled", "expected_type"),
    [
        ("disabled", False, DisabledWorkspaceProvider),
        ("disabled", True, DisabledWorkspaceProvider),
        ("docker_owner_canary", False, DisabledWorkspaceProvider),
        ("docker_owner_canary", True, DockerOwnerCanaryProvider),
    ],
)
def test_factory_requires_explicit_selection_and_enablement(
    workspace_provider: str,
    docker_owner_canary_enabled: bool,
    expected_type: type[DisabledWorkspaceProvider] | type[DockerOwnerCanaryProvider],
) -> None:
    provider = build_workspace_provider(
        _settings(
            workspace_provider=workspace_provider,
            docker_owner_canary_enabled=docker_owner_canary_enabled,
        )
    )

    assert isinstance(provider, expected_type)


@pytest.mark.parametrize(
    "provider",
    [DisabledWorkspaceProvider(), DockerOwnerCanaryProvider()],
    ids=["disabled", "docker-owner-canary"],
)
async def test_every_provider_mutator_is_unavailable(
    provider: DisabledWorkspaceProvider | DockerOwnerCanaryProvider,
) -> None:
    workspace_id = uuid4()
    spec = WorkspaceSpec(
        workspace_id=workspace_id,
        project_id=uuid4(),
        owner_id=uuid4(),
        profile_version="foundation-v1",
    )
    mutators = (
        provider.ensure(spec),
        provider.wake(workspace_id),
        provider.pause(workspace_id, "checkpoint-dummy"),
        provider.destroy(workspace_id),
        provider.execute_control(workspace_id, ControlAction(kind="wake")),
    )

    for mutation in mutators:
        with pytest.raises(WorkspaceProviderUnavailable):
            await mutation


def test_provider_dtos_are_immutable_and_control_dtos_are_minimal() -> None:
    workspace_id = uuid4()
    handle = WorkspaceHandle(
        workspace_id=workspace_id,
        provider="disabled",
        provider_ref="none",
    )
    action = ControlAction(kind="status")
    result = ControlResult(ok=False, detail="not executed")

    with pytest.raises(FrozenInstanceError):
        handle.provider_ref = "changed"  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        action.kind = "wake"  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        result.ok = True  # type: ignore[misc]
    assert [field.name for field in fields(ControlAction)] == ["kind"]
    assert [field.name for field in fields(ControlResult)] == ["ok", "detail"]


def test_task4_import_graph_contains_no_lifecycle_modules_or_functions() -> None:
    modules = (
        disabled_workspace_provider,
        docker_owner_canary_provider,
        workspace_provider_factory,
    )
    forbidden_origins = (
        "docker_client",
        "provisioner",
        "subprocess",
        "core.shell",
        "browser",
    )

    for module in modules:
        imported_origins = {
            value.__name__ if isinstance(value, ModuleType) else getattr(value, "__module__", "")
            for value in vars(module).values()
        }
        assert all(
            forbidden not in origin
            for origin in imported_origins
            for forbidden in forbidden_origins
        )


def test_status_contract_uses_opaque_uuid_not_a_runtime_handle() -> None:
    status_annotation = DisabledWorkspaceProvider.status.__annotations__["project_id"]

    assert status_annotation in (UUID, "UUID")
