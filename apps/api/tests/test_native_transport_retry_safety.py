"""An uncertain native POST outcome must never create a second paid request."""

import json
from unittest.mock import AsyncMock

import httpx
import pytest

from yleum_api.services import agent_native
from yleum_api.services.generation_failure import failure_for_error

URL = "https://gateway.test/v1/messages"
SUCCESS = {"content": [], "stop_reason": "end_turn"}
PROOF = {
    "provider_failure_kind": "preconnect",
    "provider_charge_ambiguous": False,
    "retry_safe": True,
    "retry_policy": "llmgw_preconnect_v1",
}


async def call(monkeypatch, first, *, url=URL, raw_response=False):
    calls, sleeps = [], []

    def reply(request):
        calls.append(request)
        if len(calls) > 1:
            return httpx.Response(200, json=SUCCESS)
        if isinstance(first, type):
            raise first("private transport detail", request=request)
        return first

    async def sleep(delay):
        sleeps.append(delay)

    monkeypatch.setattr(agent_native.asyncio, "sleep", sleep)
    # MockTransport normalizes response history and request identity itself.
    # A direct client stub preserves those fields for the boundary checks.
    class RawClient:
        async def post(self, request_url, **kwargs):
            return reply(httpx.Request("POST", request_url))

    async with httpx.AsyncClient(transport=httpx.MockTransport(reply)) as client:
        try:
            result = await agent_native._call_messages(
                RawClient() if raw_response else client, url, [], "s",
            )
        except RuntimeError as exc:
            result = exc
    return result, calls, sleeps


@pytest.mark.parametrize("status", [500, 502, 503, 504, 529])
@pytest.mark.parametrize("body", [None, {"error": {"type": "api_error"}}])
async def test_unknown_http_outcome_is_terminal_after_one_post(monkeypatch, status, body):
    first = (httpx.Response(status, text="private proxy detail") if body is None
             else httpx.Response(status, json=body))
    result, calls, sleeps = await call(monkeypatch, first)
    assert isinstance(result, RuntimeError)
    assert str(result).startswith("provider_request_failed:")
    assert "private" not in str(result)
    assert len(calls) == 1 and sleeps == []


@pytest.mark.parametrize("kind", [httpx.WriteTimeout, httpx.RemoteProtocolError, httpx.ReadError])
async def test_unknown_transport_outcome_is_terminal_after_one_post(monkeypatch, kind):
    result, calls, sleeps = await call(monkeypatch, kind)
    assert isinstance(result, RuntimeError)
    assert str(result).startswith("provider_request_failed:")
    assert "private" not in str(result)
    assert len(calls) == 1 and sleeps == []


@pytest.mark.parametrize("kind", [httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout])
async def test_direct_preconnect_failure_can_retry(monkeypatch, kind):
    result, calls, sleeps = await call(monkeypatch, kind)
    assert result == SUCCESS and len(calls) == 2 and sleeps == [4.0]


@pytest.mark.parametrize("url", [URL, "https://gateway.test/v1/project-cell/messages"])
async def test_closed_gateway_preconnect_receipt_can_retry(monkeypatch, url):
    result, calls, sleeps = await call(monkeypatch, httpx.Response(502, json={
        "type": "error", "error": {"type": "api_error", "details": PROOF},
    }), url=url)
    assert result == SUCCESS and len(calls) == 2 and sleeps == [4.0]


@pytest.mark.parametrize("changes", [
    {"provider_charge_ambiguous": True}, {"provider_charge_ambiguous": 0},
    {"retry_safe": 1}, {"retry_safe": False}, {"retry_policy": "unknown"},
    {"provider_failure_kind": "transport"}, {"upstream_http_status": 502},
    {"usage": {}}, {"provider_cost_rub": "0"}, {"provider_request_id": "existing"},
])
async def test_contradictory_or_additional_preconnect_evidence_never_replays(monkeypatch, changes):
    result, calls, sleeps = await call(monkeypatch, httpx.Response(502, json={
        "error": {"type": "api_error", "details": {**PROOF, **changes}},
    }))
    assert isinstance(result, RuntimeError)
    assert len(calls) == 1 and sleeps == []


@pytest.mark.parametrize("extra", [{"usage": {}}, {"provider_cost_rub": "0"}])
async def test_preconnect_envelope_cannot_carry_usage_or_charge(monkeypatch, extra):
    result, calls, sleeps = await call(monkeypatch, httpx.Response(502, json={
        "error": {"type": "api_error", "details": PROOF}, **extra,
    }))
    assert isinstance(result, RuntimeError)
    assert len(calls) == 1 and sleeps == []


async def test_duplicate_preconnect_json_keys_never_authorize_replay(monkeypatch):
    raw = json.dumps({"error": {"type": "api_error", "details": PROOF}})
    raw = raw.replace('"retry_safe": true', '"retry_safe": false, "retry_safe": true')
    result, calls, sleeps = await call(monkeypatch, httpx.Response(502, content=raw))
    assert isinstance(result, RuntimeError)
    assert len(calls) == 1 and sleeps == []


async def test_gateway_preconnect_proof_is_not_accepted_on_other_routes(monkeypatch):
    result, calls, sleeps = await call(monkeypatch, httpx.Response(502, json={
        "error": {"type": "api_error", "details": PROOF},
    }), url="https://gateway.test/other")
    assert isinstance(result, RuntimeError)
    assert len(calls) == 1 and sleeps == []


async def test_redirected_preconnect_proof_never_authorizes_replay(monkeypatch):
    response = httpx.Response(502, json={
        "error": {"type": "api_error", "details": PROOF},
    }, history=[httpx.Response(307, headers={"location": URL})])
    result, calls, sleeps = await call(monkeypatch, response, raw_response=True)
    assert isinstance(result, RuntimeError)
    assert len(calls) == 1 and sleeps == []


async def test_response_from_different_gateway_url_cannot_authorize_replay(monkeypatch):
    response = httpx.Response(502, json={
        "error": {"type": "api_error", "details": PROOF},
    }, request=httpx.Request("POST", "https://other.test/v1/messages"))
    result, calls, sleeps = await call(monkeypatch, response, raw_response=True)
    assert isinstance(result, RuntimeError)
    assert len(calls) == 1 and sleeps == []


@pytest.mark.parametrize("marker", ["provider_request_failed", "provider_http_429"])
async def test_unknown_outcome_marker_survives_native_history_without_retry_permission(
    monkeypatch, marker,
):
    monkeypatch.setenv("DATABASE_URL", "postgresql://test:test@localhost:5432/test")
    monkeypatch.setenv("JWT_SECRET", "test-only-native-secret")
    agent_native.get_settings.cache_clear()
    monkeypatch.setattr(agent_native, "_call_messages", AsyncMock(
        side_effect=RuntimeError(f"{marker}: private transport detail"),
    ))

    async def execute(action):
        pytest.fail("unknown provider outcome must not execute a tool")

    result = await agent_native.run_native_build(system="s", task="t", execute=execute)
    assert not result.done and result.stop_reason == "provider_error"
    assert result.summary.startswith(marker + ":")
    assert "private" not in result.summary
    assert failure_for_error(result.summary).retryable is False
    agent_native.get_settings.cache_clear()


@pytest.mark.parametrize("category", [None, "rate_limit"])
async def test_native_429_marker_only_asserts_an_explicit_refusal_category(monkeypatch, category):
    result, calls, sleeps = await call(monkeypatch, httpx.Response(429, json={"error": {
        "type": "api_error", "message": "insufficient balance private transport detail",
        "details": {
            "upstream_http_status": 429, "provider_charge_ambiguous": True,
            **({"provider_refusal_category": category} if category else {}),
        },
    }}))
    marker = "provider_refusal_rate_limit" if category else "provider_http_429"
    assert isinstance(result, RuntimeError) and str(result).startswith(marker + ":")
    assert "private" not in str(result) and "balance" not in str(result)
    assert len(calls) == 1 and sleeps == []
