from __future__ import annotations

from typing import Any
from uuid import UUID

import httpx
import pytest

from omnia_api.core.errors import ApiError
from omnia_api.routers import integration_runtime
from omnia_api.services import integration_providers


class _FakeClient:
    def __init__(self, response: httpx.Response, capture: dict[str, Any], **_kwargs: Any):
        self.response = response
        self.capture = capture

    async def __aenter__(self) -> _FakeClient:
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    async def get(self, url: str, **kwargs: Any) -> httpx.Response:
        self.capture.update({"method": "GET", "url": url, **kwargs})
        return self.response

    async def post(self, url: str, **kwargs: Any) -> httpx.Response:
        self.capture.update({"method": "POST", "url": url, **kwargs})
        return self.response


def test_aitunnel_provider_is_available_in_catalog() -> None:
    provider = integration_providers.get_provider("aitunnel")

    assert provider.name == "AITUNNEL"
    assert provider.category == "ai"
    assert provider.available is True
    assert provider.recommended is True
    assert provider.docs_url == "https://docs.aitunnel.ru/"
    assert [field.key for field in provider.fields] == ["api_key"]


@pytest.mark.asyncio
async def test_aitunnel_key_is_verified_by_read_only_profile_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = integration_providers.get_provider("aitunnel")
    public, secret = integration_providers.split_values(
        provider,
        {"api_key": "sk-aitunnel-test-value"},
    )
    capture: dict[str, Any] = {}
    response = httpx.Response(200, json={"id": 42, "email": "owner@example.test"})
    monkeypatch.setattr(
        integration_providers.httpx,
        "AsyncClient",
        lambda **kwargs: _FakeClient(response, capture, **kwargs),
    )

    label = await integration_providers.verify_provider("aitunnel", public, secret)

    assert label == "AITUNNEL · owner@example.test"
    assert capture["method"] == "GET"
    assert capture["url"] == "https://api.aitunnel.ru/v1/aitunnel/me"
    assert capture["headers"]["Authorization"] == "Bearer sk-aitunnel-test-value"


@pytest.mark.asyncio
async def test_runtime_ai_redacts_a_provider_echo(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_key = "sk-aitunnel-runtime-value"
    capture: dict[str, Any] = {}
    response = httpx.Response(
        200,
        json={
            "model": f"provider/{raw_key}",
            "choices": [{"message": {"content": f"Ответ {raw_key}"}}],
        },
    )
    monkeypatch.setattr(
        integration_runtime.httpx,
        "AsyncClient",
        lambda **kwargs: _FakeClient(response, capture, **kwargs),
    )

    async def fake_secrets(*_args: object) -> dict[str, str]:
        return {"api_key": raw_key}

    monkeypatch.setattr(integration_runtime, "_secrets", fake_secrets)

    result = await integration_runtime._request_aitunnel_ai(
        None,  # type: ignore[arg-type]
        object(),  # type: ignore[arg-type]
        system_prompt="Инструкция",
        user_message="Запрос",
    )

    assert result.answer == "Ответ [CREDENTIAL REDACTED]"
    assert raw_key not in result.model
    assert capture["json"]["model"] == "auto"


@pytest.mark.asyncio
async def test_owner_key_runtime_fails_closed_without_rate_limiter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unavailable_redis() -> None:
        raise RuntimeError("redis unavailable")

    monkeypatch.setattr(integration_runtime, "get_redis", unavailable_redis)

    with pytest.raises(ApiError) as raised:
        await integration_runtime._enforce_runtime_ai_limits(UUID(int=1), 42)

    assert raised.value.code == "integration_provider_unavailable"


@pytest.mark.asyncio
async def test_owner_key_runtime_updates_all_limit_buckets_atomically(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    capture: dict[str, Any] = {}

    class _FakeRedis:
        async def eval(self, script: str, numkeys: int, *values: object) -> int:
            capture.update({"script": script, "numkeys": numkeys, "values": values})
            return 0

    monkeypatch.setattr(integration_runtime, "get_redis", _FakeRedis)

    await integration_runtime._enforce_runtime_ai_limits(UUID(int=1), 42)

    assert capture["numkeys"] == 3
    assert 'redis.call("INCR", KEYS[i])' in capture["script"]
    assert 'redis.call("EXPIRE", KEYS[i]' in capture["script"]
    assert "if count > tonumber" in capture["script"]
    assert "counts[i]" not in capture["script"]
    assert capture["values"][-6:] == ("8", "60", "120", "86400", "2000", "86400")
