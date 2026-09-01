from __future__ import annotations

import ast
import importlib
import inspect
from dataclasses import FrozenInstanceError, fields
from uuid import UUID, uuid4

import pytest

from omnia_orchestrator.core import workspace_provider as workspace_provider_contract
from omnia_orchestrator.core.config import Settings, get_settings
from omnia_orchestrator.core.workspace_provider import (
    ControlAction,
    ControlResult,
    WorkspaceHandle,
    WorkspaceProviderUnavailable,
    WorkspaceSpec,
)
from omnia_orchestrator.routers import workspace as workspace_router
from omnia_orchestrator.schemas import workspace as workspace_schema
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

_FORBIDDEN_FOUNDATION_IMPORT_FRAGMENTS = (
    "docker_client",
    "provisioner",
    "subprocess",
    "core.shell",
    "browser",
    "network",
    "httpx",
    "requests",
    "urllib",
    "socket",
    "aiohttp",
    "lifecycle",
)
_FORBIDDEN_FOUNDATION_CALLS = frozenset({"exec_cmd", "run_sandbox_command"})


def _node_dotted_name(expression: ast.expr) -> str:
    if isinstance(expression, ast.Name):
        return expression.id
    if isinstance(expression, ast.Attribute):
        prefix = _node_dotted_name(expression.value)
        return f"{prefix}.{expression.attr}" if prefix else expression.attr
    return ""


def _foundation_boundary_violations(tree: ast.AST) -> list[str]:
    violations: set[str] = set()
    forbidden_call_aliases = set(_FORBIDDEN_FOUNDATION_CALLS)

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports = (
                (alias.name, alias.asname or alias.name.rsplit(".", 1)[-1])
                for alias in node.names
            )
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            imports = (
                (
                    f"{module}.{alias.name}" if module else alias.name,
                    alias.asname or alias.name,
                )
                for alias in node.names
            )
        else:
            continue
        for qualified, bound_name in imports:
            if any(
                fragment in qualified.casefold()
                for fragment in _FORBIDDEN_FOUNDATION_IMPORT_FRAGMENTS
            ):
                violations.add(f"import:{qualified}@{node.lineno}")
                forbidden_call_aliases.add(bound_name)

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        called = _node_dotted_name(node.func)
        final_name = called.rsplit(".", 1)[-1]
        if called in forbidden_call_aliases or final_name in _FORBIDDEN_FOUNDATION_CALLS:
            violations.add(f"call:{called}@{node.lineno}")
        if final_name in {"__import__", "import_module"} and node.args:
            imported = node.args[0]
            if isinstance(imported, ast.Constant) and isinstance(imported.value, str):
                if any(
                    fragment in imported.value.casefold()
                    for fragment in _FORBIDDEN_FOUNDATION_IMPORT_FRAGMENTS
                ):
                    violations.add(f"dynamic-import:{imported.value}@{node.lineno}")

    return sorted(violations)


def _workspace_registration_count(tree: ast.AST) -> int:
    workspace_aliases: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "omnia_orchestrator.routers":
            for alias in node.names:
                if alias.name == "workspace":
                    workspace_aliases.add(alias.asname or alias.name)

    count = 0
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not node.args:
            continue
        if not isinstance(node.func, ast.Attribute) or node.func.attr != "include_router":
            continue
        argument = node.args[0]
        if (
            isinstance(argument, ast.Attribute)
            and argument.attr == "router"
            and isinstance(argument.value, ast.Name)
            and argument.value.id in workspace_aliases
        ):
            count += 1
    return count


def _main_provider_implementation_imports(tree: ast.AST) -> list[str]:
    forbidden = (
        "core.workspace_provider",
        "disabled_workspace_provider",
        "docker_owner_canary_provider",
        "workspace_provider_factory",
    )
    imports: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            qualified_names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            qualified_names = [f"{module}.{alias.name}" for alias in node.names]
        else:
            continue
        imports.extend(
            qualified
            for qualified in qualified_names
            if any(fragment in qualified for fragment in forbidden)
        )
    return imports


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


def test_foundation_ast_guard_detects_function_local_lifecycle_imports_and_calls() -> None:
    mutated_provider = ast.parse(
        """
async def ensure(workspace_id):
    from omnia_orchestrator.core.docker_client import container_status
    from omnia_orchestrator.core.shell import exec_cmd
    return await run_sandbox_command(workspace_id)
"""
    )

    violations = _foundation_boundary_violations(mutated_provider)

    assert any(item.startswith("import:") for item in violations)
    assert any("exec_cmd" in item for item in violations)
    assert any("run_sandbox_command" in item for item in violations)


def test_whole_task4_foundation_import_graph_is_lifecycle_free() -> None:
    modules = (
        workspace_provider_contract,
        workspace_schema,
        workspace_router,
        workspace_provider_factory,
        disabled_workspace_provider,
        docker_owner_canary_provider,
    )

    for module in modules:
        tree = ast.parse(inspect.getsource(module))
        assert _foundation_boundary_violations(tree) == [], module.__name__


def test_main_registers_workspace_router_without_importing_provider_implementation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql://test:test@127.0.0.1:5432/test",
    )
    monkeypatch.setenv("INTERNAL_TOKEN", "test-internal-token-not-a-real-secret")
    get_settings.cache_clear()
    try:
        orchestrator_main = importlib.import_module("omnia_orchestrator.main")
        tree = ast.parse(inspect.getsource(orchestrator_main))

        assert _workspace_registration_count(tree) == 1
        assert _main_provider_implementation_imports(tree) == []
    finally:
        get_settings.cache_clear()


def test_status_contract_uses_opaque_uuid_not_a_runtime_handle() -> None:
    status_annotation = DisabledWorkspaceProvider.status.__annotations__["project_id"]

    assert status_annotation in (UUID, "UUID")
