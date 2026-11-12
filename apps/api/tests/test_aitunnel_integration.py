from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any
from uuid import UUID

import httpx
import pytest
from starlette.requests import Request

from yleum_api.core.crypto import decrypt_strong, encrypt_strong
from yleum_api.core.errors import ApiError
from yleum_api.routers import app_integrations, integration_runtime
from yleum_api.schemas.app_integration import NutritionAIKeyUpdate
from yleum_api.schemas.integration_runtime import RuntimeAIRequest
from yleum_api.services import integration_providers, runtime_nutrition_ai


class _FakeClient:
    def __init__(
        self,
        response: httpx.Response,
        capture: dict[str, Any],
        **_kwargs: Any,
    ):
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
async def test_legacy_aitunnel_cannot_verify_new_credentials() -> None:
    with pytest.raises(integration_providers.IntegrationProviderError):
        await integration_providers.verify_provider(
            "aitunnel", {}, {"api_key": "synthetic-legacy-key"},
        )


@pytest.mark.asyncio
async def test_runtime_ai_redacts_a_gateway_echo(
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

    result = await integration_runtime._request_gateway_ai(
        owner_id=UUID(int=1),
        project_id=UUID(int=2),
        system_prompt="Инструкция",
        user_message="Запрос",
    )

    assert result.answer == "Ответ [CREDENTIAL REDACTED]"
    assert raw_key not in result.model
    assert capture["json"]["model"] == "gemini-3.1-pro-preview-customtools"


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
    assert capture["values"][-6:] == (
        "8",
        "60",
        "120",
        "86400",
        "2000",
        "86400",
    )


@pytest.mark.asyncio
async def test_nutrition_uses_only_the_project_key_dish_and_portion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_id = UUID(int=42)
    project_key = "sk-aitunnel-synthetic-nutrition-only"
    project = SimpleNamespace(
        id=project_id,
        owner_id=UUID(int=1),
        runtime_ai_enabled=False,
        runtime_nutrition_ai_key_enc=encrypt_strong(project_key),
    )
    provider_requests: dict[str, Any] = {}
    response = httpx.Response(200, json={
        "choices": [{"finish_reason": "stop", "message": {
            "content": '{"kcal":130,"protein_g":2.7,"fat_g":0.3,"carbs_g":28}',
        }}],
    })

    async def runtime_context(*_args: object) -> integration_runtime.RuntimeContext:
        return integration_runtime.RuntimeContext(project_id, 42)

    class Session:
        async def get(self, _model: object, requested_id: UUID) -> object:
            assert requested_id == project_id
            return project

        async def rollback(self) -> None:
            assert not provider_requests

    class Redis:
        async def eval(self, _script: str, _count: int, *values: str) -> int:
            assert str(project_id) in values[2]
            assert values[-6:] == ("3", "60", "20", "86400", "100", "86400")
            return 0

    monkeypatch.setattr(integration_runtime, "_runtime_context", runtime_context)
    monkeypatch.setattr(integration_runtime, "get_redis", Redis)
    monkeypatch.setattr(
        integration_runtime.httpx, "AsyncClient",
        lambda **kwargs: _FakeClient(response, provider_requests, **kwargs),
    )
    request = Request({
        "type": "http", "headers": [(b"x-omnia-integration-assertion", b"verified")],
    })
    payload = RuntimeAIRequest(
        message="Рис отварной",
        context={"task": "nutrition_estimate", "portion_g": 100},
    )

    result = await integration_runtime.request_runtime_ai(
        project_id, payload, Session(), request, "",
    )

    assert json.loads(result.answer) == {
        "kcal": 130, "protein_g": 2.7, "fat_g": 0.3, "carbs_g": 28,
    }
    assert project_key not in result.model_dump_json()
    assert provider_requests["url"] == "https://api.aitunnel.ru/v1/chat/completions"
    assert provider_requests["headers"]["Authorization"] == "Bearer " + project_key
    assert json.loads(provider_requests["json"]["messages"][1]["content"]) == {
        "dish": "Рис отварной", "portion_g": 100,
    }
    assert "user" not in provider_requests["json"]

    with pytest.raises(ApiError) as raised:
        await integration_runtime.request_runtime_ai(
            project_id, payload, Session(), Request({"type": "http", "headers": []}), "",
        )
    assert raised.value.status_code == 401


@pytest.mark.asyncio
async def test_nutrition_key_setup_is_owner_only_and_stores_ciphertext(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner_id = UUID(int=1)
    project_id = UUID(int=42)
    project = SimpleNamespace(
        owner_id=owner_id, template="max_miniapp", runtime_nutrition_ai_key_enc=None,
    )
    key = "sk-aitunnel-synthetic-project-test"
    requests: dict[str, Any] = {}
    response = httpx.Response(200, json={"name": "test", "allowed_models": None})

    class Session:
        async def get(self, _model: object, requested_id: UUID) -> object:
            assert requested_id == project_id
            return project

        async def commit(self) -> None:
            assert project.runtime_nutrition_ai_key_enc != key

        async def rollback(self) -> None:
            assert not requests

    async def allow(_session: object, user_id: UUID) -> None:
        assert user_id == owner_id

    monkeypatch.setattr(app_integrations, "require_existing_max_account_access", lambda _user: None)
    monkeypatch.setattr(app_integrations, "assert_integrations_allowed", allow)
    monkeypatch.setattr(
        runtime_nutrition_ai.httpx, "AsyncClient",
        lambda **kwargs: _FakeClient(response, requests, **kwargs),
    )
    payload = NutritionAIKeyUpdate.model_validate({"api_key": key})
    with pytest.raises(ApiError) as raised:
        await app_integrations.set_nutrition_ai(
            project_id, payload, Session(), SimpleNamespace(id=UUID(int=2)),
        )
    assert raised.value.status_code == 404
    assert not requests

    result = await app_integrations.set_nutrition_ai(
        project_id, payload, Session(), SimpleNamespace(id=owner_id),
    )
    assert result.model_dump() == {"enabled": True}
    assert decrypt_strong(project.runtime_nutrition_ai_key_enc) == key
    assert requests["url"] == "https://api.aitunnel.ru/v1/aitunnel/key"


@pytest.mark.asyncio
async def test_nutrition_rejects_impossible_portion_in_provider_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = httpx.Response(200, json={
        "choices": [{"finish_reason": "stop", "message": {
            "content": '{"kcal":13000,"protein_g":2.7,"fat_g":0.3,"carbs_g":28}',
        }}],
    })
    monkeypatch.setattr(
        runtime_nutrition_ai.httpx, "AsyncClient",
        lambda **kwargs: _FakeClient(response, {}, **kwargs),
    )
    with pytest.raises(ApiError) as raised:
        await runtime_nutrition_ai.estimate_nutrition(
            "sk-aitunnel-synthetic-project-test", "Рис отварной", 100,
        )
    assert raised.value.code == "integration_response_invalid"
