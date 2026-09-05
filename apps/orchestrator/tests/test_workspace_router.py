from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import replace
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import FastAPI

from omnia_orchestrator.core.cell_resources import (
    CellCapacityUnavailable,
    CellTerminalOperationFailed,
)
from omnia_orchestrator.core.config import get_settings
from omnia_orchestrator.core.errors import OrchestratorError, orchestrator_error_handler
from omnia_orchestrator.core.workspace_provider import (
    WorkspaceHandle,
    WorkspaceResourceStatus,
    WorkspaceSpec,
    WorkspaceStatus,
)
from omnia_orchestrator.routers import workspace
from omnia_orchestrator.services.docker_cell_resources import (
    CellResourceError,
    DockerCommandResult,
)
from omnia_orchestrator.services.docker_owner_canary_provider import DockerOwnerCanaryProvider
from tests._cell_fakes import FakeDockerBackend
from tests.test_cell_checkpoint import _make_fixture as _make_checkpoint_fixture


def test_generated_next_declarations_do_not_invalidate_source_proof() -> None:
    source = {"src/app/page.tsx": "export default function Page() {}"}
    revision = workspace._workspace_revision(source)
    generated = {
        **source, "next-env.d.ts": "generated declarations",
        "nested/next-env.d.ts": "generated nested declarations",
        "tsconfig.tsbuildinfo": "cache",
    }
    assert workspace._workspace_revision(generated) == revision
    assert workspace._workspace_revision({**generated, "src/app/page.tsx": "changed"}) != revision
    assert workspace._workspace_revision({**generated, "src/env.d.ts": "custom types"}) != revision

_DEFAULT_GENERATION_RUN_ID = UUID("00000000-0000-0000-0000-000000000094")


def _default_workspace_spec(
    workspace_id: UUID,
    *,
    generation_run_id: UUID | None = _DEFAULT_GENERATION_RUN_ID,
) -> WorkspaceSpec:
    return WorkspaceSpec(
        workspace_id=workspace_id,
        project_id=UUID("00000000-0000-0000-0000-000000000092"),
        owner_id=UUID("00000000-0000-0000-0000-000000000093"),
        profile_version="docker-owner-cell-resources-v1",
        generation_run_id=generation_run_id,
    )


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
    spec = _default_workspace_spec(
        workspace_id,
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
        (
            "post",
            f"/internal/workspaces/{UUID('00000000-0000-0000-0000-000000000042')}/draft/apply",
            {
                "generation_run_id": str(UUID("00000000-0000-0000-0000-000000000043")),
                "fencing_epoch": 6,
                "expected_revision": "a" * 64,
                "files": {},
                "deletes": [],
            },
        ),
        (
            "post",
            (
                f"/internal/workspaces/"
                f"{UUID('00000000-0000-0000-0000-000000000044')}/draft/preview-session"
            ),
            {
                "generation_run_id": str(UUID("00000000-0000-0000-0000-000000000045")),
                "fencing_epoch": 6,
            },
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
        "has_draft_runtime": False,
        "draft_state": None,
        "preview_url": None,
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


async def test_ensure_returns_exact_pre_effect_capacity_wait_envelope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class CapacityProvider(_RecordingProvider):
        async def ensure(self, spec, mutation):
            raise CellCapacityUnavailable("insufficient_memory")

    monkeypatch.setattr(workspace, "build_workspace_provider", lambda _settings: CapacityProvider())
    payload = {
        "workspace_id": "00000000-0000-0000-0000-000000000061",
        "project_id": "00000000-0000-0000-0000-000000000062",
        "owner_id": "00000000-0000-0000-0000-000000000063",
        "generation_run_id": "00000000-0000-0000-0000-000000000064",
        "profile_version": "docker-owner-cell-resources-v1",
        "operation_id": "00000000-0000-0000-0000-000000000065",
        "fencing_epoch": 7,
        "request_digest": "d" * 64,
    }

    async with _client() as client:
        response = await client.post(
            "/internal/workspaces/ensure",
            headers={"X-Internal-Token": "test-internal-token-not-a-real-secret"},
            json=payload,
        )

    assert response.status_code == 429
    assert response.json() == {
        "error": {
            "code": "capacity_wait",
            "message": "insufficient_memory",
            "details": {
                "operation_id": payload["operation_id"],
                "fencing_epoch": 7,
                "request_digest": "d" * 64,
                "effect_applied": False,
                "reason": "insufficient_memory",
                "retry_after_seconds": 2,
            },
        }
    }


async def test_control_terminal_replay_returns_exact_pre_effect_envelope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FailedReplayProvider(_RecordingProvider):
        async def execute_control(self, workspace_id, action, mutation):
            raise CellTerminalOperationFailed("checkpoint failed before control mutation")

    monkeypatch.setattr(
        workspace,
        "build_workspace_provider",
        lambda _settings: FailedReplayProvider(),
    )
    payload = {
        "workspace_id": "00000000-0000-0000-0000-000000000071",
        "kind": "pause",
        "checkpoint_ref": "capacity-test",
        "operation_id": "00000000-0000-0000-0000-000000000072",
        "fencing_epoch": 8,
        "request_digest": "e" * 64,
    }

    async with _client() as client:
        response = await client.post(
            f"/internal/workspaces/{payload['workspace_id']}/control",
            headers={"X-Internal-Token": "test-internal-token-not-a-real-secret"},
            json=payload,
        )

    assert response.status_code == 409
    assert response.json() == {
        "error": {
            "code": "conflict",
            "message": "checkpoint failed before control mutation",
            "details": {
                "operation_id": payload["operation_id"],
                "fencing_epoch": 8,
                "request_digest": "e" * 64,
                "effect_applied": False,
            },
        }
    }


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
async def test_agent_bootstrap_falls_back_to_template_when_project_workspace_missing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    workspace_id = UUID("00000000-0000-0000-0000-00000000007c")
    generation_run_id = UUID("00000000-0000-0000-0000-00000000007d")
    provider, manager, docker, _ = await _ready_provider(
        tmp_path,
        workspace_id,
        generation_run_id=generation_run_id,
    )
    state = manager.state_store.load(workspace_id)
    assert state is not None and state.resource_names is not None
    template_root = tmp_path / "template"
    missing_project_root = tmp_path / "missing"
    (template_root / "src" / "app").mkdir(parents=True, exist_ok=True)
    (template_root / "src" / "app" / "page.tsx").write_text(
        "export default function Page() { return 'template' }\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(workspace, "build_workspace_provider", lambda _settings: provider)
    monkeypatch.setattr(
        workspace,
        "_project_workspace_dir",
        lambda _project_id: missing_project_root,
    )
    monkeypatch.setattr(workspace, "trusted_template_source", lambda _template: template_root)

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
        "files": {"src/app/page.tsx": "export default function Page() { return 'template' }\n"},
        "seeded_from_project": False,
        "generation_run_id": str(generation_run_id),
        "fencing_epoch": 4,
        "workspace_revision": workspace._workspace_revision(
            {"src/app/page.tsx": "export default function Page() { return 'template' }\n"}
        ),
    }
    assert await docker.read_volume_files(state.resource_names.workspace_volume) == {
        "src/app/page.tsx": b"export default function Page() { return 'template' }\n"
    }


async def test_agent_bootstrap_never_reseeds_binary_only_workspace(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    workspace_id = uuid4()
    generation_run_id = uuid4()
    provider, manager, docker, _ = await _ready_provider(
        tmp_path,
        workspace_id,
        generation_run_id=generation_run_id,
    )
    state = manager.state_store.load(workspace_id)
    assert state is not None and state.resource_names is not None
    raw_files = {"assets/database.bin": b"\x00\xff\x01"}
    await docker.write_volume_files(state.resource_names.workspace_volume, raw_files)
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

    assert response.status_code == 409
    assert await docker.read_volume_files(state.resource_names.workspace_volume) == raw_files


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
    payload = response.json()
    assert {
        key: payload[key]
        for key in ("ok", "exit_code", "detail", "timed_out", "workspace_revision")
    } == {
        "ok": True,
        "exit_code": 0,
        "detail": "DATABASE_URL=[REDACTED]\nbuild clean",
        "timed_out": False,
        "workspace_revision": workspace._workspace_revision(
            {"after.txt": "two", "before.txt": "one"}
        ),
    }
    assert payload["operation_id"]
    assert payload["before_identity"]["workspace_revision"] == workspace._workspace_revision(
        {"before.txt": "one"}
    )
    assert payload["after_identity"]["workspace_revision"] == workspace._workspace_revision(
        {"after.txt": "two", "before.txt": "one"}
    )
    assert payload["environment_mutated"] is True
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
        "COREPACK_HOME": "/home/node/.cache/node/corepack",
        "COREPACK_ENABLE_NETWORK": "0",
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


async def test_exec_workspace_agent_command_restores_draft_runtime_after_serialized_run(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    workspace_id = UUID("00000000-0000-0000-0000-000000000099")
    generation_run_id = UUID("00000000-0000-0000-0000-00000000009a")
    provider, manager, docker, _ = await _ready_provider(
        tmp_path,
        workspace_id,
        generation_run_id=generation_run_id,
    )
    state = manager.state_store.load(workspace_id)
    assert state is not None and state.resource_names is not None
    names = state.resource_names
    await docker.write_volume_files(names.workspace_volume, {"before.txt": b"one"})
    await manager.ensure_draft_runtime(workspace_id)
    docker.workspace_command_volume_files = {"before.txt": b"one", "after.txt": b"two"}
    monkeypatch.setattr(workspace, "build_workspace_provider", lambda _settings: provider)
    republished: list[UUID] = []

    async def republish(_manager, current_workspace_id):
        republished.append(current_workspace_id)
        return "https://cell.preview.example"

    monkeypatch.setattr(workspace, "_publish_draft_preview", republish)

    async with _client() as client:
        response = await client.post(
            f"/internal/workspaces/{workspace_id}/agent/exec",
            headers={"X-Internal-Token": "test-internal-token-not-a-real-secret"},
            json={
                "generation_run_id": str(generation_run_id),
                "fencing_epoch": 4,
                "expected_revision": workspace._workspace_revision({"before.txt": "one"}),
                "cmd": "pnpm typecheck",
                "timeout_seconds": 60,
            },
        )

    assert response.status_code == 200
    assert docker.containers[names.draft_container_name()].state == "running"
    assert docker.workspace_command_calls[0]["command"] == "pnpm typecheck"
    assert republished == [workspace_id]


async def test_exec_reports_saved_effects_when_draft_restart_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    workspace_id = uuid4()
    generation_run_id = uuid4()
    provider, manager, docker, _ = await _ready_provider(
        tmp_path,
        workspace_id,
        generation_run_id=generation_run_id,
    )
    state = manager.state_store.load(workspace_id)
    assert state is not None and state.resource_names is not None
    names = state.resource_names
    await docker.write_volume_files(names.workspace_volume, {"before.txt": b"one"})
    await manager.ensure_draft_runtime(workspace_id)
    docker.workspace_command_volume_files = {"before.txt": b"one", "after.txt": b"two"}

    async def fail_restart(_manager, _workspace_id: UUID):
        raise CellResourceError("draft restart unavailable")

    monkeypatch.setattr(type(manager), "ensure_draft_runtime", fail_restart)
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
                "timeout_seconds": 60,
            },
        )

    assert response.status_code == 200
    assert response.json()["ok"] is False
    assert "Command effects were saved" in response.json()["detail"]
    assert response.json()["workspace_revision"] == workspace._workspace_revision(
        {"before.txt": "one", "after.txt": "two"}
    )


async def test_draft_apply_empty_patch_seeds_template_and_is_idempotent(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    workspace_id = UUID("00000000-0000-0000-0000-00000000009b")
    generation_run_id = UUID("00000000-0000-0000-0000-00000000009c")
    provider, manager, docker, _ = await _ready_provider(
        tmp_path,
        workspace_id,
        generation_run_id=generation_run_id,
    )
    state = manager.state_store.load(workspace_id)
    assert state is not None and state.resource_names is not None
    names = state.resource_names
    template_root = tmp_path / "draft-template"
    missing_project_root = tmp_path / "missing"
    (template_root / "src" / "app").mkdir(parents=True, exist_ok=True)
    (template_root / "src" / "app" / "page.tsx").write_text(
        "export default function Page() { return 'draft' }\n",
        encoding="utf-8",
    )
    (template_root / "package.json").write_text('{"name":"draft"}\n', encoding="utf-8")
    docker.workspace_command_result = DockerCommandResult(exit_code=0, output="migration ok")
    docker.workspace_command_volume_files = {
        "package.json": b'{"name":"draft"}\n',
        "pnpm-lock.yaml": b"lockfileVersion: '9.0'\n",
        "src/app/page.tsx": b"export default function Page() { return 'draft' }\n",
    }
    docker.container_logs[names.draft_container_name()] = (
        "DATABASE_URL=postgresql://postgres:secret@pg:5432/postgres\nready"
    )
    published: list[tuple[str, int, str]] = []

    async def _publish_http(
        host: str, port: int, *, upstream_host: str, private_cell: bool,
    ) -> bool:
        assert private_cell is True
        published.append((host, port, upstream_host))
        return True

    async def _ensure_tls(
        _host: str, _port: int, *, upstream_host: str, private_cell: bool,
    ) -> bool:
        assert private_cell is True
        assert upstream_host == "172.30.0.2"
        return True

    monkeypatch.setattr(workspace, "build_workspace_provider", lambda _settings: provider)
    monkeypatch.setattr(
        workspace,
        "_project_workspace_dir",
        lambda _project_id: missing_project_root,
    )
    monkeypatch.setattr(workspace, "trusted_template_source", lambda _template: template_root)
    monkeypatch.setattr(workspace.nginx_writer, "publish_http", _publish_http)
    monkeypatch.setattr(
        workspace.nginx_writer,
        "ensure_tls",
        _ensure_tls,
    )
    monkeypatch.setattr(workspace.nginx_writer, "dev_host", lambda slug: f"{slug}.preview.example")
    monkeypatch.setattr(workspace.nginx_writer, "dev_url", lambda slug: f"https://{slug}.preview.example")
    request = {
        "generation_run_id": str(generation_run_id),
        "fencing_epoch": 4,
        "expected_revision": workspace._workspace_revision({}),
        "files": {},
        "deletes": [],
    }

    async with _client() as client:
        first = await client.post(
            f"/internal/workspaces/{workspace_id}/draft/apply",
            headers={"X-Internal-Token": "test-internal-token-not-a-real-secret"},
            json=request,
        )
        second = await client.post(
            f"/internal/workspaces/{workspace_id}/draft/apply",
            headers={"X-Internal-Token": "test-internal-token-not-a-real-secret"},
            json=request,
        )

    assert first.status_code == 200
    assert second.status_code == 200
    expected_files = {
        "package.json": '{"name":"draft"}\n',
        "pnpm-lock.yaml": "lockfileVersion: '9.0'\n",
        "src/app/page.tsx": "export default function Page() { return 'draft' }\n",
    }
    assert first.json() == {
        "state": "draft_running",
        "workspace_revision": workspace._workspace_revision(expected_files),
        "preview_url": f"https://{names.draft_preview_slug()}.preview.example",
        "package_exit_code": None,
        "package_stderr_tail": "",
        "migration_exit_code": 0,
        "migration_stderr_tail": "migration ok",
        "runtime_log_tail": "DATABASE_URL=[REDACTED]\nready",
    }
    assert second.json()["workspace_revision"] == workspace._workspace_revision(expected_files)
    assert docker.containers[names.draft_container_name()].state == "running"
    assert published == [
        (f"{names.draft_preview_slug()}.preview.example", 3000, "172.30.0.2"),
        (f"{names.draft_preview_slug()}.preview.example", 3000, "172.30.0.2"),
    ]


async def test_draft_preview_publish_fails_closed_when_tls_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    workspace_id = UUID("00000000-0000-0000-0000-00000000009d")
    _provider, manager, _docker, _ = await _ready_provider(tmp_path, workspace_id)
    await manager.ensure_draft_runtime(workspace_id)

    async def _publish_http(
        _host: str, _port: int, *, upstream_host: str, private_cell: bool,
    ) -> None:
        assert private_cell is True
        assert upstream_host == "172.30.0.2"
        return None

    async def _ensure_tls(
        _host: str, _port: int, *, upstream_host: str, private_cell: bool,
    ) -> bool:
        assert private_cell is True
        assert upstream_host == "172.30.0.2"
        return False

    unpublished: list[str] = []

    async def _unpublish(host: str, *, http_only: bool = False) -> None:
        assert http_only is True
        unpublished.append(host)

    monkeypatch.setattr(workspace.nginx_writer, "publish_http", _publish_http)
    monkeypatch.setattr(workspace.nginx_writer, "ensure_tls", _ensure_tls)
    monkeypatch.setattr(workspace.nginx_writer, "unpublish", _unpublish)
    monkeypatch.setattr(workspace.nginx_writer, "dev_host", lambda slug: f"{slug}.preview.example")

    with pytest.raises(OrchestratorError, match="TLS provisioning failed"):
        await workspace._publish_draft_preview(manager, workspace_id)

    assert unpublished == [f"cell-{workspace_id.hex[:12]}.preview.example"]


async def test_draft_preview_requires_the_owned_internal_network_address(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    workspace_id = uuid4()
    _provider, manager, docker, _ = await _ready_provider(tmp_path, workspace_id)
    draft = await manager.ensure_draft_runtime(workspace_id)
    docker.containers[draft.name] = replace(
        draft, network_ipv4={"unrelated-network": "172.30.0.2"},
    )

    async def forbidden(*_args, **_kwargs):
        pytest.fail("missing owned network address must fail before nginx mutation")

    monkeypatch.setattr(workspace.nginx_writer, "publish_http", forbidden)
    with pytest.raises(OrchestratorError, match="no internal address"):
        await workspace._publish_draft_preview(manager, workspace_id)


async def test_wake_republishes_the_recreated_draft_upstream(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    workspace_id = uuid4()
    provider, manager, _docker, _ = await _ready_provider(tmp_path, workspace_id)
    draft = await manager.ensure_draft_runtime(workspace_id)
    republished: list[UUID] = []

    async def republish(_manager, current_workspace_id):
        republished.append(current_workspace_id)
        return "https://cell.preview.example"

    monkeypatch.setattr(workspace, "_publish_draft_preview", republish)
    monkeypatch.setattr(workspace, "build_workspace_provider", lambda _settings: provider)
    async with _client() as client:
        response = await client.post(
            f"/internal/workspaces/{workspace_id}/control",
            headers={"X-Internal-Token": "test-internal-token-not-a-real-secret"},
            json={
                "workspace_id": str(workspace_id), "kind": "wake",
                "operation_id": str(uuid4()), "fencing_epoch": 5,
                "request_digest": "d" * 64,
            },
        )
    assert response.status_code == 200
    current = await manager.inspect_draft_runtime(workspace_id)
    assert current is not None and current.resource_id != draft.resource_id
    assert republished == [workspace_id]


async def test_stale_ensure_sync_does_not_republish_after_newer_epoch(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    workspace_id = uuid4()
    provider, manager, _docker, generation_run_id = await _ready_provider(
        tmp_path,
        workspace_id,
    )
    newer_mutation = workspace.LifecycleMutation(uuid4(), 5, "b" * 64)
    await provider.ensure(
        _default_workspace_spec(
            workspace_id,
            generation_run_id=generation_run_id,
        ),
        newer_mutation,
    )
    await manager.ensure_draft_runtime(workspace_id)
    republished: list[UUID] = []

    async def republish(_manager, current_workspace_id: UUID) -> str:
        republished.append(current_workspace_id)
        return "https://cell.preview.example"

    monkeypatch.setattr(workspace, "_publish_draft_preview", republish)

    await workspace._sync_lifecycle_draft_preview(
        manager,
        workspace_id,
        workspace.LifecycleMutation(UUID(int=95), 4, "a" * 64),
    )

    state = manager.state_store.load(workspace_id)
    assert state is not None
    assert state.fencing_epoch == 5
    assert state.last_operation_id == newer_mutation.operation_id
    assert republished == []


async def test_stale_wake_sync_does_not_republish_after_newer_destroy(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    workspace_id = uuid4()
    provider, manager, _docker, _ = await _ready_provider(tmp_path, workspace_id)
    await manager.ensure_draft_runtime(workspace_id)
    stale_wake = workspace.LifecycleMutation(uuid4(), 5, "b" * 64)
    await provider.execute_control(
        workspace_id,
        workspace.ControlAction(kind="wake", checkpoint_ref=None),
        stale_wake,
    )

    async def destroy_without_removing_runtime(
        _self,
        _workspace_id: UUID,
        _mutation,
        *,
        checkpoint_ref: str | None,
        record_operation: bool,
    ) -> None:
        assert checkpoint_ref is not None
        assert record_operation is False
        return None

    monkeypatch.setattr(
        type(manager),
        "destroy_compute_without_lock",
        destroy_without_removing_runtime,
    )
    newer_destroy = workspace.LifecycleMutation(uuid4(), 6, "c" * 64)
    await provider.execute_control(
        workspace_id,
        workspace.ControlAction(kind="destroy", checkpoint_ref=None),
        newer_destroy,
    )
    republished: list[UUID] = []

    async def republish(_manager, current_workspace_id: UUID) -> str:
        republished.append(current_workspace_id)
        return "https://cell.preview.example"

    monkeypatch.setattr(workspace, "_publish_draft_preview", republish)

    await workspace._sync_lifecycle_draft_preview(manager, workspace_id, stale_wake)

    state = manager.state_store.load(workspace_id)
    assert state is not None
    assert state.fencing_epoch == 6
    assert state.last_operation_id == newer_destroy.operation_id
    assert await manager.inspect_draft_runtime(workspace_id) is not None
    assert republished == []


async def test_destroy_tombstone_rejects_newer_wake_and_still_unpublishes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    workspace_id = uuid4()
    provider, manager, _docker, _ = await _ready_provider(tmp_path, workspace_id)
    await manager.ensure_draft_runtime(workspace_id)
    stale_destroy = workspace.LifecycleMutation(uuid4(), 5, "b" * 64)
    await provider.execute_control(
        workspace_id,
        workspace.ControlAction(kind="destroy", checkpoint_ref=None),
        stale_destroy,
    )
    newer_wake = workspace.LifecycleMutation(uuid4(), 6, "c" * 64)
    with pytest.raises(workspace.CellFenceRejected, match="deletion"):
        await provider.execute_control(
            workspace_id,
            workspace.ControlAction(kind="wake", checkpoint_ref=None),
            newer_wake,
        )
    unpublished: list[str] = []

    async def unpublish(host: str) -> None:
        unpublished.append(host)

    monkeypatch.setattr(workspace.nginx_writer, "unpublish", unpublish)

    await workspace._sync_lifecycle_draft_preview(
        manager,
        workspace_id,
        stale_destroy,
        remove=True,
    )

    state = manager.state_store.load(workspace_id)
    assert state is not None
    assert state.fencing_epoch == 5
    assert state.last_operation_id == stale_destroy.operation_id
    assert unpublished == [workspace._draft_preview_host(workspace_id)]


async def test_wake_publish_holds_operation_lock_until_publication_finishes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    workspace_id = uuid4()
    provider, manager, _docker, _ = await _ready_provider(tmp_path, workspace_id)
    await manager.ensure_draft_runtime(workspace_id)
    publish_started = asyncio.Event()
    release_publish = asyncio.Event()
    publish_events: list[str] = []
    wake_operation_id = uuid4()
    destroy_operation_id = uuid4()

    async def republish(_manager, current_workspace_id: UUID) -> str:
        assert current_workspace_id == workspace_id
        publish_events.append("publish-start")
        publish_started.set()
        await release_publish.wait()
        publish_events.append("publish-end")
        return "https://cell.preview.example"

    monkeypatch.setattr(workspace, "_publish_draft_preview", republish)
    monkeypatch.setattr(workspace, "build_workspace_provider", lambda _settings: provider)

    async with _client() as client:
        wake_task = asyncio.create_task(
            client.post(
                f"/internal/workspaces/{workspace_id}/control",
                headers={"X-Internal-Token": "test-internal-token-not-a-real-secret"},
                json={
                    "workspace_id": str(workspace_id),
                    "kind": "wake",
                    "operation_id": str(wake_operation_id),
                    "fencing_epoch": 5,
                    "request_digest": "d" * 64,
                },
            )
        )
        await asyncio.wait_for(publish_started.wait(), timeout=1)
        destroy_task = asyncio.create_task(
            client.post(
                f"/internal/workspaces/{workspace_id}/control",
                headers={"X-Internal-Token": "test-internal-token-not-a-real-secret"},
                json={
                    "workspace_id": str(workspace_id),
                    "kind": "destroy",
                    "operation_id": str(destroy_operation_id),
                    "fencing_epoch": 6,
                    "request_digest": "e" * 64,
                },
            )
        )
        await asyncio.sleep(0.05)
        state = manager.state_store.load(workspace_id)
        assert state is not None
        assert state.fencing_epoch == 5
        assert state.last_operation_id == wake_operation_id
        assert destroy_task.done() is False
        release_publish.set()
        wake_response, destroy_response = await asyncio.gather(wake_task, destroy_task)

    state = manager.state_store.load(workspace_id)
    assert wake_response.status_code == 200
    assert destroy_response.status_code == 200
    assert state is not None
    assert state.fencing_epoch == 6
    assert state.last_operation_id == destroy_operation_id
    assert publish_events == ["publish-start", "publish-end"]


async def test_failed_migration_does_not_start_or_publish_draft(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    workspace_id = uuid4()
    generation_run_id = uuid4()
    provider, manager, docker, _ = await _ready_provider(
        tmp_path,
        workspace_id,
        generation_run_id=generation_run_id,
    )
    state = manager.state_store.load(workspace_id)
    assert state is not None and state.resource_names is not None
    names = state.resource_names
    source = {"package.json": b'{"name":"draft"}\n'}
    await docker.write_volume_files(names.workspace_volume, source)
    docker.workspace_command_result = DockerCommandResult(
        exit_code=1,
        output="migration failed",
    )

    async def forbidden_publish(*_args, **_kwargs):
        raise AssertionError("failed migration must not publish preview")

    monkeypatch.setattr(workspace, "build_workspace_provider", lambda _settings: provider)
    monkeypatch.setattr(workspace.nginx_writer, "publish_http", forbidden_publish)
    monkeypatch.setattr(
        workspace.nginx_writer,
        "dev_url",
        lambda slug: f"https://{slug}.preview.example",
    )
    request = {
        "generation_run_id": str(generation_run_id),
        "fencing_epoch": 4,
        "expected_revision": workspace._workspace_revision(
            {"package.json": '{"name":"draft"}\n'}
        ),
        "files": {},
        "deletes": [],
    }

    async with _client() as client:
        response = await client.post(
            f"/internal/workspaces/{workspace_id}/draft/apply",
            headers={"X-Internal-Token": "test-internal-token-not-a-real-secret"},
            json=request,
        )
        preview = await client.post(
            f"/internal/workspaces/{workspace_id}/draft/preview-session",
            headers={"X-Internal-Token": "test-internal-token-not-a-real-secret"},
            json={
                "generation_run_id": str(generation_run_id),
                "fencing_epoch": 4,
            },
        )

    assert response.status_code == 200
    assert response.json()["state"] == "draft_failed"
    assert response.json()["migration_exit_code"] == 1
    assert await manager.inspect_draft_runtime(workspace_id) is None
    assert preview.status_code == 409


async def test_draft_preview_session_returns_signed_https_bootstrap_url(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    workspace_id = UUID("00000000-0000-0000-0000-00000000009d")
    generation_run_id = UUID("00000000-0000-0000-0000-00000000009e")
    provider, manager, _docker, _ = await _ready_provider(
        tmp_path,
        workspace_id,
        generation_run_id=generation_run_id,
    )
    await manager.ensure_draft_runtime(workspace_id)
    monkeypatch.setattr(workspace, "build_workspace_provider", lambda _settings: provider)
    monkeypatch.setattr(workspace.nginx_writer, "dev_url", lambda slug: f"https://{slug}.preview.example")

    async with _client() as client:
        response = await client.post(
            f"/internal/workspaces/{workspace_id}/draft/preview-session",
            headers={"X-Internal-Token": "test-internal-token-not-a-real-secret"},
            json={
                "generation_run_id": str(generation_run_id),
                "fencing_epoch": 4,
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["workspace_id"] == str(workspace_id)
    assert payload["state"] == "draft_running"
    assert payload["preview_url"].startswith("https://cell-")
    parsed = urlparse(payload["bootstrap_url"])
    assert parsed.scheme == "https"
    assert parsed.path == "/api/omnia/preview-session"
    query = parse_qs(parsed.query)
    assert sorted(query) == ["expires", "signature"]
    assert query["signature"][0]


@pytest.mark.parametrize("publish_ok", [True, False])
async def test_portable_session_requires_published_ingress(monkeypatch, tmp_path, publish_ok):
    workspace_id = uuid4()
    provider, _manager, _docker, run_id = await _ready_provider(tmp_path, workspace_id)
    published = []

    async def publish(_manager, current_id):
        published.append(current_id)
        if not publish_ok:
            raise OrchestratorError(code="container_failure", message="TLS failed", status_code=503)
        return "https://cell.preview.example"

    monkeypatch.setattr(workspace, "build_workspace_provider", lambda _settings: provider)
    monkeypatch.setattr(workspace, "_portable_active", lambda *_: True)
    monkeypatch.setattr(workspace, "_require_portable_runtime", lambda _: SimpleNamespace(
        preview=lambda state: ("running", "172.30.0.2"), secret=lambda _: "test-secret",
    ))
    monkeypatch.setattr(workspace, "_publish_draft_preview", publish)
    monkeypatch.setattr(workspace.nginx_writer, "dev_url", lambda slug: f"https://{slug}.preview.example")
    async with _client() as client:
        response = await client.post(
            f"/internal/workspaces/{workspace_id}/draft/preview-session",
            headers={"X-Internal-Token": "test-internal-token-not-a-real-secret"},
            json={"generation_run_id": str(run_id), "fencing_epoch": 4},
        )
    assert published == [workspace_id]
    assert response.status_code == (200 if publish_ok else 503)
    assert ("bootstrap_url" in response.json()) is publish_ok


@pytest.mark.parametrize("released", [False, True])
async def test_owner_preview_survives_release_without_restoring_agent_write_access(
    monkeypatch, tmp_path, released,
) -> None:
    workspace_id = uuid4()
    provider, manager, docker, run_id = await _ready_provider(tmp_path, workspace_id)
    await manager.ensure_draft_runtime(workspace_id)
    if released:
        await manager.release_generation(
            workspace_id, workspace.LifecycleMutation(uuid4(), 5, "b" * 64),
            generation_run_id=run_id,
        )
    before = manager.state_store.load(workspace_id)
    containers_before = dict(docker.containers)
    spec = _default_workspace_spec(workspace_id)
    monkeypatch.setattr(workspace, "build_workspace_provider", lambda _settings: provider)
    monkeypatch.setattr(workspace.nginx_writer, "dev_url", lambda slug: f"https://{slug}.preview.example")
    headers = {"X-Internal-Token": "test-internal-token-not-a-real-secret"}
    async with _client() as client:
        for _ in range(2):
            response = await client.post(
                f"/internal/workspaces/{workspace_id}/draft/owner-preview-session",
                headers=headers,
                json={"project_id": str(spec.project_id), "owner_id": str(spec.owner_id)},
            )
            assert response.status_code == 200, response.text
            assert response.headers["cache-control"] == "no-store"
            assert response.json()["workspace_id"] == str(workspace_id)
            assert urlparse(response.json()["bootstrap_url"]).scheme == "https"
        if released:
            stale = await client.post(
                f"/internal/workspaces/{workspace_id}/draft/preview-session",
                headers=headers, json={"generation_run_id": str(run_id), "fencing_epoch": 4},
            )
            assert stale.status_code == 409
            write = await client.post(
                f"/internal/workspaces/{workspace_id}/agent/write-files",
                headers=headers, json={"generation_run_id": str(run_id), "fencing_epoch": 4,
                                       "expected_revision": "a" * 64, "files": {"x": "y"}},
            )
            assert write.status_code == 409
    assert manager.state_store.load(workspace_id) == before
    assert docker.containers == containers_before


async def test_owner_can_restart_retained_draft_after_release_without_recreating_it(
    monkeypatch, tmp_path,
) -> None:
    workspace_id = uuid4()
    provider, manager, docker, run_id = await _ready_provider(tmp_path, workspace_id)
    draft = await manager.ensure_draft_runtime(workspace_id)
    await manager.release_generation(
        workspace_id, workspace.LifecycleMutation(uuid4(), 5, "b" * 64),
        generation_run_id=run_id,
    )
    await docker.stop_container(draft.name)
    state_before = manager.state_store.load(workspace_id)
    spec = _default_workspace_spec(workspace_id)
    monkeypatch.setattr(workspace, "build_workspace_provider", lambda _settings: provider)
    monkeypatch.setattr(workspace.nginx_writer, "dev_url", lambda slug: f"https://{slug}.preview.example")

    async def publish(*args):
        return "https://cell.preview.example"

    monkeypatch.setattr(workspace, "_publish_draft_preview", publish)
    async with _client() as client:
        for _ in range(2):
            response = await client.post(
                f"/internal/workspaces/{workspace_id}/draft/owner-start",
                headers={"X-Internal-Token": "test-internal-token-not-a-real-secret"},
                json={"project_id": str(spec.project_id), "owner_id": str(spec.owner_id)},
            )
            assert response.status_code == 200, response.text
    assert manager.state_store.load(workspace_id) == state_before
    assert docker.containers[draft.name].resource_id == draft.resource_id
    assert docker.containers[draft.name].state == "running"


@pytest.mark.parametrize("endpoint", ["owner-preview-session", "owner-start"])
@pytest.mark.parametrize("failure", ["unauthenticated", "owner", "project", "stopped"])
async def test_owner_preview_rejects_invalid_identity_or_runtime(
    monkeypatch, tmp_path, failure, endpoint,
) -> None:
    workspace_id = uuid4()
    provider, manager, _docker, _ = await _ready_provider(tmp_path, workspace_id)
    if failure != "stopped":
        await manager.ensure_draft_runtime(workspace_id)
    spec = _default_workspace_spec(workspace_id)
    monkeypatch.setattr(workspace, "build_workspace_provider", lambda _settings: provider)
    monkeypatch.setattr(workspace.nginx_writer, "dev_url", lambda slug: f"https://{slug}.preview.example")
    async with _client() as client:
        response = await client.post(
            f"/internal/workspaces/{workspace_id}/draft/{endpoint}",
            headers={} if failure == "unauthenticated" else {
                "X-Internal-Token": "test-internal-token-not-a-real-secret",
            },
            json={"project_id": str(uuid4() if failure == "project" else spec.project_id),
                  "owner_id": str(uuid4() if failure == "owner" else spec.owner_id)},
        )
    assert response.status_code == (401 if failure == "unauthenticated" else 409)
    assert "bootstrap_url" not in response.text


@pytest.mark.parametrize("initially_running", [False, True])
async def test_portable_owner_start_retries_do_not_restart_healthy_services(
    monkeypatch, tmp_path, initially_running,
):
    workspace_id = uuid4()
    provider, manager, _, run_id = await _ready_provider(tmp_path, workspace_id)
    await manager.release_generation(
        workspace_id, workspace.LifecycleMutation(uuid4(), 5, "b" * 64),
        generation_run_id=run_id,
    )
    before = manager.state_store.load(workspace_id)
    spec = _default_workspace_spec(workspace_id)
    running = initially_running
    resumes = []
    publishes = []

    async def resume(state):
        nonlocal running
        resumes.append(state.workspace_id)
        running = True

    async def publish(_manager, current_id):
        publishes.append(current_id)

    monkeypatch.setattr(workspace, "build_workspace_provider", lambda _: provider)
    monkeypatch.setattr(workspace, "_portable_active", lambda *_: True)
    monkeypatch.setattr(workspace, "_require_portable_runtime", lambda _: SimpleNamespace(
        preview=lambda _: ("running" if running else "stopped", "172.30.0.2"),
        secret=lambda _: "test-secret", resume_preview=resume,
    ))
    monkeypatch.setattr(workspace, "_publish_draft_preview", publish)
    monkeypatch.setattr(workspace.nginx_writer, "dev_url", lambda slug: f"https://{slug}.preview.example")
    async with _client() as client:
        for _ in range(2):
            response = await client.post(
                f"/internal/workspaces/{workspace_id}/draft/owner-start",
                headers={"X-Internal-Token": "test-internal-token-not-a-real-secret"},
                json={"project_id": str(spec.project_id), "owner_id": str(spec.owner_id)},
            )
            assert response.status_code == 200, response.text
    assert resumes == ([] if initially_running else [workspace_id])
    assert publishes == [workspace_id, workspace_id]
    assert manager.state_store.load(workspace_id) == before


@pytest.mark.parametrize("failure", [None, "owner", "project", "active", "token"])
async def test_owner_business_config_has_no_generation_write_authority(
    monkeypatch, tmp_path, failure,
):
    from unittest.mock import AsyncMock

    workspace_id = uuid4()
    provider, manager, _, run_id = await _ready_provider(tmp_path, workspace_id)
    if failure != "active":
        await manager.release_generation(
            workspace_id, workspace.LifecycleMutation(uuid4(), 5, "b" * 64),
            generation_run_id=run_id,
        )
    before = manager.state_store.load(workspace_id)
    spec = _default_workspace_spec(workspace_id)
    apply = AsyncMock()
    monkeypatch.setattr(workspace, "build_workspace_provider", lambda _: provider)
    monkeypatch.setattr(workspace, "_portable_active", lambda *_: True)
    monkeypatch.setattr(workspace, "_require_portable_runtime", lambda _: SimpleNamespace(
        apply_owner_business_config=apply,
    ))
    monkeypatch.setattr(workspace, "_publish_draft_preview", AsyncMock())
    async with _client() as client:
        response = await client.put(
            f"/internal/workspaces/{workspace_id}/owner-business-config",
            headers={} if failure == "token" else {
                "X-Internal-Token": "test-internal-token-not-a-real-secret",
            },
            json={"project_id": str(uuid4() if failure == "project" else spec.project_id),
                  "owner_id": str(uuid4() if failure == "owner" else spec.owner_id),
                  "version": 1, "config": {"app_name": "QA"}},
        )
    assert response.status_code == (200 if failure is None else 401 if failure == "token" else 409)
    assert apply.await_count == (1 if failure is None else 0)
    assert manager.state_store.load(workspace_id) == before


def test_bounded_redacted_text_redacts_signature_and_token_query_values() -> None:
    text = (
        "bootstrap=https://cell.preview.example/api/omnia/preview-session"
        "?signature=sig-123&expires=1700000000\n"
        "iframe=https://cell.preview.example/app?token=tok-456&mode=live"
    )

    redacted = workspace._bounded_redacted_text(text)

    assert "sig-123" not in redacted
    assert "tok-456" not in redacted
    assert "signature=[REDACTED]" in redacted
    assert "token=[REDACTED]" in redacted
    assert "expires=1700000000" in redacted
    assert "mode=live" in redacted


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
    payload = response.json()
    assert {
        key: payload[key]
        for key in ("ok", "exit_code", "detail", "timed_out", "workspace_revision")
    } == {
        "ok": False,
        "exit_code": 126,
        "detail": "command blocked: environment and secret enumeration is not allowed",
        "timed_out": False,
        "workspace_revision": workspace._workspace_revision({}),
    }
    assert payload["before_identity"] == payload["after_identity"]
    assert payload["environment_mutated"] is False
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
    assert by_path["/internal/workspaces/{workspace_id}/draft/apply"] == {"POST"}
    assert by_path["/internal/workspaces/{workspace_id}/draft/preview-session"] == {"POST"}
