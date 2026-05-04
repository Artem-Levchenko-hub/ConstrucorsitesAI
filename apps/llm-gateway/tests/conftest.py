"""Pytest config — disable .env loading and reset cached singletons per session."""
from __future__ import annotations

import os
from collections.abc import Iterator

import pytest

from omnia_gateway.core.config import reset_settings_cache
from omnia_gateway.services.litellm_router import reset_router


@pytest.fixture(autouse=True)
def _isolate_settings(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Force config defaults — no .env, no provider keys leaking from the host."""
    for var in (
        "ANTHROPIC_API_KEY",
        "OPENAI_API_KEY",
        "YANDEX_API_KEY",
        "YANDEX_FOLDER_ID",
        "OPENROUTER_API_KEY",
    ):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("DATABASE_URL", "postgresql://test:test@localhost:5432/test_omnia")
    # Point pydantic-settings at a non-existent file so it falls back to env / defaults.
    monkeypatch.chdir(os.path.dirname(os.path.dirname(__file__)))
    reset_settings_cache()
    reset_router()
    yield
    reset_settings_cache()
    reset_router()
