"""Pytest config — disable .env, neutralize external dependencies, reset singletons.

Tests in this suite never touch a real Postgres / Redis / upstream LLM.
External services are mocked at the import boundary so test code can focus on
what changed since the last run, not on infrastructure setup.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from omnia_gateway.core.config import reset_settings_cache


@pytest.fixture(autouse=True)
def _isolate_settings(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Force config defaults — no .env, no provider keys leaking from the host."""
    # setenv("") rather than delenv() — pydantic-settings still loads the local
    # .env file from cwd, and delenv only clears os.environ, leaving the file
    # value in effect. An empty env var beats the file.
    for var in (
        "AITUNNEL_API_KEY",
        "PROXYAPI_API_KEY",
    ):
        monkeypatch.setenv(var, "")
    monkeypatch.setenv("DATABASE_URL", "postgresql://test:test@localhost:5432/test_omnia")
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/15")
    monkeypatch.chdir(os.path.dirname(os.path.dirname(__file__)))
    reset_settings_cache()
    yield
    reset_settings_cache()


@pytest.fixture
def neutralize_lifespan(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub everything the FastAPI lifespan would set up."""
    monkeypatch.setattr("omnia_gateway.main.init_pool", AsyncMock(return_value=None))
    monkeypatch.setattr("omnia_gateway.main.close_pool", AsyncMock(return_value=None))
    monkeypatch.setattr("omnia_gateway.main.init_redis", AsyncMock(return_value=None))
    monkeypatch.setattr("omnia_gateway.main.close_redis", AsyncMock(return_value=None))
    monkeypatch.setattr("omnia_gateway.main.init_http", AsyncMock(return_value=None))
    monkeypatch.setattr("omnia_gateway.main.close_http", AsyncMock(return_value=None))
    monkeypatch.setattr("omnia_gateway.main.configure_logging", lambda: None)


@pytest.fixture
def neutralize_side_effects(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub cache / billing / file logging so chat router doesn't hit real I/O."""
    monkeypatch.setattr("omnia_gateway.routers.chat.cache.get", AsyncMock(return_value=None))
    monkeypatch.setattr("omnia_gateway.routers.chat.cache.set", AsyncMock(return_value=None))
    monkeypatch.setattr(
        "omnia_gateway.routers.chat.billing.precheck_balance",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        "omnia_gateway.routers.chat.billing.charge",
        AsyncMock(return_value=uuid4()),
    )
    monkeypatch.setattr(
        "omnia_gateway.routers.chat.file_logger.log_request",
        lambda payload: None,
    )
