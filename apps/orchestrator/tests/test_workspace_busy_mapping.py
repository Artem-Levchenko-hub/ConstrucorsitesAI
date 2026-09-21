"""Waiting for a busy workspace is normal operation, not an internal error.

During a live release the owner's MAX configuration request collided with a wake and
the orchestrator answered 500 with an ASGI traceback, which the editor showed as
«Failed to fetch». Lock contention now answers a retryable 503 through a handler
registered for the exception class, so a route that never thought to catch it is
covered too.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
import pytest
from fastapi import APIRouter, FastAPI

from omnia_orchestrator.core.cell_resources import (
    WorkspaceLockTimeout,
    WorkspaceLockUnavailable,
)
from omnia_orchestrator.core.errors import (
    OrchestratorError,
    orchestrator_error_handler,
    unhandled_error_handler,
    workspace_busy_handler,
)

router = APIRouter()


@router.get("/busy")
async def _busy() -> dict[str, str]:
    raise WorkspaceLockTimeout("workspace_lock_timeout: workspace-0000")


@router.get("/unavailable")
async def _unavailable() -> dict[str, str]:
    raise WorkspaceLockUnavailable("workspace_lock_unavailable")


@asynccontextmanager
async def _client() -> AsyncIterator[httpx.AsyncClient]:
    # Exactly the wiring of omnia_orchestrator.main.create_app, in registration order.
    app = FastAPI()
    app.add_exception_handler(OrchestratorError, orchestrator_error_handler)
    app.add_exception_handler(WorkspaceLockTimeout, workspace_busy_handler)
    app.add_exception_handler(Exception, unhandled_error_handler)
    app.include_router(router)
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


async def test_a_busy_workspace_is_a_retryable_503_not_an_internal_error() -> None:
    async with _client() as client:
        response = await client.get("/busy")

    assert response.status_code == 503
    error = response.json()["error"]
    assert error["code"] == "workspace_busy"
    assert error["details"] == {"retryable": True, "retry_after_seconds": 2}
    assert response.headers["Retry-After"] == "2"
    # The internal lock name is diagnostics, not something an owner should read.
    assert "workspace-0000" not in response.text and "lock" not in error["message"].lower()
    assert "Проект сейчас занят" in error["message"]


async def test_the_class_handler_wins_over_the_catch_all(caplog) -> None:
    with caplog.at_level("ERROR"):
        async with _client() as client:
            response = await client.get("/busy")

    assert response.status_code == 503
    # A 500 here used to log a traceback for contention that is expected.
    assert not [record for record in caplog.records if record.levelname == "ERROR"]


async def test_a_lock_that_could_not_be_established_is_still_an_internal_error() -> None:
    # Only the timeout is ordinary contention: an unusable lock root is a real fault
    # and must not be dressed up as "try again in a moment".
    async with _client() as client:
        response = await client.get("/unavailable")

    assert response.status_code == 500
    assert response.json()["error"]["code"] == "internal_error"


def test_the_application_registers_the_handler(monkeypatch: pytest.MonkeyPatch) -> None:
    from omnia_orchestrator.core.config import get_settings

    # `omnia_orchestrator.main` builds its app at import time, so the settings this
    # process needs must exist before the module is imported.
    monkeypatch.setenv("DATABASE_URL", "postgresql://test:test@127.0.0.1:5432/test")
    monkeypatch.setenv("INTERNAL_TOKEN", "test-internal-token-not-a-real-secret")
    get_settings.cache_clear()  # type: ignore[attr-defined]
    try:
        from omnia_orchestrator.main import create_app

        app = create_app()
    finally:
        get_settings.cache_clear()  # type: ignore[attr-defined]

    assert app.exception_handlers.get(WorkspaceLockTimeout) is workspace_busy_handler


@pytest.mark.parametrize(
    "route",
    [
        "/internal/workspaces/{workspace_id}/owner-business-config",
        "/internal/workspaces/{workspace_id}/control",
    ],
)
def test_the_routes_that_take_the_lock_are_still_registered(route: str) -> None:
    # These are the unguarded lock holders the class handler now covers; if one is
    # renamed, this test says so instead of silently protecting nothing.
    from omnia_orchestrator.routers import workspace

    paths = {getattr(item, "path", None) for item in workspace.router.routes}

    assert route in paths
