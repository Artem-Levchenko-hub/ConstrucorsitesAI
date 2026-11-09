"""Expired local budgets never become an upstream outage or a paid request."""

from unittest.mock import AsyncMock
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from yleum_gateway.main import create_app
from yleum_gateway.routers import chat
from yleum_gateway.services import provider_calls


async def test_local_response_timeout_is_distinct_from_an_upstream_502(monkeypatch):
    from yleum_gateway.core.errors import ProviderResponseDeadlineError
    from yleum_gateway.providers import llmgw

    monkeypatch.setattr(llmgw, "_key_and_url", lambda _: ("unit-only-key", "https://unit.invalid"))
    upstream = AsyncMock(side_effect=TimeoutError("PRIVATE local budget detail"))
    monkeypatch.setattr(llmgw.native_stream, "collect_response", upstream)
    with pytest.raises(ProviderResponseDeadlineError) as caught:
        await llmgw.acompletion(model="claude-sonnet-5.5", messages=[], response_budget=7)
    assert upstream.await_count == 1
    assert caught.value.http_status == 504
    assert caught.value.details["provider_charge_ambiguous"] is True
    assert caught.value.details["retry_safe"] is False
    assert caught.value.details["provider_failure_kind"] == "response_deadline"
    assert "PRIVATE" not in caught.value.message


def test_local_response_deadline_keeps_ambiguous_journal_without_redispatch(
    monkeypatch, neutralize_lifespan, neutralize_side_effects,
):
    from yleum_gateway.core.errors import ProviderResponseDeadlineError

    upstream = AsyncMock(side_effect=ProviderResponseDeadlineError())
    monkeypatch.setattr(chat.llmgw, "acompletion", upstream)
    with TestClient(create_app()) as client:
        response = client.post("/v1/chat/completions", json={
            "model": "claude-sonnet-5.5", "messages": [{"role": "user", "content": "x"}],
        })
    assert upstream.await_count == 1
    chat.billing.charge.assert_not_awaited()
    assert provider_calls.finish_call.await_args.kwargs["status"] == "ambiguous"
    assert response.status_code == 504
    error = response.json()["detail"]["error"]
    assert error["code"] == "response_deadline_exceeded"
    assert error["details"]["provider_charge_ambiguous"] is True
    assert error["details"]["retry_safe"] is False


def test_local_deadline_before_dispatch_has_distinct_wire_contract(
    monkeypatch, neutralize_lifespan, neutralize_side_effects,
):
    upstream = AsyncMock(side_effect=AssertionError("No provider dispatch allowed"))
    monkeypatch.setattr(chat.llmgw, "acompletion", upstream)
    with TestClient(create_app()) as client:
        response = client.post("/v1/chat/completions", headers={
            "x-yleum-provider-timeout-seconds": "2.565367",
        }, json={"model": "claude-sonnet-5.5", "messages": [{"role": "user", "content": "x"}]})
    upstream.assert_not_awaited()
    chat.billing.charge.assert_not_awaited()
    receipt = provider_calls.finish_call.await_args.kwargs
    assert receipt["status"] == "failed"
    assert response.status_code == 504
    error = response.json()["detail"]["error"]
    assert error["code"] == "deadline_before_dispatch"
    details = error["details"]
    assert {key: details[key] for key in (
        "provider_charge_ambiguous", "provider_failure_kind", "retry_safe",
    )} == {
        "provider_charge_ambiguous": False,
        "provider_failure_kind": "deadline_before_dispatch", "retry_safe": False,
    }
    assert set(details) == {
        "provider_charge_ambiguous", "provider_failure_kind", "retry_safe",
        "provider_scope", "provider_call_id", "transport_stage",
    }
    assert details["provider_scope"] == "llmgw" and details["transport_stage"] == "unknown"
    assert UUID(details["provider_call_id"]) == provider_calls.finish_call.await_args.args[0]


async def test_stream_adapter_expired_budget_never_opens_transport():
    from yleum_gateway.core.errors import GatewayError
    from yleum_gateway.providers.llmgw import astream

    with pytest.raises(GatewayError) as caught:
        async for _ in astream(model="claude-sonnet-5.5", messages=[], timeout=0):
            pytest.fail("Expired stream cannot emit content")
    assert caught.value.code == "deadline_before_dispatch"
    assert caught.value.details["provider_charge_ambiguous"] is False


@pytest.mark.parametrize("journal_fails", [False, True])
def test_local_deadline_accounting_confirmation_requires_committed_receipt(
    monkeypatch, neutralize_lifespan, neutralize_side_effects, journal_fails,
):
    from yleum_gateway.core.errors import BillingReconciliationRequiredError
    from yleum_gateway.services import generation_response_capture

    observations = []
    async def finish(*args, **kwargs):
        if journal_fails:
            raise BillingReconciliationRequiredError("unconfirmed journal")
        assert kwargs["status"] == "failed"
        observations.append("receipt")
    monkeypatch.setattr(provider_calls, "finish_call", finish)
    monkeypatch.setattr(generation_response_capture, "confirm_response_accounting",
                        lambda: observations.append("confirmed"))
    with TestClient(create_app()) as client:
        response = client.post("/v1/chat/completions", headers={
            "x-yleum-provider-timeout-seconds": "2.5",
        }, json={"model": "claude-sonnet-5.5", "messages": [{"role": "user", "content": "x"}]})
    assert observations == ([] if journal_fails else ["receipt", "confirmed"])
    assert response.status_code == (503 if journal_fails else 504)
