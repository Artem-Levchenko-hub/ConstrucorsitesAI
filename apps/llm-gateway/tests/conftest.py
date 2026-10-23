"""Pytest config — disable .env, neutralize external dependencies, reset singletons.

Tests in this suite never touch a real Postgres / Redis / upstream LLM.
External services are mocked at the import boundary so test code can focus on
what changed since the last run, not on infrastructure setup.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest

from yleum_gateway.core.config import reset_settings_cache
from yleum_gateway.services import pricing, provider_calls


@pytest.fixture(autouse=True)
def _forbid_unmocked_http(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """A missed sync/async transport fixture must fail locally, even if caught."""
    attempted: list[bool] = []

    def denied(*args, **kwargs):
        attempted.append(True)
        raise AssertionError("Unmocked HTTP transport is forbidden in gateway tests")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", denied)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", denied)
    yield
    assert not attempted, "Test attempted unmocked HTTP; a caught transport failure is not a pass"


@pytest.fixture(autouse=True)
def _isolate_provider_journal(monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest) -> None:
    """Unit routes use no DB; writer tests explicitly exercise their own fake pool."""
    if request.module.__name__.endswith("test_provider_calls"):
        return
    monkeypatch.setattr(provider_calls, "start_call", AsyncMock(side_effect=lambda **kw: uuid4()))
    monkeypatch.setattr(provider_calls, "finish_call", AsyncMock(return_value=None))


@pytest.fixture(autouse=True)
def _isolate_catalog(monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest) -> None:
    """No pricing network in tests; catalog tests use their own MockTransport."""
    monkeypatch.setattr(pricing, "_catalog_cache", None)
    monkeypatch.setattr(pricing, "_catalog_lock", None)
    if not request.module.__name__.endswith("test_provider_catalog_pricing"):
        monkeypatch.setattr(pricing, "_fetch_catalog_prices", AsyncMock(return_value={}))


@pytest.fixture(autouse=True)
def _isolate_settings(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Force config defaults — no .env, no provider keys leaking from the host."""
    # setenv("") rather than delenv() — pydantic-settings still loads the local
    # .env file from cwd, and delenv only clears os.environ, leaving the file
    # value in effect. An empty env var beats the file.
    for var in (
        "LLMGW_API_KEY",
        "AITUNNEL_API_KEY",
        "AITUNNEL_TEXT_API_KEY",
        "PROXYAPI_API_KEY",
    ):
        monkeypatch.setenv(var, "")
    monkeypatch.setenv("AITUNNEL_TEXT_FALLBACK_ENABLED", "false")
    monkeypatch.setenv("AITUNNEL_TEXT_ORGANIZATION_ID", "")
    monkeypatch.setenv("DATABASE_URL", "postgresql://test:test@localhost:5432/test_omnia")
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/15")
    monkeypatch.chdir(os.path.dirname(os.path.dirname(__file__)))
    reset_settings_cache()
    yield
    reset_settings_cache()


@pytest.fixture
def neutralize_lifespan(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub everything the FastAPI lifespan would set up."""
    monkeypatch.setattr("yleum_gateway.main.init_pool", AsyncMock(return_value=None))
    monkeypatch.setattr("yleum_gateway.main.close_pool", AsyncMock(return_value=None))
    monkeypatch.setattr("yleum_gateway.main.init_redis", AsyncMock(return_value=None))
    monkeypatch.setattr("yleum_gateway.main.close_redis", AsyncMock(return_value=None))
    monkeypatch.setattr("yleum_gateway.main.init_http", AsyncMock(return_value=None))
    monkeypatch.setattr("yleum_gateway.main.close_http", AsyncMock(return_value=None))
    monkeypatch.setattr("yleum_gateway.main.configure_logging", lambda: None)
    # Route fixtures configure auth after TestClient starts. Like the other
    # neutralized startup resources, the background cleanup switch must not
    # populate the real settings cache before those per-test credentials exist.
    monkeypatch.setattr("yleum_gateway.main.get_settings", lambda: SimpleNamespace(
        generation_response_cleanup_enabled=False, generation_response_retention_enabled=False,
    ))


@pytest.fixture
def neutralize_side_effects(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub cache / billing / file logging so chat router doesn't hit real I/O."""
    monkeypatch.setattr("yleum_gateway.routers.chat.cache.get", AsyncMock(return_value=None))
    monkeypatch.setattr("yleum_gateway.routers.chat.cache.set", AsyncMock(return_value=None))
    monkeypatch.setattr(
        "yleum_gateway.routers.chat.billing.precheck_balance",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        "yleum_gateway.routers.chat.billing.charge",
        AsyncMock(return_value=uuid4()),
    )
    monkeypatch.setattr(
        "yleum_gateway.routers.chat.file_logger.log_request",
        lambda payload: None,
    )
