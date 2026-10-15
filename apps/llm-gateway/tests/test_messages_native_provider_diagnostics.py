"""Native error diagnostics keep provider text inside the gateway boundary."""

from decimal import Decimal
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi.testclient import TestClient

from yleum_gateway.main import create_app
from yleum_gateway.routers import messages_native
from yleum_gateway.services import billing, provider_calls


@pytest.fixture
def client(neutralize_lifespan, neutralize_side_effects):
    with TestClient(create_app()) as client:
        yield client


@pytest.mark.parametrize("kind", [httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout])
@pytest.mark.parametrize("journal_ok", [False, True])
def test_preconnect_permission_requires_closed_failed_receipt(client, monkeypatch, kind, journal_ok):
    monkeypatch.setattr(messages_native, "native_messages_route",
                        lambda: ("synthetic", "https://provider.test/v1"))
    closed = []

    async def finish(*args, **kwargs):
        assert kwargs["status"] == "failed"
        if not journal_ok:
            raise RuntimeError("private journal detail")
        closed.append(True)

    monkeypatch.setattr(provider_calls, "finish_call", AsyncMock(side_effect=finish))

    def fail(*args):
        raise kind("private transport detail")

    monkeypatch.setattr(messages_native, "_post_llmgw", fail)
    response = client.post("/v1/messages", json={
        "model": "claude-sonnet-5", "messages": [{"role": "user", "content": "synthetic"}],
    })
    assert "private" not in response.text
    if journal_ok:
        assert closed == [True]
        assert response.status_code == 502
        assert response.json()["error"] == {
            "type": "api_error", "message": "Provider connection could not be established",
            "details": {
                "provider_failure_kind": "preconnect", "provider_charge_ambiguous": False,
                "retry_safe": True, "retry_policy": "llmgw_preconnect_v1",
            },
        }
    else:
        assert response.status_code == 503
        assert "retry_safe" not in response.text and "llmgw_preconnect_v1" not in response.text
    assert provider_calls.finish_call.await_count == 1
    assert billing.charge.await_count == 0


@pytest.mark.parametrize("kind", [httpx.WriteTimeout, httpx.ReadTimeout, httpx.RemoteProtocolError])
def test_uncertain_transport_outcome_never_publishes_preconnect_permission(client, monkeypatch, kind):
    monkeypatch.setattr(messages_native, "native_messages_route",
                        lambda: ("synthetic", "https://provider.test/v1"))
    calls = []

    def fail(*args):
        calls.append(1)
        raise kind("private transport detail")

    monkeypatch.setattr(messages_native, "_post_llmgw", fail)
    response = client.post("/v1/messages", json={
        "model": "claude-sonnet-5", "messages": [{"role": "user", "content": "synthetic"}],
    })
    assert calls == [1] and response.status_code == 503
    assert response.json()["error"]["type"] == "billing_unavailable"
    assert "retry_safe" not in response.text and "private" not in response.text
    assert provider_calls.finish_call.await_args.kwargs["status"] == "ambiguous"
    assert billing.charge.await_count == 0


@pytest.mark.parametrize("status,body,category", [
    (401, {"error": {"code": "invalid_api_key"}}, "access"),
    (401, {"error": {"code": "insufficient_balance"}}, "funds"),
    (429, {"error": {"type": "rate_limit_error"}}, "rate_limit"),
    (401, {"error": {"message": "insufficient balance private-key"}}, None),
    (401, {"error": {"code": "insufficient_quota"}}, None),
    (401, {"error": {"code": "insufficient_funds", "type": "authentication_error"}}, None),
    (401, {"error": ["private-key"]}, None),
    (401, ["private-key"], None),
    (401, "private-key not-json", None),
    (402, {"error": {"message": "private-key"}}, None),
])
def test_native_refusal_returns_only_safe_category_and_keeps_receipt(
    client, monkeypatch, status, body, category,
):
    monkeypatch.setattr(messages_native, "native_messages_route",
                        lambda: ("synthetic", "https://provider.test/v1"))
    calls = []

    def reply(*args):
        calls.append(args)
        headers = {"x-llmgw-request-id": "provider-request-id", "x-cost-rub": "2.50"}
        if isinstance(body, str):
            return httpx.Response(status, content=body.encode(), headers=headers)
        if isinstance(body, dict) and isinstance(body.get("error"), dict):
            body["error"]["message"] = body["error"].get("message", "") + " private-key"
        return httpx.Response(status, json=body, headers=headers)

    monkeypatch.setattr(messages_native, "_post_llmgw", reply)
    response = client.post("/v1/messages", json={
        "model": "claude-sonnet-5", "messages": [{"role": "user", "content": "synthetic"}],
    })
    assert response.status_code == status
    error = response.json()["error"]
    assert error["type"] == "api_error"
    details = error["details"]
    assert details["upstream_http_status"] == status
    assert details["provider_charge_ambiguous"] is (status == 429)
    assert details.get("provider_refusal_category") == category
    assert "private" not in response.text
    assert len(calls) == 1
    receipt = provider_calls.finish_call.await_args.kwargs
    assert receipt["status"] == ("ambiguous" if status == 429 else "failed")
    assert receipt["error_type"] == "HTTPStatusError"
    assert receipt["provider_request_id"] == "provider-request-id"
    assert receipt["provider_cost_rub"] == Decimal("2.50")
    assert billing.charge.await_count == 0


@pytest.mark.parametrize("status", [500, 502, 503])
def test_native_server_error_still_quarantines_receipt_without_diagnostic_replay(
    client, monkeypatch, status,
):
    monkeypatch.setattr(messages_native, "native_messages_route",
                        lambda: ("synthetic", "https://provider.test/v1"))
    calls = []

    def reply(*args):
        calls.append(args)
        return httpx.Response(status, json={"error": {
            "code": "insufficient_funds", "message": "private-key",
        }})

    monkeypatch.setattr(messages_native, "_post_llmgw", reply)
    response = client.post("/v1/messages", json={
        "model": "claude-sonnet-5", "messages": [{"role": "user", "content": "synthetic"}],
    })
    assert response.status_code == 503
    assert response.json()["error"]["type"] == "billing_unavailable"
    assert "provider_refusal_category" not in response.text and "private" not in response.text
    assert len(calls) == 1
    assert provider_calls.finish_call.await_args.kwargs["status"] == "ambiguous"
    assert billing.charge.await_count == 0


@pytest.mark.parametrize("content", ["command output", [{"type": "text", "text": "command output"}]])
def test_failed_tool_result_keeps_error_semantics_in_openai_compatible_content(content):
    result = messages_native._openai_messages({"messages": [
        {"role": "assistant", "content": [
            {"type": "tool_use", "id": "build-id", "name": "build", "input": {}},
        ]},
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "build-id", "content": content,
             "is_error": True, "cache_control": {"type": "ephemeral"}},
            {"type": "text", "text": "Fix this and continue."},
        ]},
    ]})
    assert result[1] == {
        "role": "tool", "tool_call_id": "build-id", "content": "TOOL_ERROR: command output",
        "cache_control": {"type": "ephemeral"},
    }
    assert result[2] == {"role": "user", "content": "Fix this and continue."}


@pytest.mark.parametrize("flag", [False, None, "true", 1])
def test_successful_or_unproven_tool_error_keeps_existing_openai_content(flag):
    result = messages_native._openai_messages({"messages": [
        {"role": "assistant", "content": [
            {"type": "tool_use", "id": "build-id", "name": "build", "input": {}},
        ]},
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "build-id", "content": "command output",
             "is_error": flag},
        ]},
    ]})
    assert result[1] == {"role": "tool", "tool_call_id": "build-id", "content": "command output"}


@pytest.mark.parametrize("status,error,kind,category", [
    (400, {"type": "invalid_request_error"}, "invalid_request_error", None),
    (400, {"code": "invalid_request_error"}, "invalid_request_error", None),
    (413, {"type": "request_too_large"}, "request_too_large", None),
    (422, {"type": "invalid_request_error"}, "invalid_request_error", None),
    (400, {"message": "invalid_request_error"}, "api_error", None),
    (400, {"type": "invalid_request_error-private"}, "api_error", None),
    (400, {"type": "invalid_request_error", "padding": "private" * 3000}, "api_error", None),
    (400, {"type": ["invalid_request_error"]}, "api_error", None),
    (400, {"type": "invalid_request_error", "code": "request_too_large"}, "api_error", None),
    (400, {"type": "invalid_request_error", "code": "insufficient_balance"}, "api_error", "funds"),
    (400, {"type": "authentication_error", "code": "invalid_request_error"}, "api_error", "access"),
    (429, {"type": "rate_limit_error", "code": "invalid_request_error"}, "api_error", "rate_limit"),
])
def test_native_invalid_request_preserves_only_allowlisted_safe_error_type(
    client, monkeypatch, status, error, kind, category,
):
    monkeypatch.setattr(messages_native, "native_messages_route",
                        lambda: ("synthetic", "https://provider.test/v1"))
    calls = []

    def reply(*args):
        calls.append(args)
        return httpx.Response(status, json={"type": "error", "error": {
            **error, "message": "synthetic-private-key https://private.test/path",
            "param": "private-field",
        }}, headers={"x-llmgw-request-id": "provider-request-id", "x-cost-rub": "2.50"})

    monkeypatch.setattr(messages_native, "_post_llmgw", reply)
    response = client.post("/v1/messages", json={
        "model": "claude-sonnet-5", "messages": [{"role": "user", "content": "synthetic"}],
    })
    assert response.status_code == status
    output = response.json()["error"]
    assert output["type"] == kind
    assert output["details"]["upstream_http_status"] == status
    # This fixture carries cost evidence and a conflicting 429 request code;
    # the latest closed retry policy must keep that outcome ambiguous.
    assert output["details"]["provider_charge_ambiguous"] is (status == 429)
    assert "retry_safe" not in output["details"] and "retry_policy" not in output["details"]
    assert output["details"].get("provider_refusal_category") == category
    assert "private" not in response.text
    assert len(calls) == 1
    receipt = provider_calls.finish_call.await_args.kwargs
    assert receipt["status"] == ("ambiguous" if status == 429 else "failed")
    assert receipt["error_type"] == "HTTPStatusError"
    assert receipt["provider_request_id"] == "provider-request-id"
    assert receipt["provider_cost_rub"] == Decimal("2.50")
    assert billing.charge.await_count == 0
