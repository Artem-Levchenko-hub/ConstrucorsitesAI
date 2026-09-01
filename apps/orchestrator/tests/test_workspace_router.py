from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from types import SimpleNamespace
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import FastAPI

from omnia_orchestrator.core.config import get_settings
from omnia_orchestrator.core.errors import OrchestratorError, orchestrator_error_handler
from omnia_orchestrator.core.workspace_provider import WorkspaceStatus
from omnia_orchestrator.routers import workspace


class _RecordingProvider:
    def __init__(self) -> None:
        self.project_ids: list[UUID] = []

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


def test_main_application_registers_only_get_capability_route() -> None:
    from omnia_orchestrator.main import app

    matching = [
        route
        for route in app.routes
        if getattr(route, "path", None)
        == "/internal/projects/{project_id}/workspace/capabilities"
    ]

    assert len(matching) == 1
    assert matching[0].methods == {"GET"}
