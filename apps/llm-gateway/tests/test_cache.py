"""Cache key + get/set round-trip — get_redis() is faked at the module level."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from yleum_gateway.services import cache


def test_make_cache_key_includes_full_conversation() -> None:
    msgs_a = [
        {"role": "system", "content": "you are an agent"},
        {"role": "user", "content": "build a landing page"},
    ]
    msgs_b = [
        {"role": "system", "content": "you are an agent"},
        {"role": "assistant", "content": "previous reply was different"},
        {"role": "user", "content": "build a landing page"},
    ]
    assert cache.make_cache_key("claude-sonnet-4-6", msgs_a) != cache.make_cache_key(
        "claude-sonnet-4-6", msgs_b
    )


@pytest.mark.parametrize("changed", ["user_id", "project_id", "temperature", "max_tokens"])
def test_make_cache_key_scopes_owner_project_and_generation_parameters(changed: str) -> None:
    messages = [{"role": "user", "content": "same request"}]
    base = {
        "user_id": "owner-a",
        "project_id": "project-a",
        "temperature": 0.2,
        "max_tokens": 128,
    }
    other = {**base, changed: {
        "user_id": "owner-b",
        "project_id": "project-b",
        "temperature": 0.8,
        "max_tokens": 256,
    }[changed]}
    assert cache.make_cache_key("claude-sonnet-4-6", messages, **base) != cache.make_cache_key(
        "claude-sonnet-4-6", messages, **other
    )


def test_make_cache_key_uses_new_namespace() -> None:
    key = cache.make_cache_key("claude-sonnet-4-6", [{"role": "user", "content": "hello"}])
    assert key.startswith("llm:cache:v2:")


def test_make_cache_key_changes_with_model() -> None:
    msgs = [{"role": "user", "content": "hi"}]
    assert cache.make_cache_key("claude-sonnet-4-6", msgs) != cache.make_cache_key(
        "gpt-5-mini", msgs
    )


def test_make_cache_key_changes_with_user_content() -> None:
    a = [{"role": "user", "content": "build A"}]
    b = [{"role": "user", "content": "build B"}]
    assert cache.make_cache_key("claude-sonnet-4-6", a) != cache.make_cache_key(
        "claude-sonnet-4-6", b
    )


@pytest.mark.asyncio
async def test_get_returns_none_on_miss(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_redis = AsyncMock()
    fake_redis.get = AsyncMock(return_value=None)
    monkeypatch.setattr(cache, "get_redis", lambda: fake_redis)
    assert await cache.get("any") is None


@pytest.mark.asyncio
async def test_get_returns_parsed_payload_on_hit(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {"id": "x", "choices": []}
    fake_redis = AsyncMock()
    fake_redis.get = AsyncMock(return_value=json.dumps(payload).encode("utf-8"))
    monkeypatch.setattr(cache, "get_redis", lambda: fake_redis)
    assert await cache.get("any") == payload


@pytest.mark.asyncio
async def test_set_writes_with_ttl(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_redis = AsyncMock()
    monkeypatch.setattr(cache, "get_redis", lambda: fake_redis)
    monkeypatch.setenv("CACHE_TTL_SECONDS", "120")
    from yleum_gateway.core.config import reset_settings_cache

    reset_settings_cache()

    await cache.set("k", {"v": 1})
    fake_redis.set.assert_awaited_once()
    args, kwargs = fake_redis.set.call_args
    assert args[0] == "k"
    assert kwargs.get("ex") == 120
    assert json.loads(args[1]) == {"v": 1}
