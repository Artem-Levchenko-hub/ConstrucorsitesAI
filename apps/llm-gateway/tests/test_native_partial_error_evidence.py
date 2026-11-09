"""A partial error body cannot erase accepted headers or authorize another POST."""

from decimal import Decimal
from unittest.mock import AsyncMock
from uuid import UUID

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


@pytest.mark.parametrize("status", [200, 402, 502])
@pytest.mark.parametrize("journal_ok", [False, True])
@pytest.mark.parametrize("kind", [httpx.ReadTimeout, httpx.ConnectError])
def test_partial_native_body_keeps_observed_status_without_identity_or_backup(
    client, monkeypatch, status, journal_ok, kind,
):
    monkeypatch.setattr(messages_native, "native_messages_route",
                        lambda: ("synthetic-key", "https://provider.test/v1"))
    posts, diagnostics = [], []

    class Partial(httpx.SyncByteStream):
        def __iter__(self):
            yield b'{"error":'
            raise kind("private body with synthetic-key")

    def provider(request):
        posts.append(request)
        return httpx.Response(status, stream=Partial(), headers={
            "x-llmgw-request-id": "known-partial-response", "x-cost-rub": "2.50",
            "x-private-header": "synthetic-key",
        })

    monkeypatch.setattr(httpx, "HTTPTransport", lambda **kwargs: httpx.MockTransport(provider))
    monkeypatch.setattr(messages_native.log, "warning",
                        lambda event, **fields: diagnostics.append((event, fields)))
    if not journal_ok:
        monkeypatch.setattr(provider_calls, "finish_call", AsyncMock(
            side_effect=RuntimeError("private journal with synthetic-key"),
        ))
    response = client.post("/v1/messages", json={
        "model": "claude-sonnet-5", "messages": [{"role": "user", "content": "synthetic"}],
    })
    assert response.status_code == 503 and len(posts) == 1
    error = response.json()["error"]
    assert error["type"] == "billing_unavailable"
    if status >= 400:
        assert error["details"]["upstream_http_status"] == status
        assert error["details"]["provider_charge_ambiguous"] is True
        assert error["details"]["retry_safe"] is False
    else:
        assert "upstream_http_status" not in error.get("details", {})
    receipt = provider_calls.finish_call.await_args
    assert receipt.kwargs["status"] == "ambiguous"
    assert receipt.kwargs["provider_request_id"] == "known-partial-response"
    assert receipt.kwargs["provider_cost_rub"] == Decimal("2.50")
    event = next(fields for event, fields in diagnostics if event == "native_messages.transport_error")
    assert event["provider_scope"] == "llmgw" and event["transport_stage"] == "body"
    assert event["upstream_http_status"] == status
    assert UUID(event["provider_call_id"]) == receipt.args[0]
    assert "synthetic-key" not in response.text + str(diagnostics)
    assert billing.charge.await_count == 0


def test_unknown_native_transport_status_is_not_invented(client, monkeypatch):
    monkeypatch.setattr(messages_native, "native_messages_route",
                        lambda: ("synthetic-key", "https://provider.test/v1"))
    def fail(*args, **kwargs):
        raise httpx.ReadTimeout("private transport")
    monkeypatch.setattr(messages_native, "_post_llmgw", fail)
    response = client.post("/v1/messages", json={
        "model": "claude-sonnet-5", "messages": [{"role": "user", "content": "synthetic"}],
    })
    assert response.status_code == 503
    assert "upstream_http_status" not in response.text and "retry_safe" not in response.text
    assert provider_calls.finish_call.await_count == 1
    assert provider_calls.finish_call.await_args.kwargs["status"] == "ambiguous"
