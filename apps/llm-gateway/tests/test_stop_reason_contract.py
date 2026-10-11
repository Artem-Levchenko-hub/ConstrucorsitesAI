"""Unusable paid output is settled once, but never reported as successful."""

import json
from decimal import Decimal
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient

from yleum_gateway.core.config import reset_settings_cache
from yleum_gateway.main import create_app
from yleum_gateway.routers import chat, messages_native
from yleum_gateway.services import provider_calls


@pytest.fixture
def client(neutralize_lifespan, neutralize_side_effects, monkeypatch):
    monkeypatch.setenv("LLMGW_API_KEY", "synthetic-test-key")
    reset_settings_cache()
    monkeypatch.setattr(
        messages_native,
        "native_messages_route",
        lambda: ("synthetic-test-key", "https://api.llmgw.ru/v1"),
    )
    with TestClient(create_app()) as client:
        yield client


def request_output(client, monkeypatch, route, reason):
    calls = []
    message = {
        "content": '<omnia:action>{"type":"finish"}</omnia:action>',
        "tool_calls": [
            {
                "id": "test-tool",
                "type": "function",
                "function": {"name": "write_file", "arguments": '{"path":"x","content":"y"}'},
            }
        ],
    }
    choice = {"message": message}
    if reason != "missing":
        choice["finish_reason"] = reason
    receipt = {
        "id": "test-request",
        "model": "anthropic/claude-sonnet-5",
        "usage": {"prompt_tokens": 10, "completion_tokens": 20},
        "choices": [choice],
    }

    def reply(request):
        calls.append(request)
        if route == "stream":
            chunks = [
                {**receipt, "choices": [{"delta": {"content": message["content"]}}]},
                {**receipt, "choices": [{k: v for k, v in choice.items() if k != "message"}]},
            ]
            return httpx.Response(
                200,
                headers={"x-llmgw-cost-rub": "0.25"},
                content="".join(f"data: {json.dumps(chunk)}\n\n" for chunk in chunks)
                + "data: [DONE]\n\n",
            )
        return httpx.Response(200, headers={"x-llmgw-cost-rub": "0.25"}, json=receipt)

    monkeypatch.setattr(httpx, "HTTPTransport", lambda *a, **kw: httpx.MockTransport(reply))
    response = client.post(
        "/v1/messages" if route == "native" else "/v1/chat/completions",
        json={
            "model": "claude-sonnet-5",
            "messages": [{"role": "user", "content": "test"}],
            "max_tokens": 100,
            "user": str(uuid4()),
            **({"stream": True} if route == "stream" else {}),
        },
    )
    return response, calls


@pytest.mark.parametrize("route", ["native", "chat", "stream"])
@pytest.mark.parametrize(
    "reason", ["missing", None, "", "unknown", "pause_turn", "content_filter", [], {}]
)
def test_unusable_output_settles_once_without_success(client, monkeypatch, route, reason):
    async def charge(**kwargs):
        provider_calls.finish_call.assert_awaited_once()
        assert kwargs["cost_rub"] == Decimal("0.25")

    chat.billing.charge.side_effect = charge
    response, calls = request_output(client, monkeypatch, route, reason)
    if route == "stream":
        events = [line[6:] for line in response.text.splitlines() if line.startswith("data: ")]
        assert "[DONE]" not in events
        assert not any(json.loads(event).get("usage") for event in events)
        error = json.loads(events[-1])["error"]
    else:
        assert response.status_code == 502, response.text
        body = response.json()
        error = body.get("detail", body)["error"]
        assert "choices" not in body and "content" not in body
    assert error["code"] == (
        "provider_output_filtered" if reason == "content_filter" else "provider_output_invalid"
    )
    assert error.get("retry_safe", error.get("details", {}).get("retry_safe")) is False
    assert len(calls) == 1
    provider_calls.finish_call.assert_awaited_once()
    assert provider_calls.finish_call.await_args.kwargs.get("status", "completed") == "completed"
    assert provider_calls.finish_call.await_args.kwargs["provider_cost_rub"] is not None
    chat.billing.charge.assert_awaited_once()
    chat.cache.set.assert_not_awaited()


@pytest.mark.parametrize("route", ["native", "chat", "stream"])
def test_accounting_failure_precedes_output_failure(client, monkeypatch, route):
    from yleum_gateway.core.errors import BillingReconciliationRequiredError

    chat.billing.charge.side_effect = BillingReconciliationRequiredError("test accounting failure")
    response, calls = request_output(client, monkeypatch, route, "content_filter")
    if route == "stream":
        body = json.loads(
            [line[6:] for line in response.text.splitlines() if line.startswith("data: ")][-1]
        )
    else:
        assert response.status_code == 503
        body = response.json()
    error = body.get("detail", body)["error"]
    assert error["code"] == "billing_reconciliation_required"
    assert len(calls) == 1
    chat.billing.charge.assert_awaited_once()
    provider_calls.finish_call.assert_awaited_once()
    chat.cache.set.assert_not_awaited()


@pytest.mark.parametrize("reason", ["stop", "tool_calls", "length"])
def test_stream_preserves_supported_terminal_reason(client, monkeypatch, reason):
    response, calls = request_output(client, monkeypatch, "stream", reason)
    events = [line[6:] for line in response.text.splitlines() if line.startswith("data: ")]
    assert events[-1] == "[DONE]"
    assert json.loads(events[-2])["choices"][0]["finish_reason"] == reason
    assert len(calls) == 1


def test_unsafe_legacy_cache_entry_is_not_returned(client, monkeypatch):
    monkeypatch.setattr(
        chat.cache, "get", AsyncMock(return_value={"choices": [{"message": {"content": "unsafe"}}]})
    )
    response, calls = request_output(client, monkeypatch, "chat", "stop")
    assert response.status_code == 200
    assert len(calls) == 1
    assert response.json()["metadata"]["cache_hit"] is False


@pytest.mark.parametrize("route", ["native", "chat", "stream"])
def test_journal_failure_precedes_output_failure_without_debit(client, monkeypatch, route):
    from yleum_gateway.core.errors import BillingReconciliationRequiredError

    provider_calls.finish_call.side_effect = BillingReconciliationRequiredError(
        "test receipt failure"
    )
    response, calls = request_output(client, monkeypatch, route, "unknown")
    if route == "stream":
        body = json.loads(
            [line[6:] for line in response.text.splitlines() if line.startswith("data: ")][-1]
        )
    else:
        assert response.status_code == 503
        body = response.json()
    error = body.get("detail", body)["error"]
    assert error.get("code", error.get("type")) in {
        "billing_unavailable",
        "billing_reconciliation_required",
    }
    assert len(calls) == 1
    provider_calls.finish_call.assert_awaited_once()
    chat.billing.charge.assert_not_awaited()
    chat.cache.set.assert_not_awaited()


@pytest.mark.parametrize("first", ["content_filter", "unknown", "length"])
def test_conflicting_stream_terminal_is_never_rescued_by_later_stop(client, monkeypatch, first):
    chunks = [
        {
            "choices": [{"finish_reason": reason}],
            "model": "anthropic/claude-sonnet-5",
            "usage": {"prompt_tokens": 10, "completion_tokens": 20},
        }
        for reason in [first, "stop"]
    ]
    content = "".join(f"data: {json.dumps(chunk)}\n\n" for chunk in chunks) + "data: [DONE]\n\n"
    monkeypatch.setattr(
        httpx,
        "HTTPTransport",
        lambda *a, **kw: httpx.MockTransport(lambda request: httpx.Response(200, content=content)),
    )
    response = client.post(
        "/v1/chat/completions",
        json={
            "model": "claude-sonnet-5",
            "messages": [{"role": "user", "content": "test"}],
            "stream": True,
            "user": str(uuid4()),
        },
    )
    assert "[DONE]" not in response.text
    assert "provider_output_invalid" in response.text
    chat.billing.charge.assert_awaited_once()
