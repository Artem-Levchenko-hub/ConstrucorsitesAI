from __future__ import annotations

import importlib
import os
import threading
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr

from yleum_orchestrator.core import internal_auth
from yleum_orchestrator.core.errors import OrchestratorError, orchestrator_error_handler
from yleum_orchestrator.services import build_retention_inventory as inventory


@pytest.fixture
def routes():
    name = "yleum_orchestrator.routers.build_retention"
    if importlib.util.find_spec(name) is None:
        pytest.skip("protected roots endpoint is missing")
    return importlib.import_module(name)


def test_protected_roots_router_is_available():
    assert importlib.util.find_spec("yleum_orchestrator.routers.build_retention") is not None


@pytest.mark.skipif(os.name == "nt", reason="orchestrator app imports Linux fcntl authority")
async def test_actual_orchestrator_app_registers_authenticated_root_route(monkeypatch, routes):
    from yleum_orchestrator.core.config import get_settings

    monkeypatch.setenv("DATABASE_URL", "postgresql://test:test@127.0.0.1/test")
    monkeypatch.setenv("INTERNAL_TOKEN", "test-private-token")
    monkeypatch.setenv("SENTRY_DSN", "")
    get_settings.cache_clear()
    try:
        from yleum_orchestrator.main import create_app

        def forbidden(root):
            pytest.fail("unauthenticated live application route started inventory")

        monkeypatch.setattr(routes, "collect_roots", forbidden)
        async with AsyncClient(
            transport=ASGITransport(app=create_app()), base_url="http://test"
        ) as c:
            response = await c.get("/internal/build-retention/roots")
        assert response.status_code == 401
    finally:
        get_settings.cache_clear()


@pytest.fixture
async def client(routes, monkeypatch, tmp_path):
    settings = SimpleNamespace(
        internal_token=SecretStr("test-private-token"),
        cell_state_path=str(tmp_path / "project-cells.json"),
    )
    monkeypatch.setattr(routes, "get_settings", lambda: settings)
    monkeypatch.setattr(internal_auth, "get_settings", lambda: settings)
    app = FastAPI()
    app.include_router(routes.router)
    app.add_exception_handler(OrchestratorError, orchestrator_error_handler)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as value:
        yield value


def roots():
    assert hasattr(inventory, "ProtectedBuildRoots"), "protected root value object is missing"
    return inventory.ProtectedBuildRoots(
        archive_names=frozenset({"b" * 32 + ".tar", "a" * 32 + ".tar"}),
        image_ids=frozenset({"sha256:" + "f" * 64}),
        image_refs=frozenset({"registry.yleum.ru/max-app/" + str(UUID(int=3))}),
        busy_workspaces=frozenset({str(UUID(int=4))}),
        active_operations=1,
        observed_at="2026-11-03T12:00:00+00:00",
        generation="a" * 64,
    )


@pytest.mark.parametrize("token", [None, "wrong-private-token"])
async def test_endpoint_authenticates_before_inventory(client, routes, monkeypatch, token):
    def forbidden(root):
        pytest.fail("unauthenticated inventory was started")

    monkeypatch.setattr(routes, "collect_roots", forbidden)
    response = await client.get(
        "/internal/build-retention/roots",
        headers={"X-Internal-Token": token} if token else {},
    )
    assert response.status_code == 401
    assert "wrong-private-token" not in response.text
    assert "test-private-token" not in response.text


async def test_endpoint_exposes_versioned_sorted_roots_and_echoes_nonce(
    client,
    routes,
    monkeypatch,
    tmp_path,
):
    observed = []

    def collect(root):
        observed.append(root)
        return roots()

    monkeypatch.setattr(routes, "collect_roots", collect)
    response = await client.get(
        "/internal/build-retention/roots?nonce=bounded-test_123",
        headers={"X-Internal-Token": "test-private-token"},
    )
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    assert observed == [Path(tmp_path)]
    value = response.json()
    assert value == {
        "version": 1,
        "archive_names": sorted(roots().archive_names),
        "image_ids": sorted(roots().image_ids),
        "image_refs": sorted(roots().image_refs),
        "busy_workspaces": sorted(roots().busy_workspaces),
        "active_operations": 1,
        "observed_at": "2026-11-03T12:00:00+00:00",
        "generation": "a" * 64,
        "nonce": "bounded-test_123",
    }


@pytest.mark.parametrize("nonce", ["x" * 129, "contains%20spaces", "contains%2Fslash"])
async def test_endpoint_rejects_unbounded_or_unsafe_nonce_before_scan(
    client,
    routes,
    monkeypatch,
    nonce,
):
    def forbidden(root):
        pytest.fail("invalid nonce inventory was started")

    monkeypatch.setattr(routes, "collect_roots", forbidden)
    response = await client.get(
        "/internal/build-retention/roots?nonce=" + nonce,
        headers={"X-Internal-Token": "test-private-token"},
    )
    assert response.status_code == 422


@pytest.mark.parametrize(
    "failure",
    [
        inventory.UnsafeStateError("private-canary-secret"),
        PermissionError("private-canary-secret"),
        RuntimeError("private-canary-secret"),
    ],
)
async def test_endpoint_returns_non_200_sanitized_inventory_failure(
    client,
    routes,
    monkeypatch,
    failure,
):
    def fail(root):
        raise failure

    monkeypatch.setattr(routes, "collect_roots", fail)
    response = await client.get(
        "/internal/build-retention/roots",
        headers={"X-Internal-Token": "test-private-token"},
    )
    assert response.status_code == 503
    assert response.headers["Cache-Control"] == "no-store"
    assert "private-canary-secret" not in response.text
    assert "archive_names" not in response.json()


async def test_blocking_inventory_runs_outside_async_request_thread(client, routes, monkeypatch):
    request_threads = []
    collect_threads = []
    original = routes.verify_internal_token

    def verify(token):
        request_threads.append(threading.get_ident())
        original(token)

    def collect(root):
        collect_threads.append(threading.get_ident())
        return roots()

    monkeypatch.setattr(routes, "verify_internal_token", verify)
    monkeypatch.setattr(routes, "collect_roots", collect)
    response = await client.get(
        "/internal/build-retention/roots",
        headers={"X-Internal-Token": "test-private-token"},
    )
    assert response.status_code == 200
    assert request_threads and collect_threads and request_threads != collect_threads
