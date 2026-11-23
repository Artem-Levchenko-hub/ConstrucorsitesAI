"""Exercise the signed MAX route, Redis admission, and AITunnel HTTP boundary."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from redis.asyncio import Redis

from tests.test_app_integrations_api import _max_init_data
from tests.test_integration_runtime_auth import assertion
from tests.test_integration_runtime_contracts import upstream
from yleum_api.core.config import get_settings
from yleum_api.core.crypto import encrypt_strong
from yleum_api.core.db import get_session
from yleum_api.core.errors import ApiError, api_error_handler, validation_error_handler
from yleum_api.routers import integration_runtime

_KEY = "sk-aitunnel-synthetic-runtime-value"
_PROVIDER = "https://api.aitunnel.ru/v1/chat/completions"
_PUBLISHED_MENU_PAYLOAD = {
    "message": (
        "Верни только JSON-массив из 30 троек [номер завтрака, номер обеда, номер ужина]. "
        "Номера начинаются с 1."
    ),
    "context": {"task": "meal_plan", "days": 30, "servings": 2},
}


@pytest.fixture
async def runtime_api():
    project = SimpleNamespace(
        id=uuid4(),
        owner_id=uuid4(),
        runtime_ai_enabled=False,
        runtime_aitunnel_ai_enabled=True,
        runtime_nutrition_ai_key_enc=encrypt_strong(_KEY),
    )
    max_bot = SimpleNamespace(bot_token_enc=encrypt_strong("test-max-token"))
    rollbacks = []

    class Session:
        async def execute(self, _statement):
            return SimpleNamespace(scalar_one_or_none=lambda: max_bot, scalars=list)

        async def get(self, _model, project_id):
            assert project_id == project.id
            return project

        async def rollback(self):
            rollbacks.append(True)

    app = FastAPI()
    app.include_router(integration_runtime.router)
    app.add_exception_handler(ApiError, api_error_handler)
    app.add_exception_handler(RequestValidationError, validation_error_handler)
    app.dependency_overrides[get_session] = Session
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://runtime.test",
    ) as client:
        yield client, project, rollbacks


@pytest.fixture
async def redis_backend(monkeypatch):
    async with Redis.from_url(get_settings().redis_url, decode_responses=True) as redis:
        await redis.ping()
        monkeypatch.setattr(integration_runtime, "get_redis", lambda: redis)
        yield redis


def _path(project, operation="ai"):
    return f"/api/runtime/projects/{project.id}/{operation}"


async def _ask(
    runtime_api, payload, *, user="42", authenticated=True, tampered=False, expired=False,
    same_origin=True, foreign=False,
):
    client, project, _rollbacks = runtime_api
    path = _path(project)
    body = json.dumps(payload).encode()
    headers = {"Content-Type": "application/json"}
    if authenticated:
        now = int(time.time())
        claims = {"iat": now - 60, "exp": now - 1} if expired else {}
        if same_origin is not None:
            claims["same_origin"] = same_origin
        headers["X-Omnia-Integration-Assertion"] = assertion(
            str(project.id), path, method="POST", body=b"{}" if tampered else body,
            max_user_id=user, bot_token="test-foreign-bot" if foreign else "test-max-token",
            **claims,
        )
    else:
        headers["X-MAX-Init-Data"] = _max_init_data("test-max-token", int(user))
        headers["Origin"] = "http://runtime.test"
    return await client.post(path, content=body, headers=headers)


def _completed(answer="Готово", *, finish="stop"):
    return httpx.Response(200, json={
        "model": f"untrusted/{_KEY}",
        "choices": [{"finish_reason": finish, "message": {"content": answer}}],
    })


async def test_published_meal_plan_uses_project_aitunnel_and_json_mode(
    runtime_api, redis_backend, monkeypatch,
):
    _client, project, rollbacks = runtime_api
    project.runtime_ai_enabled = True
    provider_calls = []
    days = [[1 + day % 2, 2, 3] for day in range(30)]

    def answer(request):
        assert rollbacks == [True]
        provider_calls.append(request)
        return _completed(json.dumps({"days": days}))

    upstream(monkeypatch, answer)
    response = await _ask(runtime_api, _PUBLISHED_MENU_PAYLOAD)
    replay = await _ask(runtime_api, _PUBLISHED_MENU_PAYLOAD)

    assert response.status_code == replay.status_code == 200
    assert replay.json() == response.json()
    assert json.loads(response.json()["answer"]) == days
    assert response.json()["model"] == "gemini-2.5-flash-lite"
    [request] = provider_calls
    assert str(request.url) == _PROVIDER
    assert request.headers["Authorization"] == f"Bearer {_KEY}"
    body = json.loads(request.content)
    assert body["response_format"] == {"type": "json_object"}
    assert body["stream"] is False
    assert 4_096 <= body["max_tokens"] <= 8_192
    assert 'поле "days"' in body["messages"][0]["content"]
    assert _PUBLISHED_MENU_PAYLOAD["message"] in body["messages"][1]["content"]
    assert '"task": "meal_plan"' in body["messages"][1]["content"]
    assert "user" not in body
    assert _KEY not in response.text


@pytest.mark.parametrize("response_body", [
    {"days": [[1, 2, 3]] * 29},
    {"days": [[1, 2]] * 30},
    {"days": [[0, 2, 3]] * 30},
    {"days": [[1, True, 3]] * 30},
    {"days": [[1, 2.0, 3]] * 30},
    {"days": [[1, 2, 3]] * 30, "extra": True},
])
async def test_published_meal_plan_rejects_invalid_triplets(
    runtime_api, redis_backend, monkeypatch, response_body,
):
    upstream(monkeypatch, lambda _: _completed(json.dumps(response_body)))

    response = await _ask(runtime_api, _PUBLISHED_MENU_PAYLOAD)

    assert response.status_code == 502
    assert response.json()["error"]["code"] == "integration_response_invalid"


@pytest.mark.parametrize("extra,has_json_mode", [
    ({"provider": "aitunnel"}, False),
    ({"provider": None}, False),
    ({"json_mode": True}, True),
    ({"json_mode": False}, False),
    ({"context": {"task": "meal_plan", "days": 7, "servings": 2}}, False),
])
async def test_meal_plan_adapter_is_only_for_legacy_30_day_request(
    runtime_api, redis_backend, monkeypatch, extra, has_json_mode,
):
    calls = []
    days = [[1, 2, 3]] * 30

    def answer(request):
        calls.append(json.loads(request.content))
        if len(calls) == 1:
            return _completed(json.dumps({"days": days}))
        return _completed('{"answer":"Меню готово"}')

    upstream(monkeypatch, answer)
    legacy = await _ask(runtime_api, _PUBLISHED_MENU_PAYLOAD)
    response = await _ask(runtime_api, {**_PUBLISHED_MENU_PAYLOAD, **extra})

    assert legacy.status_code == response.status_code == 200
    assert json.loads(legacy.json()["answer"]) == days
    assert json.loads(response.json()["answer"]) == {"answer": "Меню готово"}
    assert len(calls) == 2
    assert ("response_format" in calls[1]) is has_json_mode


@pytest.mark.parametrize(
    "auth", [{"authenticated": False}, {"tampered": True}, {"expired": True}, {"foreign": True}],
)
async def test_owner_key_requires_verified_core_assertion(runtime_api, monkeypatch, auth):
    upstream(monkeypatch, lambda _: pytest.fail("Unauthenticated request reached provider"))

    response = await _ask(runtime_api, {"message": "Привет"}, **auth)

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "max_init_data_invalid"


@pytest.mark.parametrize("payload", [
    {"message": "Привет", "provider": "aitunnel"},
    _PUBLISHED_MENU_PAYLOAD,
])
async def test_old_core_must_be_updated_before_aitunnel_text_dispatch(
    runtime_api, monkeypatch, payload,
):
    monkeypatch.setattr(
        integration_runtime, "get_redis", lambda: pytest.fail("Old core reached Redis"),
    )
    upstream(monkeypatch, lambda _: pytest.fail("Old core reached provider"))

    response = await _ask(runtime_api, payload, same_origin=None)

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "max_deploy_required"


@pytest.mark.parametrize("auth,expected_status,expected_error", [
    ({"same_origin": None}, 409, "max_deploy_required"),
    ({"same_origin": False}, 409, "max_deploy_required"),
    ({"same_origin": "true"}, 409, "max_deploy_required"),
    ({"same_origin": 1}, 409, "max_deploy_required"),
    ({"authenticated": False}, 401, "max_init_data_invalid"),
    ({"expired": True}, 401, "max_init_data_invalid"),
    ({"foreign": True}, 401, "max_init_data_invalid"),
])
async def test_unattested_or_invalid_request_cannot_read_an_aitunnel_receipt(
    runtime_api, redis_backend, monkeypatch, auth, expected_status, expected_error,
):
    upstream(monkeypatch, lambda _: _completed("Результат исходного запроса"))
    payload = {"message": "Привет", "idempotency_key": "valid-session-only"}
    first = await _ask(runtime_api, payload)
    assert first.status_code == 200

    monkeypatch.setattr(
        integration_runtime, "get_redis", lambda: pytest.fail("Untrusted request reached Redis"),
    )
    response = await _ask(runtime_api, payload, **auth)

    assert response.status_code == expected_status
    assert response.json()["error"]["code"] == expected_error


@pytest.mark.parametrize("configured", ["missing_flag", "missing_key"])
async def test_explicit_aitunnel_never_falls_back(runtime_api, monkeypatch, configured):
    _client, project, _rollbacks = runtime_api
    project.runtime_ai_enabled = True
    if configured == "missing_flag":
        project.runtime_aitunnel_ai_enabled = False
    else:
        project.runtime_nutrition_ai_key_enc = None
    upstream(monkeypatch, lambda _: pytest.fail("Explicit AITunnel request reached a provider"))

    response = await _ask(runtime_api, {"message": "Привет", "provider": "aitunnel"})

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "ai_integration_required"


@pytest.mark.parametrize("enabled,platform_ai", [(False, False), (True, False), (True, True)])
async def test_status_distinguishes_legacy_nutrition_and_general_aitunnel(
    runtime_api, enabled, platform_ai,
):
    client, project, _rollbacks = runtime_api
    project.runtime_aitunnel_ai_enabled = enabled
    project.runtime_ai_enabled = platform_ai
    path = _path(project, "integrations")

    response = await client.get(path, headers={
        "X-Omnia-Integration-Assertion": assertion(str(project.id), path),
    })

    assert response.status_code == 200
    body = response.json()
    assert body["providers"] == (["aitunnel", "llmgw"] if platform_ai else ["aitunnel"])
    assert "Оценка КБЖУ" in body["capabilities"]
    assert ("ИИ-ответы" in body["capabilities"]) is enabled
    assert body.get("ai_provider") == ("aitunnel" if enabled else None)


async def test_ordinary_implicit_request_keeps_gateway_billing_when_both_are_enabled(
    runtime_api, redis_backend, monkeypatch,
):
    _client, project, _rollbacks = runtime_api
    project.runtime_ai_enabled = True
    monkeypatch.setattr(get_settings(), "llm_gateway_url", "http://gateway.test")
    calls = []

    def answer(request):
        calls.append(request)
        return httpx.Response(200, json={
            "model": "platform-model", "choices": [{"message": {"content": "Через платформу"}}],
        })

    upstream(monkeypatch, answer)
    response = await _ask(runtime_api, {"message": "Привет"}, same_origin=None)

    assert response.status_code == 200
    assert response.json() == {"answer": "Через платформу", "model": "platform-model"}
    [request] = calls
    assert str(request.url) == "http://gateway.test/v1/chat/completions"
    assert "Authorization" not in request.headers
    payload = json.loads(request.content)
    assert payload["user"] == str(project.owner_id)
    assert payload["metadata"]["require_billing"] is True


async def test_published_meal_plan_retry_stays_blocked_after_switch_to_gateway(
    runtime_api, redis_backend, monkeypatch,
):
    _client, project, _rollbacks = runtime_api
    monkeypatch.setattr(get_settings(), "llm_gateway_url", "http://gateway.test")
    days = [[1, 2, 3]] * 30
    calls = []

    def answer(request):
        calls.append(str(request.url))
        if str(request.url) == _PROVIDER:
            return _completed(json.dumps({"days": days}))
        return _completed("Ответ LLMGW")

    upstream(monkeypatch, answer)
    original = await _ask(runtime_api, _PUBLISHED_MENU_PAYLOAD)
    assert original.status_code == 200
    assert json.loads(original.json()["answer"]) == days
    project.runtime_aitunnel_ai_enabled = False
    project.runtime_ai_enabled = True

    replay = await _ask(runtime_api, _PUBLISHED_MENU_PAYLOAD)
    independent = await _ask(runtime_api, {"message": "Отдельный вопрос"})

    assert replay.status_code == 409
    assert replay.json()["error"]["code"] == "integration_operation_conflict"
    assert independent.status_code == 200
    assert independent.json()["answer"] == "Ответ LLMGW"
    assert calls == [_PROVIDER, "http://gateway.test/v1/chat/completions"]


@pytest.mark.parametrize("explicit_key", [False, True])
async def test_ambiguous_aitunnel_request_cannot_switch_to_gateway_billing(
    runtime_api, redis_backend, monkeypatch, explicit_key,
):
    _client, project, _rollbacks = runtime_api
    monkeypatch.setattr(get_settings(), "llm_gateway_url", "http://gateway.test")
    calls = []

    def answer(request):
        calls.append(str(request.url))
        if str(request.url) == _PROVIDER:
            raise httpx.ReadTimeout("synthetic upstream timeout", request=request)
        return _completed("Ответ LLMGW")

    upstream(monkeypatch, answer)
    payload = {"message": "Исключи повторную оплату"}
    if explicit_key:
        payload["idempotency_key"] = "one-request"
    original = await _ask(runtime_api, payload)
    assert original.status_code == 503
    project.runtime_ai_enabled = True

    retry_payload = {**payload, "message": "Изменённые данные"} if explicit_key else payload
    replay = await _ask(runtime_api, retry_payload)
    assert replay.status_code == 409
    assert replay.json()["error"]["code"] == "integration_operation_conflict"
    assert calls == [_PROVIDER]

    legacy_gateway = await _ask(runtime_api, retry_payload, same_origin=None)
    assert legacy_gateway.status_code == 200
    assert calls == [_PROVIDER, "http://gateway.test/v1/chat/completions"]


async def test_legacy_nutrition_accepts_valid_older_core(runtime_api, redis_backend, monkeypatch):
    values = {"kcal": 130, "protein_g": 2.7, "fat_g": 0.3, "carbs_g": 28}
    upstream(monkeypatch, lambda _: _completed(json.dumps(values)))

    response = await _ask(runtime_api, {
        "message": "Рис отварной", "context": {"task": "nutrition_estimate", "portion_g": 100},
    }, same_origin=None)

    assert response.status_code == 200
    assert json.loads(response.json()["answer"]) == values


async def test_concurrent_then_replayed_operation_dispatches_once(
    runtime_api, redis_backend, monkeypatch,
):
    entered = asyncio.Event()
    finish = asyncio.Event()
    calls = []

    async def answer(request):
        calls.append(request)
        entered.set()
        await finish.wait()
        return _completed('{"ok":true}')

    upstream(monkeypatch, answer)
    payload = {
        "message": "Привет", "provider": "aitunnel", "json_mode": True,
        "idempotency_key": "same-action:123",
    }

    async with asyncio.TaskGroup() as group:
        first_task = group.create_task(_ask(runtime_api, payload))
        try:
            await asyncio.wait_for(entered.wait(), timeout=2)
            concurrent = await _ask(runtime_api, payload)
            assert concurrent.status_code == 409
            assert concurrent.json()["error"]["code"] == "integration_operation_conflict"
        finally:
            finish.set()

    first = first_task.result()
    replay = await _ask(runtime_api, payload)
    expired = await _ask(runtime_api, payload, expired=True)
    different = await _ask(runtime_api, {**payload, "message": "Другой запрос"})
    assert first.status_code == replay.status_code == 200
    assert first.json() == replay.json()
    assert expired.status_code == 401
    assert expired.json()["error"]["code"] == "max_init_data_invalid"
    assert different.status_code == 409
    assert different.json()["error"]["code"] == "idempotency_conflict"
    [request] = calls
    assert json.loads(request.content)["response_format"] == {"type": "json_object"}


@pytest.mark.parametrize("explicit", [False, True])
async def test_replay_does_not_charge_again_after_key_resave_or_rotation(
    runtime_api, redis_backend, monkeypatch, explicit,
):
    _client, project, _rollbacks = runtime_api
    calls = []

    def answer(request):
        calls.append(request)
        return _completed(f"Ответ {len(calls)}")

    upstream(monkeypatch, answer)
    payload = {"message": "Привет"}
    if explicit:
        payload["idempotency_key"] = "one-intent"
    a = await _ask(runtime_api, payload)
    replay = await _ask(runtime_api, payload)
    b = await _ask(runtime_api, payload, user="43")
    project.runtime_nutrition_ai_key_enc = encrypt_strong(_KEY)
    resaved = await _ask(runtime_api, payload)
    new_key = "sk-aitunnel-synthetic-rotated-runtime-key"
    project.runtime_nutrition_ai_key_enc = encrypt_strong(new_key)
    rotated = await _ask(runtime_api, payload)

    assert [r.status_code for r in (a, replay, b, resaved, rotated)] == [200] * 5
    assert [r.json()["answer"] for r in (a, replay, b, resaved, rotated)] == [
        "Ответ 1", "Ответ 1", "Ответ 2", "Ответ 1", "Ответ 1",
    ]
    if explicit:
        changed_payload = await _ask(runtime_api, {**payload, "message": "Другая задача"})
        assert changed_payload.status_code == 409
        assert changed_payload.json()["error"]["code"] == "idempotency_conflict"
    assert len(calls) == 2

    new_payload = {"message": "Новая задача"}
    if explicit:
        new_payload["idempotency_key"] = "next-intent"
    fresh = await _ask(runtime_api, new_payload)
    assert fresh.status_code == 200
    assert len(calls) == 3
    assert calls[2].headers["Authorization"] == f"Bearer {new_key}"
    assert "response_format" not in json.loads(calls[0].content)

    project.runtime_nutrition_ai_key_enc = None
    blocked = await _ask(runtime_api, payload)
    assert blocked.status_code == 409
    assert blocked.json()["error"]["code"] == "ai_integration_required"
    assert len(calls) == 3


async def test_paid_dispatch_uses_existing_visitor_and_project_limits(
    runtime_api, redis_backend, monkeypatch,
):
    _client, project, _rollbacks = runtime_api
    calls = []

    def answer(request):
        calls.append(request)
        return _completed()

    upstream(monkeypatch, answer)
    paid = {"message": "Привет", "idempotency_key": "operation-0"}
    assert (await _ask(runtime_api, paid)).status_code == 200
    assert (await _ask(runtime_api, paid)).status_code == 200

    for number in range(1, 8):
        assert (await _ask(runtime_api, {
            **paid, "idempotency_key": f"operation-{number}",
        })).status_code == 200
    assert len(calls) == 8
    assert await redis_backend.get(f"omnia:runtime-ai:project-day:{project.id}") == "8"

    limited = {**paid, "idempotency_key": "operation-8"}
    response = await _ask(runtime_api, limited)
    assert response.status_code == 429
    assert response.json()["error"]["code"] == "rate_limited"
    assert len(calls) == 8

    await redis_backend.delete(f"omnia:runtime-ai:minute:{project.id}:42")
    assert (await _ask(runtime_api, limited)).status_code == 200
    assert len(calls) == 9


@pytest.mark.parametrize("explicit", [False, True])
async def test_ambiguous_timeout_replays_safe_error_without_retry(
    runtime_api, redis_backend, monkeypatch, caplog, explicit,
):
    calls = []
    caplog.set_level(logging.INFO, logger="yleum_api.services.runtime_nutrition_ai")

    def timeout(request):
        calls.append(request)
        raise httpx.ReadTimeout(f"private upstream error {_KEY}", request=request)

    upstream(monkeypatch, timeout)
    payload = {"message": "Секретный тестовый запрос"}
    if explicit:
        payload["idempotency_key"] = "ambiguous-timeout"
    first = await _ask(runtime_api, payload)
    replay = await _ask(runtime_api, payload)

    assert first.status_code == replay.status_code == 503
    assert first.json() == replay.json()
    assert first.json()["error"]["code"] == "integration_provider_unavailable"
    assert len(calls) == 1
    assert "provider=aitunnel" in caplog.text
    assert caplog.text.count("status=integration_provider_unavailable") == 1
    assert _KEY not in first.text + caplog.text
    assert payload["message"] not in caplog.text


@pytest.mark.parametrize("explicit_key", [False, True])
async def test_provider_rate_limit_replay_expires_after_one_minute(
    runtime_api, redis_backend, monkeypatch, explicit_key,
):
    keys = set()
    calls = []
    execute = redis_backend.eval

    async def record_single_key(script, numkeys, *args):
        if numkeys == 1:
            keys.add(args[0])
        return await execute(script, numkeys, *args)

    def answer(request):
        calls.append(request)
        return httpx.Response(429) if len(calls) == 1 else _completed()

    monkeypatch.setattr(redis_backend, "eval", record_single_key)
    upstream(monkeypatch, answer)
    payload = {"message": "Привет"}
    if explicit_key:
        payload["idempotency_key"] = "rate-limited-request"

    first = await _ask(runtime_api, payload)
    replay = await _ask(runtime_api, payload)
    assert first.status_code == replay.status_code == 429
    assert first.json() == replay.json()
    assert first.json()["error"]["code"] == "rate_limited"
    assert len(calls) == 1
    [key] = keys
    assert 55_000 <= await redis_backend.pttl(key) <= 60_000
    assert "Повторите через минуту" in first.json()["error"]["message"]

    await redis_backend.pexpire(key, 0)
    subsequent = await _ask(runtime_api, payload)
    assert subsequent.status_code == 200
    assert len(calls) == 2


async def test_redis_outage_blocks_paid_aitunnel(runtime_api, monkeypatch):
    def unavailable():
        raise RuntimeError("synthetic Redis failure")

    monkeypatch.setattr(integration_runtime, "get_redis", unavailable)
    upstream(monkeypatch, lambda _: pytest.fail("Redis outage led to a paid request"))

    response = await _ask(runtime_api, {"message": "Привет"})

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "integration_provider_unavailable"


@pytest.mark.parametrize("key", ["", "spaces in key", "*", "a" * 129])
async def test_idempotency_key_has_bounded_ascii_format(runtime_api, monkeypatch, key):
    upstream(monkeypatch, lambda _: pytest.fail("Invalid request reached provider"))

    response = await _ask(runtime_api, {"message": "Привет", "idempotency_key": key})

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_failed"


@pytest.mark.parametrize(
    "body,expected_status,expected_code",
    [
        (_completed('{"x":1}', finish="length"), 502, "integration_response_invalid"),
        (_completed("[]"), 502, "integration_response_invalid"),
        (_completed('{"x":NaN}'), 502, "integration_response_invalid"),
        (_completed('{"x":'), 502, "integration_response_invalid"),
        (_completed(""), 502, "integration_response_invalid"),
        (httpx.Response(401), 422, "integration_credentials_invalid"),
        (httpx.Response(402), 402, "wallet_empty"),
        (httpx.Response(429), 429, "rate_limited"),
        (httpx.Response(503), 503, "integration_provider_unavailable"),
    ],
)
async def test_provider_rejects_partial_or_malformed_output_without_secret_echo(
    runtime_api, redis_backend, monkeypatch, body, expected_status, expected_code,
):
    _client, project, _rollbacks = runtime_api
    project.runtime_ai_enabled = True
    calls = []

    def answer(request):
        assert str(request.url) == _PROVIDER
        calls.append(request)
        return body

    upstream(monkeypatch, answer)

    response = await _ask(runtime_api, {
        "message": "Привет", "provider": "aitunnel", "json_mode": True,
    })

    assert response.status_code == expected_status
    assert response.json()["error"]["code"] == expected_code
    assert _KEY not in response.text
    assert len(calls) == 1


async def test_escaped_provider_key_is_redacted_inside_valid_json(
    runtime_api, redis_backend, monkeypatch,
):
    escaped = _KEY.replace("s", r"\u0073", 1)
    upstream(monkeypatch, lambda _: _completed('{"answer":"' + escaped + '"}'))

    response = await _ask(runtime_api, {"message": "Привет", "json_mode": True})

    assert response.status_code == 200
    assert json.loads(response.json()["answer"]) == {"answer": "[CREDENTIAL REDACTED]"}
    assert _KEY not in response.text
