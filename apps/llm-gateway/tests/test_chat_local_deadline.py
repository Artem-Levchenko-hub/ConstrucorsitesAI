"""Expired local budgets never become an upstream outage or a paid request."""

from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from yleum_gateway.main import create_app
from yleum_gateway.routers import chat
from yleum_gateway.services import provider_calls


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
    assert error["details"] == {
        "provider_charge_ambiguous": False,
        "provider_failure_kind": "deadline_before_dispatch", "retry_safe": False,
    }


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
