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


def test_role_map_deepseek_generation_with_opus_vision() -> None:
    assert model_for_role("director") == "deepseek-v4-pro"
    assert model_for_role("polish") == "deepseek-v4-pro"
    assert model_for_role("classify") == "deepseek-v4-pro"
    assert model_for_role("edit") == "deepseek-v4-pro"
    assert model_for_role("single_shot") == "deepseek-v4-pro"
    assert model_for_role("agent") == "deepseek-v4-pro"
    assert model_for_role("agent_escalation") == "deepseek-v4-pro"
    assert model_for_role("audit") == "claude-opus-4-8"
    assert model_for_role("audit_retry") == "claude-opus-4-8"
    for role, model in ROLE_MODEL_MAP.items():
        expected = "claude-opus-4-8" if role in {"audit", "audit_retry"} else "deepseek-v4-pro"
        assert model == expected, f"{role} -> {model}"
    assert DEFAULT_ROLE_MODEL == "deepseek-v4-pro"
    banned = {
        "deepseek-chat", "deepseek-v4-pro-thinking",
        "kimi-k2.6", "kimi-k2.6-thinking", "gemini-3-flash-vision",
        "gemini-3.5-flash-high", "claude-sonnet-4-6", "claude-haiku-4-5",
        "claude-opus-4-7",
    }
    for m in ROLE_MODEL_MAP.values():
        assert m not in banned


def test_art_director_writer_both_deepseek() -> None:
    assert model_for_role("art_director") == "deepseek-v4-pro"
    assert model_for_role("freeform_writer") == "deepseek-v4-pro"


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
