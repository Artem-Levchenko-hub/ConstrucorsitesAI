from __future__ import annotations

from typing import Any
from uuid import UUID

import httpx
import pytest

from omnia_api.core.errors import ApiError
from omnia_api.routers import integration_runtime
from omnia_api.services import integration_providers
from omnia_api.services.secret_safety import (
    redact_selected_element_secrets,
    selected_elements_contain_provider_secret,
)


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
async def test_runtime_ai_uses_encrypted_aitunnel_connection_without_exposing_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    capture: dict[str, Any] = {}
    response = httpx.Response(
        200,
        json={
            "model": "provider/sk-aitunnel-runtime-value",
            "choices": [
                {
                    "message": {
                        "content": (
                            "Готовый безопасный ответ sk-aitunnel-runtime-value"
                        )
                    }
                }
            ],
        },
    )
    monkeypatch.setattr(
        integration_runtime.httpx,
        "AsyncClient",
        lambda **kwargs: _FakeClient(response, capture, **kwargs),
    )

    async def fake_secrets(*_args: object) -> dict[str, str]:
        return {"api_key": "sk-aitunnel-runtime-value"}

    monkeypatch.setattr(integration_runtime, "_secrets", fake_secrets)

    result = await integration_runtime._request_aitunnel_ai(
        None,  # type: ignore[arg-type]
        object(),  # type: ignore[arg-type]
        system_prompt="Системная инструкция",
        user_message="Запрос пользователя",
    )

    assert result.answer == "Готовый безопасный ответ [CREDENTIAL REDACTED]"
    assert result.model == "provider/[CREDENTIAL REDACTED]"
    assert capture["url"] == "https://api.aitunnel.ru/v1/chat/completions"
    assert capture["json"]["model"] == "auto"
    assert "sk-aitunnel-runtime-value" not in result.model
    assert "sk-aitunnel-runtime-value" not in result.answer


@pytest.mark.asyncio
async def test_owner_key_runtime_fails_closed_when_rate_limiter_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unavailable_redis() -> None:
        raise RuntimeError("redis unavailable")

    monkeypatch.setattr(integration_runtime, "get_redis", unavailable_redis)

    with pytest.raises(ApiError) as raised:
        await integration_runtime._enforce_runtime_ai_limits(
            UUID(int=1),
            42,
            fail_closed=True,
        )

    assert raised.value.code == "integration_provider_unavailable"


def test_selected_element_credential_is_detected_and_redacted() -> None:
    selected = [
        {
            "selector": "#provider-key",
            "comment": "AITUNNEL ключ sk-aitunnel-selection-secret",
            "html": '<input value="sk-aitunnel-selection-secret">',
        }
    ]

    assert selected_elements_contain_provider_secret(selected) is True
    safe = redact_selected_element_secrets(selected)

    assert safe is not None
    assert "sk-aitunnel-selection-secret" not in str(safe)
    assert "[CREDENTIAL REDACTED]" in str(safe)
