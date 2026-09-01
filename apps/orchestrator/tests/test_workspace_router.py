from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from types import SimpleNamespace
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import FastAPI

from omnia_orchestrator.core.config import get_settings
from omnia_orchestrator.core.errors import OrchestratorError, orchestrator_error_handler
from omnia_orchestrator.core.workspace_provider import (
    WorkspaceHandle,
    WorkspaceResourceStatus,
    WorkspaceSpec,
    WorkspaceStatus,
)
from omnia_orchestrator.routers import workspace
from omnia_orchestrator.services.docker_cell_resources import DockerCommandResult
from omnia_orchestrator.services.docker_owner_canary_provider import DockerOwnerCanaryProvider
from tests._cell_fakes import FakeDockerBackend
from tests.test_cell_checkpoint import _make_fixture as _make_checkpoint_fixture

_DEFAULT_GENERATION_RUN_ID = UUID("00000000-0000-0000-0000-000000000094")


class _RecordingProvider:
    def __init__(self) -> None:
        self.project_ids: list[UUID] = []
        self.ensure_calls: list[dict[str, object]] = []
        self.control_calls: list[dict[str, object]] = []
        self.observe_calls: list[dict[str, object]] = []
        self.inspect_calls: list[UUID] = []

    async def status(self, project_id: UUID) -> WorkspaceStatus:
        self.project_ids.append(project_id)
        return WorkspaceStatus(
            project_id=project_id,
            provider="docker_owner_canary",
            enabled=True,
            ready=False,
            state="unsupported",
            detail="docker owner canary is unsupported in the foundation",
        )

    async def ensure(self, spec, mutation) -> WorkspaceHandle:
        self.ensure_calls.append(
            {
                "workspace_id": spec.workspace_id,
                "project_id": spec.project_id,
                "owner_id": spec.owner_id,
                "generation_run_id": spec.generation_run_id,
                "profile_version": spec.profile_version,
                "operation_id": mutation.operation_id,
                "fencing_epoch": mutation.fencing_epoch,
                "request_digest": mutation.request_digest,
            }
        )
        return WorkspaceHandle(
            workspace_id=spec.workspace_id,
            provider="docker_owner_canary",
            provider_ref="cell-1",
        )

    async def execute_control(
        self,
        workspace_id: UUID,
        action,
        mutation,
    ) -> WorkspaceResourceStatus:
        self.control_calls.append(
            {
                "workspace_id": workspace_id,
                "kind": action.kind,
                "checkpoint_ref": action.checkpoint_ref,
                "operation_id": mutation.operation_id,
                "fencing_epoch": mutation.fencing_epoch,
                "request_digest": mutation.request_digest,
            }
        )
        return WorkspaceResourceStatus(
            workspace_id=workspace_id,
            state="resources_paused" if action.kind in {"pause", "stop", "restore"} else "retained",
            provider_ref="cell-1",
            fencing_epoch=mutation.fencing_epoch,
            checkpoint_ref=action.checkpoint_ref,
            has_workspace=True,
            has_agent_home=True,
            has_postgres=action.kind not in {"destroy"},
            has_redis=action.kind not in {"destroy"},
        )

    async def observe_resources(self, workspace_id: UUID, mutation) -> WorkspaceResourceStatus:
        self.observe_calls.append(
            {
                "workspace_id": workspace_id,
                "operation_id": mutation.operation_id,
                "fencing_epoch": mutation.fencing_epoch,
                "request_digest": mutation.request_digest,
            }
        )
        return WorkspaceResourceStatus(
            workspace_id=workspace_id,
            state="resources_ready",
            provider_ref="cell-1",
            fencing_epoch=mutation.fencing_epoch,
            checkpoint_ref=None,
            has_workspace=True,
            has_agent_home=True,
            has_postgres=True,
            has_redis=True,
        )

    async def inspect_resources(self, workspace_id: UUID) -> WorkspaceResourceStatus:
        self.inspect_calls.append(workspace_id)
        return WorkspaceResourceStatus(
            workspace_id=workspace_id,
            state="resources_paused",
            provider_ref="cell-1",
            fencing_epoch=7,
            checkpoint_ref="accepted-1",
            has_workspace=True,
            has_agent_home=True,
            has_postgres=True,
            has_redis=True,
        )


class _BlockingWorkspaceCommandDocker(FakeDockerBackend):
    def __init__(self) -> None:
        super().__init__()
        self.exec_started = asyncio.Event()
        self.release_exec = asyncio.Event()
        self.write_started = asyncio.Event()
        self.events: list[str] = []

    async def run_workspace_command(self, **kwargs: object) -> DockerCommandResult:
        self.events.append("exec-start")
        self.exec_started.set()
        await self.release_exec.wait()
        self.events.append("exec-finish")
        return await super().run_workspace_command(**kwargs)

    async def write_volume_files(self, name: str, files: dict[str, bytes]) -> None:
        self.events.append("write")
        self.write_started.set()
        await super().write_volume_files(name, files)


class _CountingBootstrapDocker(FakeDockerBackend):
    def __init__(self) -> None:
        super().__init__()
        self.clear_calls = 0
        self.write_calls = 0

    async def clear_volume(self, name: str) -> None:
        self.clear_calls += 1
        await super().clear_volume(name)

    async def write_volume_files(self, name: str, files: dict[str, bytes]) -> None:
        self.write_calls += 1
        await super().write_volume_files(name, files)


async def _ready_provider(
    tmp_path,
    workspace_id: UUID,
    *,
    generation_run_id: UUID | None = _DEFAULT_GENERATION_RUN_ID,
    docker: FakeDockerBackend | None = None,
) -> tuple[DockerOwnerCanaryProvider, object, FakeDockerBackend, UUID | None]:
    manager, checkpoints, docker_backend = _make_checkpoint_fixture(tmp_path, docker=docker)
    provider = DockerOwnerCanaryProvider(
        resource_manager=manager,
        checkpoint_manager=checkpoints,
    )
    spec = WorkspaceSpec(
        workspace_id=workspace_id,
        project_id=UUID("00000000-0000-0000-0000-000000000092"),
        owner_id=UUID("00000000-0000-0000-0000-000000000093"),
        profile_version="docker-owner-cell-resources-v1",
        generation_run_id=generation_run_id,
    )
    await provider.ensure(spec, workspace.LifecycleMutation(UUID(int=95), 4, "a" * 64))
    return provider, manager, docker_backend, generation_run_id


@asynccontextmanager
async def _client() -> AsyncIterator[httpx.AsyncClient]:
    app = FastAPI()
    app.add_exception_handler(OrchestratorError, orchestrator_error_handler)
    app.include_router(workspace.router)
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


@pytest.fixture(autouse=True)
def _internal_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql://test:test@127.0.0.1:5432/test",
    )
    monkeypatch.setenv("INTERNAL_TOKEN", "test-internal-token-not-a-real-secret")
    monkeypatch.delenv("WORKSPACE_PROVIDER", raising=False)
    monkeypatch.delenv("DOCKER_OWNER_CANARY_ENABLED", raising=False)
    get_settings.cache_clear()  # type: ignore[attr-defined]
    yield
    get_settings.cache_clear()  # type: ignore[attr-defined]


async def test_workspace_capabilities_authenticates_before_provider_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = _RecordingProvider()
    factory_calls = 0

    def build_provider(_settings: object) -> _RecordingProvider:
        nonlocal factory_calls
        factory_calls += 1
        return provider

    monkeypatch.setattr(workspace, "build_workspace_provider", build_provider)
    project_id = uuid4()
    path = f"/internal/projects/{project_id}/workspace/capabilities"

    async with _client() as client:
        missing = await client.get(path)
        wrong = await client.get(path, headers={"X-Internal-Token": "wrong-token"})

    assert missing.status_code == 401
    assert wrong.status_code == 401
    assert missing.json() == {
        "error": {
            "code": "unauthorized",
            "message": "missing or invalid X-Internal-Token",
        }
    }
    assert wrong.json() == missing.json()
    assert factory_calls == 0
    assert provider.project_ids == []


@pytest.mark.parametrize(
    ("method", "path", "payload"),
    [
        (
            "post",
            "/internal/workspaces/ensure",
            {
                "workspace_id": str(UUID("00000000-0000-0000-0000-000000000011")),
                "project_id": str(UUID("00000000-0000-0000-0000-000000000012")),
                "owner_id": str(UUID("00000000-0000-0000-0000-000000000013")),
                "generation_run_id": str(UUID("00000000-0000-0000-0000-000000000015")),
                "profile_version": "docker-owner-cell-resources-v1",
                "operation_id": str(UUID("00000000-0000-0000-0000-000000000014")),
                "fencing_epoch": 4,
                "request_digest": "a" * 64,
            },
        ),
        (
            "post",
            f"/internal/workspaces/{UUID('00000000-0000-0000-0000-000000000021')}/control",
            {
                "workspace_id": str(UUID("00000000-0000-0000-0000-000000000021")),
                "kind": "pause",
                "checkpoint_ref": "accepted-1",
                "operation_id": str(UUID("00000000-0000-0000-0000-000000000022")),
                "fencing_epoch": 5,
                "request_digest": "b" * 64,
            },
        ),
        (
            "post",
            f"/internal/workspaces/{UUID('00000000-0000-0000-0000-000000000031')}/resources/observe",
            {
                "workspace_id": str(UUID("00000000-0000-0000-0000-000000000031")),
                "operation_id": str(UUID("00000000-0000-0000-0000-000000000032")),
                "fencing_epoch": 6,
                "request_digest": "c" * 64,
            },
        ),
        (
            "get",
            f"/internal/workspaces/{UUID('00000000-0000-0000-0000-000000000041')}/resources",
            None,
        ),
    ],
)
async def test_internal_resource_routes_authenticate_before_provider(
    monkeypatch: pytest.MonkeyPatch,
    method: str,
    path: str,
    payload: dict[str, object] | None,
) -> None:
    provider = _RecordingProvider()
    factory_calls = 0

    def build_provider(_settings: object) -> _RecordingProvider:
        nonlocal factory_calls
        factory_calls += 1
        return provider

    monkeypatch.setattr(workspace, "build_workspace_provider", build_provider)

    async with _client() as client:
        request = getattr(client, method)
        if payload is None:
            missing = await request(path)
            wrong = await request(path, headers={"X-Internal-Token": "wrong-token"})
        else:
            missing = await request(path, json=payload)
            wrong = await request(path, json=payload, headers={"X-Internal-Token": "wrong-token"})

    assert missing.status_code == 401
    assert wrong.status_code == 401
    assert factory_calls == 0
    assert provider.ensure_calls == []
    assert provider.control_calls == []
    assert provider.observe_calls == []
    assert provider.inspect_calls == []


async def test_authenticated_workspace_capability_response_is_stable_and_dark(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = _RecordingProvider()
    monkeypatch.setattr(workspace, "build_workspace_provider", lambda _settings: provider)
    monkeypatch.setattr(
        workspace,
        "get_settings",
        lambda: SimpleNamespace(
            workspace_provider="docker_owner_canary",
            docker_owner_canary_enabled=True,
            internal_token="must-not-leak",
        ),
    )
    project_id = uuid4()
    path = f"/internal/projects/{project_id}/workspace/capabilities"

    async with _client() as client:
        response = await client.get(
            path,
            headers={"X-Internal-Token": "test-internal-token-not-a-real-secret"},
        )

    assert response.status_code == 200
    assert response.json() == {
        "project_id": str(project_id),
        "provider": "docker_owner_canary",
        "enabled": True,
        "ready": False,
        "state": "unsupported",
        "detail": "docker owner canary is unsupported in the foundation",
    }
    serialized = response.text
    assert "test-internal-token" not in serialized
    assert "must-not-leak" not in serialized
    assert provider.project_ids == [project_id]


async def test_default_capability_route_is_disabled_and_get_only() -> None:
    project_id = uuid4()
    path = f"/internal/projects/{project_id}/workspace/capabilities"
    headers = {"X-Internal-Token": "test-internal-token-not-a-real-secret"}

    async with _client() as client:
        response = await client.get(path, headers=headers)
        mutation = await client.post(path, headers=headers)

    assert response.status_code == 200
    assert response.json() == {
        "project_id": str(project_id),
        "provider": "disabled",
        "enabled": False,
        "ready": False,
        "state": "disabled",
        "detail": "workspace provider is disabled",
    }
    assert mutation.status_code == 405


async def test_authenticated_resource_routes_delegate_and_hide_secrets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = _RecordingProvider()
    monkeypatch.setattr(workspace, "build_workspace_provider", lambda _settings: provider)
    workspace_id = UUID("00000000-0000-0000-0000-000000000051")
    project_id = UUID("00000000-0000-0000-0000-000000000052")
    owner_id = UUID("00000000-0000-0000-0000-000000000053")
    headers = {"X-Internal-Token": "test-internal-token-not-a-real-secret"}

    async with _client() as client:
        ensure = await client.post(
            "/internal/workspaces/ensure",
            headers=headers,
            json={
                "workspace_id": str(workspace_id),
                "project_id": str(project_id),
                "owner_id": str(owner_id),
                "generation_run_id": str(UUID("00000000-0000-0000-0000-000000000057")),
                "profile_version": "docker-owner-cell-resources-v1",
                "operation_id": str(UUID("00000000-0000-0000-0000-000000000054")),
                "fencing_epoch": 4,
                "request_digest": "a" * 64,
            },
        )
        control = await client.post(
            f"/internal/workspaces/{workspace_id}/control",
            headers=headers,
            json={
                "workspace_id": str(workspace_id),
                "kind": "pause",
                "checkpoint_ref": "accepted-1",
                "operation_id": str(UUID("00000000-0000-0000-0000-000000000055")),
                "fencing_epoch": 5,
                "request_digest": "b" * 64,
            },
        )
        observe = await client.post(
            f"/internal/workspaces/{workspace_id}/resources/observe",
            headers=headers,
            json={
                "workspace_id": str(workspace_id),
                "operation_id": str(UUID("00000000-0000-0000-0000-000000000056")),
                "fencing_epoch": 6,
                "request_digest": "c" * 64,
            },
        )
        inspect_response = await client.get(
            f"/internal/workspaces/{workspace_id}/resources",
            headers=headers,
        )

    assert ensure.status_code == 200
    assert ensure.json() == {
        "workspace_id": str(workspace_id),
        "state": "resources_ready",
        "provider_ref": "cell-1",
        "fencing_epoch": 4,
        "checkpoint_ref": None,
        "has_workspace": True,
        "has_agent_home": True,
        "has_postgres": True,
        "has_redis": True,
    }
    assert control.status_code == 200
    assert control.json()["checkpoint_ref"] == "accepted-1"
    assert observe.status_code == 200
    assert observe.json()["state"] == "resources_ready"
    assert inspect_response.status_code == 200
    assert inspect_response.json()["checkpoint_ref"] == "accepted-1"
    assert provider.ensure_calls == [
        {
            "workspace_id": workspace_id,
            "project_id": project_id,
            "owner_id": owner_id,
            "generation_run_id": UUID("00000000-0000-0000-0000-000000000057"),
            "profile_version": "docker-owner-cell-resources-v1",
            "operation_id": UUID("00000000-0000-0000-0000-000000000054"),
            "fencing_epoch": 4,
            "request_digest": "a" * 64,
        }
    ]
    assert provider.control_calls[0]["kind"] == "pause"
    assert provider.observe_calls[0]["fencing_epoch"] == 6
    assert provider.inspect_calls == [workspace_id]
    serialized = ensure.text + control.text + observe.text + inspect_response.text
    assert "test-internal-token" not in serialized


async def test_resource_route_rejects_workspace_id_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = _RecordingProvider()
    monkeypatch.setattr(workspace, "build_workspace_provider", lambda _settings: provider)
    workspace_id = UUID("00000000-0000-0000-0000-000000000061")

    async with _client() as client:
        response = await client.post(
            f"/internal/workspaces/{workspace_id}/control",
            headers={"X-Internal-Token": "test-internal-token-not-a-real-secret"},
            json={
                "workspace_id": str(UUID("00000000-0000-0000-0000-000000000062")),
                "kind": "wake",
                "checkpoint_ref": None,
                "operation_id": str(UUID("00000000-0000-0000-0000-000000000063")),
                "fencing_epoch": 7,
                "request_digest": "d" * 64,
            },
        )

    assert response.status_code == 400
    assert provider.control_calls == []


async def test_agent_bootstrap_rejects_workspace_volume_identity_mismatch(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    workspace_id = UUID("00000000-0000-0000-0000-000000000071")
    provider, manager, docker, _generation_run_id = await _ready_provider(
        tmp_path,
        workspace_id,
        generation_run_id=UUID("00000000-0000-0000-0000-000000000074"),
    )
    state = manager.state_store.load(workspace_id)
    assert state is not None and state.resource_names is not None
    volume = docker.volumes[state.resource_names.workspace_volume]
    docker.volumes[state.resource_names.workspace_volume] = SimpleNamespace(
        resource_id=volume.resource_id,
        name=volume.name,
        labels={"omnia.workspace_id": str(workspace_id), "omnia.resource_kind": "workspace"},
        files=volume.files,
    )
    monkeypatch.setattr(workspace, "build_workspace_provider", lambda _settings: provider)

    async with _client() as client:
        response = await client.post(
            f"/internal/workspaces/{workspace_id}/agent/bootstrap",
            headers={"X-Internal-Token": "test-internal-token-not-a-real-secret"},
            json={
                "generation_run_id": "00000000-0000-0000-0000-000000000074",
                "fencing_epoch": 4,
            },
        )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "conflict"


async def test_agent_bootstrap_requires_active_generation_lease(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    manager, checkpoints, _docker = _make_checkpoint_fixture(tmp_path)
    provider = DockerOwnerCanaryProvider(
        resource_manager=manager,
        checkpoint_manager=checkpoints,
    )
    workspace_id = UUID("00000000-0000-0000-0000-000000000075")
    await provider.ensure(
        WorkspaceSpec(
            workspace_id=workspace_id,
            project_id=UUID("00000000-0000-0000-0000-000000000092"),
            owner_id=UUID("00000000-0000-0000-0000-000000000093"),
            profile_version="docker-owner-cell-resources-v1",
        ),
        workspace.LifecycleMutation(UUID(int=76), 4, "a" * 64),
    )
    monkeypatch.setattr(workspace, "build_workspace_provider", lambda _settings: provider)

    async with _client() as client:
        response = await client.post(
            f"/internal/workspaces/{workspace_id}/agent/bootstrap",
            headers={"X-Internal-Token": "test-internal-token-not-a-real-secret"},
            json={
                "generation_run_id": str(uuid4()),
                "fencing_epoch": 4,
            },
        )

    assert response.status_code == 409
    assert response.json()["error"]["message"] == "workspace generation lease is not active"


async def test_agent_bootstrap_returns_generation_lease_and_revision(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    workspace_id = UUID("00000000-0000-0000-0000-000000000077")
    generation_run_id = UUID("00000000-0000-0000-0000-000000000078")
    provider, manager, docker, _ = await _ready_provider(
        tmp_path,
        workspace_id,
        generation_run_id=generation_run_id,
    )
    state = manager.state_store.load(workspace_id)
    assert state is not None and state.resource_names is not None
    await docker.write_volume_files(
        state.resource_names.workspace_volume,
        {"src/app/page.tsx": b"export default function Page() { return null }\n"},
    )
    monkeypatch.setattr(workspace, "build_workspace_provider", lambda _settings: provider)

    async with _client() as client:
        response = await client.post(
            f"/internal/workspaces/{workspace_id}/agent/bootstrap",
            headers={"X-Internal-Token": "test-internal-token-not-a-real-secret"},
            json={
                "generation_run_id": str(generation_run_id),
                "fencing_epoch": 4,
            },
        )

    assert response.status_code == 200
    assert response.json() == {
        "files": {"src/app/page.tsx": "export default function Page() { return null }\n"},
        "seeded_from_project": False,
        "generation_run_id": str(generation_run_id),
        "fencing_epoch": 4,
        "workspace_revision": workspace._workspace_revision(
            {"src/app/page.tsx": "export default function Page() { return null }\n"}
        ),
    }


async def test_agent_bootstrap_rejects_stale_generation_before_seed_side_effects(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    workspace_id = UUID("00000000-0000-0000-0000-000000000079")
    generation_run_id = UUID("00000000-0000-0000-0000-00000000007a")
    docker = _CountingBootstrapDocker()
    provider, manager, _docker, _ = await _ready_provider(
        tmp_path,
        workspace_id,
        generation_run_id=generation_run_id,
        docker=docker,
    )
    state = manager.state_store.load(workspace_id)
    assert state is not None and state.project_id is not None
    project_root = tmp_path / "projects" / str(state.project_id)
    project_root.mkdir(parents=True, exist_ok=True)
    (project_root / "seed.txt").write_text("seed", encoding="utf-8")
    docker.clear_calls = 0
    docker.write_calls = 0
    monkeypatch.setattr(workspace, "build_workspace_provider", lambda _settings: provider)
    monkeypatch.setattr(workspace, "_project_workspace_dir", lambda _project_id: project_root)

    async with _client() as client:
        response = await client.post(
            f"/internal/workspaces/{workspace_id}/agent/bootstrap",
            headers={"X-Internal-Token": "test-internal-token-not-a-real-secret"},
            json={
                "generation_run_id": str(UUID("00000000-0000-0000-0000-00000000007b")),
                "fencing_epoch": 4,
            },
        )

    assert response.status_code == 409
    assert response.json()["error"]["message"] == "workspace generation lease mismatch"
    assert docker.clear_calls == 0
    assert docker.write_calls == 0


async def test_agent_write_rejects_over_budget_payload(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    workspace_id = UUID("00000000-0000-0000-0000-000000000081")
    generation_run_id = UUID("00000000-0000-0000-0000-000000000084")
    provider, _manager, _docker, _ = await _ready_provider(
        tmp_path,
        workspace_id,
        generation_run_id=generation_run_id,
    )
    monkeypatch.setattr(workspace, "build_workspace_provider", lambda _settings: provider)

    async with _client() as client:
        response = await client.post(
            f"/internal/workspaces/{workspace_id}/agent/write-files",
            headers={"X-Internal-Token": "test-internal-token-not-a-real-secret"},
            json={
                "generation_run_id": str(generation_run_id),
                "fencing_epoch": 4,
                "expected_revision": workspace._workspace_revision({}),
                "files": {"big.txt": "x" * (2 * 1024 * 1024 + 1)},
                "deletes": [],
            },
        )

    assert response.status_code == 413
    assert response.json()["error"]["code"] == "validation_failed"


@pytest.mark.parametrize(
    ("files", "deletes"),
    [
        ({".env": "secret"}, []),
        ({}, [".env.production"]),
    ],
)
async def test_agent_write_rejects_secret_paths(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
    files: dict[str, str],
    deletes: list[str],
) -> None:
    workspace_id = UUID("00000000-0000-0000-0000-00000000008d")
    generation_run_id = UUID("00000000-0000-0000-0000-00000000008e")
    provider, _manager, _docker, _ = await _ready_provider(
        tmp_path,
        workspace_id,
        generation_run_id=generation_run_id,
    )
    monkeypatch.setattr(workspace, "build_workspace_provider", lambda _settings: provider)

    async with _client() as client:
        response = await client.post(
            f"/internal/workspaces/{workspace_id}/agent/write-files",
            headers={"X-Internal-Token": "test-internal-token-not-a-real-secret"},
            json={
                "generation_run_id": str(generation_run_id),
                "fencing_epoch": 4,
                "expected_revision": workspace._workspace_revision({}),
                "files": files,
                "deletes": deletes,
            },
        )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "validation_failed"


async def test_agent_write_uses_explicit_deletes_and_keeps_empty_string_as_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    workspace_id = UUID("00000000-0000-0000-0000-000000000085")
    generation_run_id = UUID("00000000-0000-0000-0000-000000000086")
    provider, manager, docker, _ = await _ready_provider(
        tmp_path,
        workspace_id,
        generation_run_id=generation_run_id,
    )
    state = manager.state_store.load(workspace_id)
    assert state is not None and state.resource_names is not None
    await docker.write_volume_files(
        state.resource_names.workspace_volume,
        {
            "old.txt": b"remove me",
            "keep.txt": b"before",
        },
    )
    base_files = {
        "keep.txt": "before",
        "old.txt": "remove me",
    }
    monkeypatch.setattr(workspace, "build_workspace_provider", lambda _settings: provider)

    async with _client() as client:
        response = await client.post(
            f"/internal/workspaces/{workspace_id}/agent/write-files",
            headers={"X-Internal-Token": "test-internal-token-not-a-real-secret"},
            json={
                "generation_run_id": str(generation_run_id),
                "fencing_epoch": 4,
                "expected_revision": workspace._workspace_revision(base_files),
                "files": {
                    "blank.txt": "",
                    "keep.txt": "after",
                },
                "deletes": ["old.txt"],
            },
        )

    assert response.status_code == 200
    assert response.json() == {
        "written": 2,
        "deleted": 1,
        "workspace_revision": workspace._workspace_revision(
            {"blank.txt": "", "keep.txt": "after"}
        ),
    }
    files = await docker.read_volume_files(state.resource_names.workspace_volume)
    assert files == {
        "blank.txt": b"",
        "keep.txt": b"after",
    }


async def test_agent_write_rejects_stale_revision_before_side_effects(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    workspace_id = UUID("00000000-0000-0000-0000-000000000087")
    generation_run_id = UUID("00000000-0000-0000-0000-000000000088")
    provider, manager, docker, _ = await _ready_provider(
        tmp_path,
        workspace_id,
        generation_run_id=generation_run_id,
    )
    state = manager.state_store.load(workspace_id)
    assert state is not None and state.resource_names is not None
    await docker.write_volume_files(
        state.resource_names.workspace_volume,
        {"current.txt": b"current"},
    )
    monkeypatch.setattr(workspace, "build_workspace_provider", lambda _settings: provider)

    async with _client() as client:
        response = await client.post(
            f"/internal/workspaces/{workspace_id}/agent/write-files",
            headers={"X-Internal-Token": "test-internal-token-not-a-real-secret"},
            json={
                "generation_run_id": str(generation_run_id),
                "fencing_epoch": 4,
                "expected_revision": workspace._workspace_revision({"current.txt": "older"}),
                "files": {"current.txt": "next"},
                "deletes": [],
            },
        )

    assert response.status_code == 409
    assert response.json()["error"]["message"] == "workspace changed"
    files = await docker.read_volume_files(state.resource_names.workspace_volume)
    assert files == {"current.txt": b"current"}


async def test_agent_write_allows_idempotent_retry_when_patch_already_applied(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    workspace_id = UUID("00000000-0000-0000-0000-000000000089")
    generation_run_id = UUID("00000000-0000-0000-0000-00000000008a")
    provider, manager, docker, _ = await _ready_provider(
        tmp_path,
        workspace_id,
        generation_run_id=generation_run_id,
    )
    state = manager.state_store.load(workspace_id)
    assert state is not None and state.resource_names is not None
    await docker.write_volume_files(
        state.resource_names.workspace_volume,
        {"a.txt": b"old"},
    )
    monkeypatch.setattr(workspace, "build_workspace_provider", lambda _settings: provider)
    request = {
        "generation_run_id": str(generation_run_id),
        "fencing_epoch": 4,
        "expected_revision": workspace._workspace_revision({"a.txt": "old"}),
        "files": {"a.txt": "new"},
        "deletes": [],
    }

    async with _client() as client:
        first = await client.post(
            f"/internal/workspaces/{workspace_id}/agent/write-files",
            headers={"X-Internal-Token": "test-internal-token-not-a-real-secret"},
            json=request,
        )
        second = await client.post(
            f"/internal/workspaces/{workspace_id}/agent/write-files",
            headers={"X-Internal-Token": "test-internal-token-not-a-real-secret"},
            json=request,
        )

    assert first.status_code == 200
    assert first.json()["workspace_revision"] == workspace._workspace_revision({"a.txt": "new"})
    assert second.status_code == 200
    assert second.json() == {
        "written": 0,
        "deleted": 0,
        "workspace_revision": workspace._workspace_revision({"a.txt": "new"}),
    }


async def test_exec_workspace_agent_command_runs_inside_cell_bundle(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    workspace_id = UUID("00000000-0000-0000-0000-000000000091")
    generation_run_id = UUID("00000000-0000-0000-0000-000000000094")
    provider, manager, docker, _ = await _ready_provider(
        tmp_path,
        workspace_id,
        generation_run_id=generation_run_id,
    )
    state = manager.state_store.load(workspace_id)
    assert state is not None and state.resource_names is not None
    names = state.resource_names
    credentials = manager.credential_store.load_or_create(workspace_id)
    await docker.write_volume_files(
        names.workspace_volume,
        {"before.txt": b"one"},
    )
    docker.workspace_command_result = DockerCommandResult(
        exit_code=0,
        output="DATABASE_URL=postgresql://postgres:secret@pg:5432/postgres\nbuild clean",
    )
    docker.workspace_command_volume_files = {
        "after.txt": b"two",
        "before.txt": b"one",
    }
    monkeypatch.setattr(workspace, "build_workspace_provider", lambda _settings: provider)

    async with _client() as client:
        response = await client.post(
            f"/internal/workspaces/{workspace_id}/agent/exec",
            headers={"X-Internal-Token": "test-internal-token-not-a-real-secret"},
            json={
                "generation_run_id": str(generation_run_id),
                "fencing_epoch": 4,
                "expected_revision": workspace._workspace_revision({"before.txt": "one"}),
                "cmd": "pnpm typecheck",
                "timeout_seconds": 321,
            },
        )

    assert response.status_code == 200
    assert response.json() == {
        "ok": True,
        "exit_code": 0,
        "detail": "DATABASE_URL=[REDACTED]\nbuild clean",
        "timed_out": False,
        "workspace_revision": workspace._workspace_revision(
            {"after.txt": "two", "before.txt": "one"}
        ),
    }
    assert len(docker.workspace_command_calls) == 1
    call = docker.workspace_command_calls[0]
    assert call["workspace_volume_name"] == names.workspace_volume
    assert call["agent_home_volume_name"] == names.agent_home_volume
    assert call["labels"] == {
        "omnia.managed": "true",
        "omnia.project_cell": "true",
        "omnia.workspace_id": str(workspace_id),
        "omnia.project_id": "00000000-0000-0000-0000-000000000092",
        "omnia.owner_id": "00000000-0000-0000-0000-000000000093",
        "omnia.provider": "docker_owner_canary",
        "omnia.profile_version": "docker-owner-cell-resources-v1",
        "omnia.resource_kind": "agent-exec",
    }
    assert call["image"] == "omnia-template-max-miniapp-nextjs:dev"
    assert call["command"] == "pnpm typecheck"
    assert call["internal_network_name"] == names.internal_network
    assert call["egress_network_name"] == names.egress_network
    assert call["environment"] == {
        "HOME": "/root",
        "CI": "1",
        "NODE_ENV": "development",
        "DATABASE_URL": (
            "postgresql://postgres:"
            f"{credentials.postgres_password}@{names.postgres_container}:5432/postgres"
        ),
        "PGHOST": names.postgres_container,
        "PGPORT": "5432",
        "PGUSER": "postgres",
        "PGPASSWORD": credentials.postgres_password,
        "PGDATABASE": "postgres",
        "REDIS_URL": f"redis://{names.redis_container}:6379/0",
    }
    assert call["timeout_seconds"] == 321


async def test_exec_workspace_agent_command_blocks_env_enumeration(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    workspace_id = uuid4()
    generation_run_id = uuid4()
    provider, _manager, docker, _ = await _ready_provider(
        tmp_path,
        workspace_id,
        generation_run_id=generation_run_id,
    )
    monkeypatch.setattr(workspace, "build_workspace_provider", lambda _settings: provider)

    async with _client() as client:
        response = await client.post(
            f"/internal/workspaces/{workspace_id}/agent/exec",
            headers={"X-Internal-Token": "test-internal-token-not-a-real-secret"},
            json={
                "generation_run_id": str(generation_run_id),
                "fencing_epoch": 4,
                "expected_revision": workspace._workspace_revision({}),
                "cmd": "printenv | sort",
                "timeout_seconds": 180,
            },
        )

    assert response.status_code == 200
    assert response.json() == {
        "ok": False,
        "exit_code": 126,
        "detail": "command blocked: environment and secret enumeration is not allowed",
        "timed_out": False,
        "workspace_revision": workspace._workspace_revision({}),
    }
    assert docker.workspace_command_calls == []


async def test_exec_workspace_agent_command_rejects_stale_revision_before_runner(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    workspace_id = UUID("00000000-0000-0000-0000-000000000095")
    generation_run_id = UUID("00000000-0000-0000-0000-000000000096")
    provider, manager, docker, _ = await _ready_provider(
        tmp_path,
        workspace_id,
        generation_run_id=generation_run_id,
    )
    state = manager.state_store.load(workspace_id)
    assert state is not None and state.resource_names is not None
    await docker.write_volume_files(
        state.resource_names.workspace_volume,
        {"page.tsx": b"current"},
    )
    monkeypatch.setattr(workspace, "build_workspace_provider", lambda _settings: provider)

    async with _client() as client:
        response = await client.post(
            f"/internal/workspaces/{workspace_id}/agent/exec",
            headers={"X-Internal-Token": "test-internal-token-not-a-real-secret"},
            json={
                "generation_run_id": str(generation_run_id),
                "fencing_epoch": 4,
                "expected_revision": workspace._workspace_revision({"page.tsx": "older"}),
                "cmd": "pnpm typecheck",
                "timeout_seconds": 60,
            },
        )

    assert response.status_code == 409
    assert response.json()["error"]["message"] == "workspace changed"
    assert docker.workspace_command_calls == []


async def test_exec_and_write_share_same_workspace_lock(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    workspace_id = UUID("00000000-0000-0000-0000-000000000097")
    generation_run_id = UUID("00000000-0000-0000-0000-000000000098")
    docker = _BlockingWorkspaceCommandDocker()
    provider, manager, _docker, _ = await _ready_provider(
        tmp_path,
        workspace_id,
        generation_run_id=generation_run_id,
        docker=docker,
    )
    state = manager.state_store.load(workspace_id)
    assert state is not None and state.resource_names is not None
    await docker.write_volume_files(
        state.resource_names.workspace_volume,
        {"page.tsx": b"before"},
    )
    docker.events.clear()
    docker.write_started.clear()
    monkeypatch.setattr(workspace, "build_workspace_provider", lambda _settings: provider)

    async with _client() as client:
        exec_task = asyncio.create_task(
            client.post(
                f"/internal/workspaces/{workspace_id}/agent/exec",
                headers={"X-Internal-Token": "test-internal-token-not-a-real-secret"},
                json={
                    "generation_run_id": str(generation_run_id),
                    "fencing_epoch": 4,
                    "expected_revision": workspace._workspace_revision({"page.tsx": "before"}),
                    "cmd": "pnpm typecheck",
                    "timeout_seconds": 60,
                },
            )
        )
        await asyncio.wait_for(docker.exec_started.wait(), timeout=1)
        write_task = asyncio.create_task(
            client.post(
                f"/internal/workspaces/{workspace_id}/agent/write-files",
                headers={"X-Internal-Token": "test-internal-token-not-a-real-secret"},
                json={
                    "generation_run_id": str(generation_run_id),
                    "fencing_epoch": 4,
                    "expected_revision": workspace._workspace_revision({"page.tsx": "before"}),
                    "files": {"page.tsx": "after"},
                    "deletes": [],
                },
            )
        )
        await asyncio.sleep(0.05)
        assert docker.events == ["exec-start"]
        assert write_task.done() is False
        assert docker.write_started.is_set() is False
        docker.release_exec.set()
        exec_response, write_response = await asyncio.gather(exec_task, write_task)

    assert exec_response.status_code == 200
    assert write_response.status_code == 200
    assert docker.events == ["exec-start", "exec-finish", "write"]


def test_workspace_router_registers_capability_and_resource_routes() -> None:
    matching = [
        route
        for route in workspace.router.routes
        if getattr(route, "path", None) is not None
    ]
    by_path = {route.path: route.methods for route in matching}
    assert by_path["/internal/projects/{project_id}/workspace/capabilities"] == {"GET"}
    assert by_path["/internal/workspaces/ensure"] == {"POST"}
    assert by_path["/internal/workspaces/{workspace_id}/control"] == {"POST"}
    assert by_path["/internal/workspaces/{workspace_id}/resources/observe"] == {"POST"}
    assert by_path["/internal/workspaces/{workspace_id}/resources"] == {"GET"}
    assert by_path["/internal/workspaces/{workspace_id}/agent/bootstrap"] == {"POST"}
    assert by_path["/internal/workspaces/{workspace_id}/agent/write-files"] == {"POST"}
    assert by_path["/internal/workspaces/{workspace_id}/agent/exec"] == {"POST"}
