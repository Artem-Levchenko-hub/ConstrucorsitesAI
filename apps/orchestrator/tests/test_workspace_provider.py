from __future__ import annotations

import ast
import inspect
from dataclasses import FrozenInstanceError, fields
from pathlib import Path
from typing import Any, cast
from uuid import UUID, uuid4

import pytest

from omnia_orchestrator.core import workspace_provider as workspace_provider_contract
from omnia_orchestrator.core.cell_resources import CellResourceError, LifecycleMutation
from omnia_orchestrator.core.config import Settings, get_settings
from omnia_orchestrator.core.workspace_provider import (
    ControlAction,
    WorkspaceHandle,
    WorkspaceProviderUnavailable,
    WorkspaceResourceStatus,
    WorkspaceSpec,
)
from omnia_orchestrator.routers import workspace as workspace_router
from omnia_orchestrator.schemas import workspace as workspace_schema
from omnia_orchestrator.services import (
    disabled_workspace_provider,
    docker_owner_canary_provider,
    workspace_provider_factory,
)
from omnia_orchestrator.services.cell_checkpoint import (
    CellCheckpointManager,
    CheckpointManifest,
)
from omnia_orchestrator.services.disabled_workspace_provider import DisabledWorkspaceProvider
from omnia_orchestrator.services.docker_cell_resources import DockerVolumeRecord
from omnia_orchestrator.services.docker_owner_canary_provider import (
    DockerOwnerCanaryProvider,
)
from omnia_orchestrator.services.docker_py_cell_backend import DockerPyCellBackend
from omnia_orchestrator.services.workspace_provider_factory import build_workspace_provider
from tests.test_cell_checkpoint import _make_fixture as _make_checkpoint_fixture

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
)
_FORBIDDEN_FOUNDATION_CALLS = frozenset({"exec_cmd", "run_sandbox_command"})


class FailingCompositeCheckpointManager(CellCheckpointManager):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.create_calls = 0

    async def create(
        self,
        workspace_id: UUID,
        checkpoint_ref: str,
        mutation: LifecycleMutation,
        *,
        record_operation: bool = True,
    ) -> CheckpointManifest:
        manifest = await super().create(
            workspace_id,
            checkpoint_ref,
            mutation,
            record_operation=record_operation,
        )
        self.create_calls += 1
        if self.create_calls == 1:
            raise RuntimeError("checkpoint sealed before control mutation")
        return manifest


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
        **cast(dict[str, Any], overrides),
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


async def test_selected_enabled_docker_owner_provider_is_ready(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        workspace_provider_factory,
        "_host_supports_live_docker_provider",
        lambda: True,
    )
    provider = build_workspace_provider(
        _settings(
            workspace_provider="docker_owner_canary",
            docker_owner_canary_enabled=True,
            cell_postgres_image="postgres@sha256:" + "1" * 64,
            cell_redis_image="redis@sha256:" + "2" * 64,
            cell_backup_image="alpine@sha256:" + "3" * 64,
        )
    )
    project_id = uuid4()

    status = await provider.status(project_id)

    assert isinstance(provider, DockerOwnerCanaryProvider)
    assert status.project_id == project_id
    assert status.provider == "docker_owner_canary"
    assert status.enabled is True
    assert status.ready is True
    assert status.state == "ready"
    assert status.detail == "docker owner canary is ready"


async def test_release_control_clears_generation_without_stopping_compute(tmp_path: Path) -> None:
    manager, checkpoints, docker = _make_checkpoint_fixture(tmp_path)
    provider = DockerOwnerCanaryProvider(
        resource_manager=manager,
        checkpoint_manager=checkpoints,
    )
    workspace_id = uuid4()
    run_id = uuid4()
    spec = WorkspaceSpec(
        workspace_id=workspace_id,
        project_id=uuid4(),
        owner_id=uuid4(),
        profile_version="docker-owner-cell-resources-v1",
        generation_run_id=run_id,
    )
    await provider.ensure(spec, LifecycleMutation(uuid4(), 1, "a" * 64))
    running_before = {
        name for name, record in docker.containers.items() if record.state == "running"
    }

    release_mutation = LifecycleMutation(uuid4(), 2, "b" * 64)
    result = await provider.execute_control(
        workspace_id,
        ControlAction(kind="release"),
        release_mutation,
    )
    replay = await provider.execute_control(
        workspace_id,
        ControlAction(kind="release"),
        release_mutation,
    )

    state = manager.state_store.load(workspace_id)
    assert state is not None
    assert state.active_generation_run_id is None
    assert result.state == "resources_ready"
    assert replay == result
    assert {
        name for name, record in docker.containers.items() if record.state == "running"
    } == running_before


def test_enabled_factory_builds_live_resource_dependencies(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        workspace_provider_factory,
        "_host_supports_live_docker_provider",
        lambda: True,
    )
    state_path = tmp_path / "runtime-state" / "project-cells.json"
    provider = build_workspace_provider(
        _settings(
            workspace_provider="docker_owner_canary",
            docker_owner_canary_enabled=True,
            cell_state_path=str(state_path),
            cell_postgres_image="postgres@sha256:" + "1" * 64,
            cell_redis_image="redis@sha256:" + "2" * 64,
            cell_backup_image="alpine@sha256:" + "3" * 64,
        )
    )

    assert isinstance(provider, DockerOwnerCanaryProvider)
    assert provider.resource_manager is not None
    assert provider.checkpoint_manager is not None
    assert isinstance(provider.resource_manager.docker, DockerPyCellBackend)
    assert provider.resource_manager.docker.exec_memory_limit_bytes == 1024**3
    assert provider.resource_manager.docker.exec_cpu_cores == 0.5
    assert provider.resource_manager.profile.full_quota.memory_bytes == 5 * 1024**3
    assert provider.resource_manager.profile.full_quota.cpu_cores == 2.5
    assert provider.resource_manager.docker is provider.checkpoint_manager.docker
    assert (
        provider.resource_manager.state_store
        is provider.checkpoint_manager.state_store
    )
    assert (
        provider.resource_manager.credential_store
        is provider.checkpoint_manager.credential_store
    )
    assert provider.resource_manager.state_store.path == state_path
    assert provider.resource_manager.credential_store.root == (
        tmp_path / "runtime-state" / "project-cells-credentials"
    )
    assert provider.resource_manager.operation_lock.root == tmp_path / "runtime-state"


async def test_enabled_factory_fails_closed_on_windows_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        workspace_provider_factory,
        "_host_supports_live_docker_provider",
        lambda: False,
    )
    provider = build_workspace_provider(
        _settings(
            workspace_provider="docker_owner_canary",
            docker_owner_canary_enabled=True,
            cell_postgres_image="postgres@sha256:" + "1" * 64,
            cell_redis_image="redis@sha256:" + "2" * 64,
            cell_backup_image="alpine@sha256:" + "3" * 64,
        )
    )

    status = await provider.status(uuid4())

    assert isinstance(provider, DockerOwnerCanaryProvider)
    assert provider.resource_manager is None
    assert provider.checkpoint_manager is None
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
    overrides: dict[str, object] = {}
    if docker_owner_canary_enabled:
        overrides = {
            "cell_postgres_image": "postgres@sha256:" + "1" * 64,
            "cell_redis_image": "redis@sha256:" + "2" * 64,
            "cell_backup_image": "alpine@sha256:" + "3" * 64,
        }
    provider = build_workspace_provider(
        _settings(
            workspace_provider=workspace_provider,
            docker_owner_canary_enabled=docker_owner_canary_enabled,
            **overrides,
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
    mutation = LifecycleMutation(uuid4(), 1, "a" * 64)
    spec = WorkspaceSpec(
        workspace_id=workspace_id,
        project_id=uuid4(),
        owner_id=uuid4(),
        profile_version="foundation-v1",
    )
    mutators = (
        provider.ensure(spec, mutation),
        provider.wake(workspace_id, mutation),
        provider.pause(workspace_id, "checkpoint-dummy", mutation),
        provider.destroy(workspace_id, mutation),
        provider.inspect_resources(workspace_id),
        provider.observe_resources(workspace_id, mutation),
        provider.execute_control(workspace_id, ControlAction(kind="wake"), mutation),
    )

    for call in mutators:
        with pytest.raises(WorkspaceProviderUnavailable):
            await call


def test_provider_dtos_are_immutable_and_control_dtos_are_minimal() -> None:
    workspace_id = uuid4()
    handle = WorkspaceHandle(
        workspace_id=workspace_id,
        provider="disabled",
        provider_ref="none",
    )
    action = ControlAction(kind="wake")
    result = WorkspaceResourceStatus(
        workspace_id=workspace_id,
        state="retained",
        provider_ref=None,
        fencing_epoch=None,
        checkpoint_ref=None,
        has_workspace=False,
        has_agent_home=False,
        has_postgres=False,
        has_redis=False,
    )

    with pytest.raises(FrozenInstanceError):
        handle.provider_ref = "changed"  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        action.kind = "wake"  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        result.state = "resources_ready"  # type: ignore[misc]
    assert [field.name for field in fields(ControlAction)] == ["kind", "checkpoint_ref"]
    assert [field.name for field in fields(WorkspaceResourceStatus)] == [
        "workspace_id",
        "state",
        "provider_ref",
        "fencing_epoch",
        "checkpoint_ref",
        "has_workspace",
        "has_agent_home",
        "has_postgres",
        "has_redis",
    ]


async def test_resource_provider_delegates_without_legacy_provisioner(tmp_path: Path) -> None:
    manager, checkpoints, docker = _make_checkpoint_fixture(tmp_path)
    provider = DockerOwnerCanaryProvider(
        resource_manager=manager,
        checkpoint_manager=checkpoints,
    )
    workspace_id = UUID("00000000-0000-0000-0000-000000000001")
    spec = WorkspaceSpec(
        workspace_id=workspace_id,
        project_id=UUID("00000000-0000-0000-0000-000000000002"),
        owner_id=UUID("00000000-0000-0000-0000-000000000003"),
        profile_version="docker-owner-cell-resources-v1",
    )
    ensure_mutation = LifecycleMutation(uuid4(), 1, "a" * 64)
    control_mutation = LifecycleMutation(uuid4(), 2, "b" * 64)

    handle = await provider.ensure(spec, ensure_mutation)
    status = await provider.execute_control(
        workspace_id,
        ControlAction(kind="pause", checkpoint_ref="accepted-1"),
        control_mutation,
    )

    assert handle.provider == "docker_owner_canary"
    assert status.state == "resources_paused"
    assert status.checkpoint_ref == "accepted-1"
    assert docker.begin_operation_calls == 1


async def test_composite_pause_records_single_outer_action_journal(tmp_path: Path) -> None:
    manager, checkpoints, _docker = _make_checkpoint_fixture(tmp_path)
    provider = DockerOwnerCanaryProvider(
        resource_manager=manager,
        checkpoint_manager=checkpoints,
    )
    workspace_id = UUID("00000000-0000-0000-0000-000000000001")
    spec = WorkspaceSpec(
        workspace_id=workspace_id,
        project_id=UUID("00000000-0000-0000-0000-000000000002"),
        owner_id=UUID("00000000-0000-0000-0000-000000000003"),
        profile_version="docker-owner-cell-resources-v1",
    )
    await provider.ensure(spec, LifecycleMutation(uuid4(), 1, "a" * 64))

    await provider.execute_control(
        workspace_id,
        ControlAction(kind="pause", checkpoint_ref="accepted-1"),
        LifecycleMutation(uuid4(), 2, "b" * 64),
    )

    state = manager.state_store.load(workspace_id)
    assert state is not None
    assert state.last_operation_id is not None
    operation = state.operation(state.last_operation_id)
    assert operation is not None
    assert operation.kind == "pause"
    assert operation.checkpoint_ref == "accepted-1"
    assert [item.kind for item in state.operations] == ["ensure", "pause"]


@pytest.mark.parametrize(
    ("action", "checkpoint_ref"),
    [
        (ControlAction(kind="pause", checkpoint_ref="accepted-1"), "accepted-1"),
        (ControlAction(kind="destroy"), None),
    ],
    ids=["pause", "destroy"],
)
async def test_composite_control_replays_recorded_checkpoint_failure_without_resealing(
    tmp_path: Path,
    action: ControlAction,
    checkpoint_ref: str | None,
) -> None:
    manager, checkpoints, docker = _make_checkpoint_fixture(tmp_path)
    failing_checkpoints = FailingCompositeCheckpointManager(
        profile_version=checkpoints.profile_version,
        postgres_image=checkpoints.postgres_image,
        docker=checkpoints.docker,
        credential_store=checkpoints.credential_store,
        state_store=checkpoints.state_store,
    )
    provider = DockerOwnerCanaryProvider(
        resource_manager=manager,
        checkpoint_manager=failing_checkpoints,
    )
    workspace_id = UUID("00000000-0000-0000-0000-000000000001")
    spec = WorkspaceSpec(
        workspace_id=workspace_id,
        project_id=UUID("00000000-0000-0000-0000-000000000002"),
        owner_id=UUID("00000000-0000-0000-0000-000000000003"),
        profile_version="docker-owner-cell-resources-v1",
    )
    await provider.ensure(spec, LifecycleMutation(uuid4(), 1, "a" * 64))
    mutation = LifecycleMutation(uuid4(), 2, "b" * 64)
    effective_checkpoint_ref = (
        checkpoint_ref
        if checkpoint_ref is not None
        else f"final-{mutation.fencing_epoch}-{mutation.operation_id.hex}"
    )

    with pytest.raises(RuntimeError, match="checkpoint sealed before control mutation"):
        await provider.execute_control(workspace_id, action, mutation)

    state = manager.state_store.load(workspace_id)
    assert state is not None
    assert state.bundle_state == "resources_ready"
    assert state.phase == "failed"
    assert state.last_operation_id == mutation.operation_id
    operation = state.operation(mutation.operation_id)
    assert operation is not None
    assert operation.kind == action.kind
    assert operation.status == "failed"
    assert operation.checkpoint_ref == effective_checkpoint_ref
    assert operation.detail == "checkpoint sealed before control mutation"
    names = state.resource_names
    assert names is not None
    checkpoint_files = await docker.read_volume_files(names.checkpoint_volume)
    assert f"{effective_checkpoint_ref}/manifest.json" in checkpoint_files
    create_calls_before_replay = failing_checkpoints.create_calls

    with pytest.raises(CellResourceError, match="checkpoint sealed before control mutation"):
        await provider.execute_control(workspace_id, action, mutation)

    assert failing_checkpoints.create_calls == create_calls_before_replay


async def test_observe_resources_surfaces_conflict_state(tmp_path: Path) -> None:
    manager, checkpoints, docker = _make_checkpoint_fixture(tmp_path)
    provider = DockerOwnerCanaryProvider(
        resource_manager=manager,
        checkpoint_manager=checkpoints,
    )
    workspace_id = UUID("00000000-0000-0000-0000-000000000001")
    spec = WorkspaceSpec(
        workspace_id=workspace_id,
        project_id=UUID("00000000-0000-0000-0000-000000000002"),
        owner_id=UUID("00000000-0000-0000-0000-000000000003"),
        profile_version="docker-owner-cell-resources-v1",
    )
    await provider.ensure(spec, LifecycleMutation(uuid4(), 1, "a" * 64))
    state = manager.state_store.load(workspace_id)
    assert state is not None and state.resource_names is not None
    docker.seed_volume(
        state.resource_names.workspace_volume,
        {
            "omnia.managed": "true",
            "omnia.project_cell": "true",
            "omnia.workspace_id": str(workspace_id),
            "omnia.project_id": str(spec.project_id),
            "omnia.owner_id": str(spec.owner_id),
            "omnia.provider": "docker_owner_canary",
            "omnia.profile_version": spec.profile_version,
            "omnia.resource_kind": "workspace",
        },
    )
    docker.volumes[state.resource_names.workspace_volume] = DockerVolumeRecord(
        resource_id="seed-volume-conflict",
        name=state.resource_names.workspace_volume,
        labels={"omnia.workspace_id": "different"},
        files={},
    )

    status = await provider.observe_resources(
        workspace_id,
        LifecycleMutation(uuid4(), 2, "b" * 64),
    )

    assert status.state == "conflict"


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
        main_path = (
            Path(__file__).resolve().parents[1]
            / "src"
            / "omnia_orchestrator"
            / "main.py"
        )
        tree = ast.parse(main_path.read_text(encoding="utf-8"))

        assert _workspace_registration_count(tree) == 1
        assert _main_provider_implementation_imports(tree) == []
    finally:
        get_settings.cache_clear()


def test_status_contract_uses_opaque_uuid_not_a_runtime_handle() -> None:
    status_annotation = DisabledWorkspaceProvider.status.__annotations__["project_id"]

    assert status_annotation in (UUID, "UUID")
