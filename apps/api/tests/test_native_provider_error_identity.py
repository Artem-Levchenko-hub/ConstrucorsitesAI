"""The old three-field status contract and the closed routing extension both stop replay."""

from uuid import uuid4

import httpx
import pytest

from yleum_api.services import agent_native
from yleum_api.services.generation.recovery_trace import safe_recovery_trace

from .test_agent_native_provider_diagnostics import URL, server_error_body


async def test_native_scoped_partial_error_keeps_identity_without_second_post(monkeypatch):
    call = str(uuid4())
    body = server_error_body()
    body["error"]["details"].update(
        provider_scope="aitunnel", transport_stage="body", provider_call_id=call,
    )
    requests = []
    def reply(request):
        requests.append(request)
        return httpx.Response(503, json=body)
    async def no_sleep(delay):
        pytest.fail("an ambiguous partial response must not retry")
    monkeypatch.setattr(agent_native.asyncio, "sleep", no_sleep)
    async with httpx.AsyncClient(transport=httpx.MockTransport(reply)) as client:
        with pytest.raises(agent_native._NativeProviderServerError) as raised:
            await agent_native._call_messages(client, URL, [], "synthetic")
    assert len(requests) == 1
    trace = safe_recovery_trace(raised.value.diagnostics())
    assert trace["provider_scope"] == "aitunnel" and trace["transport_stage"] == "body"
    assert trace["provider_call_id"] == call and trace["upstream_http_status"] == 502
    assert trace["retry_safe"] is False and trace["will_retry"] is False
    assert "private" not in str(trace) + str(raised.value)


@pytest.mark.parametrize("field,value", [
    ("provider_scope", "unknown"), ("transport_stage", "private-text"),
    ("provider_call_id", "not-a-uuid"), ("provider_call_id", str(uuid4()).upper()),
    ("provider_call_id", True), ("unapproved", "anything"),
])
def test_extended_native_contract_rejects_malformed_or_unknown_context(field, value):
    body = server_error_body()
    body["error"]["details"][field] = value
    response = httpx.Response(503, json=body, request=httpx.Request("POST", URL))
    assert agent_native._gateway_ambiguous_server_status(response, URL) is None


async def test_partial_402_is_only_diagnostic_and_keeps_generic_unresolved_outcome(monkeypatch):
    body = server_error_body(402)
    call = str(uuid4())
    body["error"]["details"].update(
        provider_scope="aitunnel", transport_stage="body", provider_call_id=call,
    )
    requests = []
    def reply(request):
        requests.append(request)
        return httpx.Response(503, json=body)
    async def no_sleep(delay):
        pytest.fail("an incomplete 402 is not a safe refusal")
    monkeypatch.setattr(agent_native.asyncio, "sleep", no_sleep)
    async with httpx.AsyncClient(transport=httpx.MockTransport(reply)) as client:
        with pytest.raises(agent_native._NativeProviderBillingError) as raised:
            await agent_native._call_messages(client, URL, [], "synthetic")
    assert len(requests) == 1
    assert str(raised.value).startswith("BILLING_UNAVAILABLE:")
    assert not str(raised.value).startswith("provider_http_402:")
    trace = safe_recovery_trace(raised.value.diagnostics())
    assert trace["upstream_http_status"] == 402 and trace["provider_scope"] == "aitunnel"
    assert trace["provider_call_id"] == call and trace["provider_charge_ambiguous"] is True
    assert trace["retry_safe"] is False and trace["will_retry"] is False
    assert "provider_refusal_category" not in trace
