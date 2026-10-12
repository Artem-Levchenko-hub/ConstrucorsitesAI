"""Native error diagnostics keep provider text inside the gateway boundary."""

from decimal import Decimal

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
def test_native_refusal_returns_only_safe_category_and_keeps_failed_receipt(
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
    assert details["provider_charge_ambiguous"] is False
    assert details.get("provider_refusal_category") == category
    assert "private" not in response.text
    assert len(calls) == 1
    receipt = provider_calls.finish_call.await_args.kwargs
    assert receipt["status"] == "failed"
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
