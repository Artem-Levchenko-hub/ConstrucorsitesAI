"""Observe output policy at the upstream transport, without provider calls."""

import hashlib
import json
from decimal import Decimal
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient

from yleum_gateway.core.config import reset_settings_cache
from yleum_gateway.core.errors import WalletEmptyError
from yleum_gateway.main import create_app
from yleum_gateway.routers import messages_native
from yleum_gateway.services import cache


@pytest.fixture
def client(neutralize_lifespan, neutralize_side_effects):
    with TestClient(create_app()) as value:
        yield value


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("model,default", [
    ("claude-sonnet-5.5", None),
    ("claude-sonnet-5", 8192),
    ("~anthropic/claude-sonnet-latest", 8192),
    ("gemini-3.1-pro-preview-customtools", 8192),
])
@pytest.mark.parametrize("budget", ["absent", None, 4096])
def test_chat_upstream_budget(client, monkeypatch, stream, model, default, budget):
    monkeypatch.setenv("LLMGW_API_KEY", "test-only-key")
    reset_settings_cache()
    captured = []

    def provider(request):
        payload = json.loads(request.content)
        captured.append(payload)
        if stream:
            chunk = {"choices": [{"delta": {"content": "ok"}, "finish_reason": "stop"}],
                     "usage": {"prompt_tokens": 10, "completion_tokens": 1}}
            return httpx.Response(200, text="data: " + json.dumps(chunk) + "\n\ndata: [DONE]\n\n")
        return httpx.Response(200, json={
            "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 1},
        })

    monkeypatch.setattr(httpx, "HTTPTransport", lambda *a, **kw: httpx.MockTransport(provider))
    body = {"model": model, "messages": [{"role": "user", "content": "hello"}], "stream": stream}
    if budget != "absent":
        body["max_tokens"] = budget
    response = client.post("/v1/chat/completions", json=body)
    assert response.status_code == 200, response.text
    assert len(captured) == 1
    expected = 4096 if budget == 4096 else (32768 if stream and default else default)
    if expected is None:
        assert "max_tokens" not in captured[0]
    else:
        assert captured[0]["max_tokens"] == expected


@pytest.mark.parametrize("model,slug,default", [
    ("claude-sonnet-5.5", "anthropic/claude-sonnet-5.5", None),
    ("anthropic/claude-sonnet-5.5", "anthropic/claude-sonnet-5.5", None),
    ("claude-sonnet-5", "anthropic/claude-sonnet-5", 8192),
    ("~anthropic/claude-sonnet-latest", "~anthropic/claude-sonnet-latest", 8192),
])
@pytest.mark.parametrize("budget", ["absent", None, 4096, 128000])
def test_native_omits_only_automatic_budget_and_preserves_precheck(
    client, monkeypatch, model, slug, default, budget,
):
    captured = []
    precheck = AsyncMock()
    monkeypatch.setattr(messages_native.billing, "precheck_balance", precheck)
    monkeypatch.setattr(messages_native.billing, "charge", AsyncMock(return_value=uuid4()))
    monkeypatch.setattr(messages_native.file_logger, "log_request", lambda value: None)
    monkeypatch.setattr(messages_native, "native_messages_route", lambda: ("fixture", "https://provider.test/v1"))

    def provider(url, payload, headers):
        captured.append(payload)
        return httpx.Response(200, json={
            "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 1, "cost_rub": "0.01"},
        })

    monkeypatch.setattr(messages_native, "_post_llmgw", provider)
    body = {"model": model, "user": str(uuid4()),
            "messages": [{"role": "user", "content": "x" * 40000}]}
    if budget != "absent":
        body["max_tokens"] = budget
    response = client.post("/v1/messages", json=body)
    assert response.status_code == 200, response.text
    assert len(captured) == 1
    assert captured[0]["model"] == slug
    expected = default if budget in ("absent", None) else budget
    if expected is None:
        assert "max_tokens" not in captured[0]
    else:
        assert captured[0]["max_tokens"] == expected
    precheck.assert_awaited_once()
    # Hand-calculated floor: >=10k input and 2.5k estimated output at existing tariff.
    if default is None:
        assert precheck.await_args.args[1] >= Decimal("7.8707")


@pytest.mark.parametrize("model,budget,changes", [
    ("claude-sonnet-5.5", None, True),
    ("claude-sonnet-5.5", 4096, False),
    ("claude-sonnet-5", None, False),
    ("~anthropic/claude-sonnet-latest", None, False),
])
def test_cache_separates_provider_default_from_historical_default(model, budget, changes):
    messages = [{"role": "user", "content": "hello"}]
    historical = {"model": model, "messages": messages, "user_id": None,
                  "project_id": None, "temperature": None, "max_tokens": budget}
    old_key = "llm:cache:v2:" + hashlib.sha256(json.dumps(
        historical, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode()).hexdigest()
    assert (cache.make_cache_key(model, messages, max_tokens=budget) != old_key) is changes


def test_provider_default_does_not_lower_native_wallet_estimate():
    payload = {"messages": [{"role": "user", "content": "x" * 40000}]}
    estimate = messages_native._estimate_native_cost("claude-sonnet-5.5", payload)
    previous = messages_native._estimate_native_cost(
        "claude-sonnet-5.5", {**payload, "max_tokens": 128000},
    )
    assert estimate >= previous


@pytest.mark.parametrize("model", ["claude-sonnet-5.5", "anthropic/claude-sonnet-5.5"])
def test_provider_default_wallet_refusal_prevents_upstream_call(client, monkeypatch, model):
    monkeypatch.setattr(messages_native.billing, "precheck_balance", AsyncMock(
        side_effect=WalletEmptyError("Insufficient wallet balance for request"),
    ))
    monkeypatch.setattr(messages_native, "native_messages_route", lambda: ("fixture", "https://provider.test/v1"))
    monkeypatch.setattr(messages_native, "_post_llmgw", pytest.fail)
    response = client.post("/v1/messages", json={
        "model": model, "user": str(uuid4()),
        "messages": [{"role": "user", "content": "do not spend"}],
    })
    assert response.status_code == 402
    assert response.json()["error"]["type"] == "wallet_empty"
