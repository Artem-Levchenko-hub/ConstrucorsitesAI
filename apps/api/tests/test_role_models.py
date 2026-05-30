"""Tests for the role→model orchestration registry (Phase M)."""

from __future__ import annotations

import os

# Settings() requires these env vars; set before importing config.
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://x:x@localhost/x")
os.environ.setdefault("JWT_SECRET", "test-secret")

from omnia_api.core.config import (  # noqa: E402
    DEFAULT_ROLE_MODEL,
    FREE_GENERATION_LIMIT,
    ROLE_MODEL_MAP,
    model_for_role,
)


def test_role_map_orchestrator_opus_workers_deepseek() -> None:
    # Owner directive (2026-05-30): orchestrator = Opus, every worker/developer
    # role = DeepSeek (vsegpt). audit (vision judge) + audit_retry (escalation)
    # stay premium on purpose — see ROLE_MODEL_MAP comment.
    assert model_for_role("director") == "claude-opus-4-7"
    assert model_for_role("polish") == "deepseek-v4-flash-thinking"
    assert model_for_role("classify") == "deepseek-v4-flash-thinking"
    assert model_for_role("edit") == "deepseek-v4-flash-thinking"
    assert model_for_role("single_shot") == "deepseek-v4-flash-thinking"
    assert model_for_role("audit") == "claude-sonnet-4-6"
    assert model_for_role("audit_retry") == "claude-opus-4-7"


def test_no_role_uses_flaky_gemini() -> None:
    # gemini-2.5-flash streaming is unreliable behind the RU egress proxy
    # (incomplete chunked read ~50%). It must not back any pipeline role.
    assert "gemini-2.5-flash" not in ROLE_MODEL_MAP.values()


def test_override_takes_precedence() -> None:
    assert model_for_role("director", override="gpt-5") == "gpt-5"
    assert model_for_role("polish", override="claude-haiku-4-5") == "claude-haiku-4-5"


def test_unknown_role_falls_back_to_default() -> None:
    assert model_for_role("does-not-exist") == DEFAULT_ROLE_MODEL


def test_free_generation_limit_is_three() -> None:
    assert FREE_GENERATION_LIMIT == 3


def test_every_role_resolves_to_a_nonempty_model() -> None:
    for role in ROLE_MODEL_MAP:
        resolved = model_for_role(role)
        assert isinstance(resolved, str) and resolved
