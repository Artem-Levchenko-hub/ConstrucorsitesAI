"""Settlement reconciliation is terminal through the native caller's existing gate."""

import httpx
import pytest

from yleum_api.services import agent_native


@pytest.mark.asyncio
async def test_reconciliation_required_never_retries_a_paid_provider_call(monkeypatch):
    calls = []

    def reply(request):
        calls.append(request)
        return httpx.Response(
            503,
            json={
                "error": {
                    "type": "billing_unavailable",
                    "code": "billing_reconciliation_required",
                    "message": "Billing reconciliation required",
                }
            },
        )

    async def no_sleep(_delay):
        pytest.fail("reconciliation must not retry a completed provider request")

    monkeypatch.setattr(agent_native.asyncio, "sleep", no_sleep)
    async with httpx.AsyncClient(transport=httpx.MockTransport(reply)) as client:
        with pytest.raises(RuntimeError, match="BILLING_UNAVAILABLE"):
            await agent_native._call_messages(client, "https://gateway.test/v1/messages", [], "s")
    assert len(calls) == 1
