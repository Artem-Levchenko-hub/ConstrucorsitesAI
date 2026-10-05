"""Real HTTP adapter → public chat route receipt and terminal-event contracts."""

from __future__ import annotations

import asyncio
import json
from decimal import Decimal
from uuid import uuid4

import httpx
import pytest
from anyio import CancelScope
from fastapi.testclient import TestClient

from yleum_gateway.core.config import reset_settings_cache
from yleum_gateway.main import create_app
from yleum_gateway.routers import chat

MODEL = "gemini-3.1-pro-preview-customtools"
USAGE = {
    "prompt_tokens": 1000, "completion_tokens": 100, "total_tokens": 1100,
    "prompt_tokens_details": {"cached_tokens": 600, "cache_creation_tokens": 200},
    "completion_tokens_details": {"reasoning_tokens": 80}, "cost_usd": "0.0123",
}


@pytest.fixture
def client(neutralize_lifespan, neutralize_side_effects, monkeypatch):
    monkeypatch.setenv("LLMGW_API_KEY", "test-only-provider-key")
    reset_settings_cache()
    with TestClient(create_app()) as client:
        yield client


def provider(monkeypatch, *, streaming, cost, usage=None):
    usage = USAGE if usage is None else usage
    common = {"id": "body-request-id", "model": "anthropic/claude-sonnet-5"}
    headers = {"x-llmgw-request-id": "provider-request-id"}
    if cost is not None:
        headers["x-llmgw-cost-rub"] = cost

    def reply(request):
        if streaming:
            chunks = [
                {**common, "choices": [{"delta": {"content": "answer"}}]},
                {**common, "choices": [], "usage": usage},
            ]
            content = "".join(f"data: {json.dumps(c)}\n\n" for c in chunks)
            return httpx.Response(200, headers=headers, content=content + "data: [DONE]\n\n")
        return httpx.Response(200, headers=headers, json={
            **common, "choices": [{"message": {"content": "answer"}}], "usage": usage,
        })

    monkeypatch.setattr(httpx, "HTTPTransport", lambda *a, **kw: httpx.MockTransport(reply))


def post(client, streaming):
    return client.post("/v1/chat/completions", json={
        "model": MODEL, "messages": [{"role": "user", "content": "hello"}],
        "user": str(uuid4()), "stream": streaming,
    })


def events(response):
    return [line[6:] for line in response.text.splitlines() if line.startswith("data: ")]


@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("cost,expected,basis", [
    ("0", "0", "provider_reported_rub"),
    ("1.2345", "1.2345", "provider_reported_rub"),
    (None, "0.3262", "token_tariff"),
    ("NaN", "0.3262", "token_tariff"),
    ("-1", "0.3262", "token_tariff"),
])
def test_adapter_receipt_controls_wallet_and_public_usage(
    client, monkeypatch, streaming, cost, expected, basis,
):
    provider(monkeypatch, streaming=streaming, cost=cost)
    result = post(client, streaming)
    assert result.status_code == 200, result.text
    if streaming:
        payloads = events(result)
        assert payloads[-1] == "[DONE]"
        body = json.loads(payloads[-2])
    else:
        body = result.json()
    assert body["model"] == "claude-sonnet-5"
    assert body["metadata"]["cost_rub"] == expected
    assert body["metadata"]["provider_request_id"] == "provider-request-id"
    assert body["usage"]["completion_tokens_details"]["reasoning_tokens"] == 80
    assert body["usage"]["prompt_tokens"] == 1000
    assert body["usage"]["completion_tokens"] == 100
    billed = chat.billing.charge.await_args.kwargs
    assert billed["model_id"] == "claude-sonnet-5"
    assert billed["cost_rub"] == Decimal(expected)
    assert billed["provider_request_id"] == "provider-request-id"
    assert billed["provider_cost_usd"] == Decimal("0.0123")
    assert billed["cache_read_tokens"] == 600
    assert billed["cache_write_tokens"] == 200
    assert billed["cost_provenance"]["basis"] == basis
    assert billed["cost_provenance"]["provider_cost_usd"] == "0.0123"
    assert billed["cost_provenance"]["calculated_cost_rub"] == "0.3262"


def test_stream_billing_failure_has_no_success_terminal(client, monkeypatch):
    provider(monkeypatch, streaming=True, cost="1.2345")
    chat.billing.charge.side_effect = RuntimeError("database unavailable")
    result = post(client, True)
    payloads = events(result)
    assert "[DONE]" not in payloads
    body = json.loads(payloads[-1])
    assert body["error"]["code"] == "billing_reconciliation_required"
    assert not any(json.loads(p).get("usage") for p in payloads)
    assert chat.billing.charge.await_count == 1


@pytest.mark.parametrize("streaming", [False, True])
def test_reported_zero_tokens_are_not_replaced_by_local_counts(client, monkeypatch, streaming):
    provider(monkeypatch, streaming=streaming, cost="0", usage={
        "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0,
    })
    result = post(client, streaming)
    body = json.loads(events(result)[-2]) if streaming else result.json()
    assert body["usage"]["prompt_tokens"] == 0
    assert body["usage"]["completion_tokens"] == 0
    assert body["metadata"]["cost_rub"] == "0"
    assert chat.billing.charge.await_args.kwargs["tokens_out"] == 0


@pytest.mark.parametrize("streaming", [False, True])
def test_ambiguous_accepted_post_is_not_retried(client, monkeypatch, streaming):
    calls = []

    def timeout(request):
        calls.append(request)
        raise httpx.ReadTimeout("response missing", request=request)

    monkeypatch.setattr(httpx, "HTTPTransport", lambda *a, **kw: httpx.MockTransport(timeout))
    result = post(client, streaming)
    assert len(calls) == 1
    error = json.loads(events(result)[-1])["error"] if streaming else result.json()["detail"]["error"]
    assert error["details"]["provider_charge_ambiguous"] is True
    assert chat.billing.charge.await_count == 0


@pytest.mark.parametrize("streaming", [False, True])
def test_missing_usage_is_explicitly_estimated(client, monkeypatch, streaming):
    provider(monkeypatch, streaming=streaming, cost=None, usage={})
    result = post(client, streaming)
    body = json.loads(events(result)[-2]) if streaming else result.json()
    assert body["metadata"]["cost_provenance"]["basis"] == "local_token_estimate"
    assert chat.billing.charge.await_args.kwargs["cost_provenance"]["basis"] == "local_token_estimate"


def test_synthetic_response_id_is_not_recorded_as_provider_receipt(client, monkeypatch):
    def reply(request):
        return httpx.Response(200, json={
            "choices": [{"message": {"content": "answer"}}], "usage": USAGE,
        })

    monkeypatch.setattr(httpx, "HTTPTransport", lambda *a, **kw: httpx.MockTransport(reply))
    result = post(client, False)
    assert result.status_code == 200
    assert result.json()["id"]
    assert chat.billing.charge.await_args.kwargs["provider_request_id"] is None


def test_truncated_stream_is_ambiguous_and_never_successful(client, monkeypatch):
    def reply(request):
        return httpx.Response(200, headers={"x-llmgw-request-id": "incomplete-id"},
                              content='data: {"choices":[{"delta":{"content":"partial"}}]}\n\n')

    monkeypatch.setattr(httpx, "HTTPTransport", lambda *a, **kw: httpx.MockTransport(reply))
    result = post(client, True)
    payloads = events(result)
    assert "[DONE]" not in payloads
    error = json.loads(payloads[-1])["error"]
    assert error["details"]["provider_charge_ambiguous"] is True
    assert error["details"]["provider_request_id"] == "incomplete-id"
    billed = chat.billing.charge.await_args.kwargs
    assert billed["provider_request_id"] == "incomplete-id"
    assert billed["cost_provenance"]["basis"] == "local_token_estimate"


async def test_stream_settles_before_yielding_final_usage(monkeypatch, neutralize_side_effects):
    from yleum_gateway.services.streaming import stream_completion

    monkeypatch.setenv("LLMGW_API_KEY", "test-only-provider-key")
    reset_settings_cache()
    provider(monkeypatch, streaming=True, cost="0")
    stream = stream_completion(request=None, model=MODEL,
                               messages=[{"role": "user", "content": "hi"}],
                               user_id=uuid4(), project_id=None, message_id=None,
                               temperature=None, max_tokens=None)
    first = json.loads(await anext(stream))
    assert first["choices"][0]["delta"]["content"] == "answer"
    assert chat.billing.charge.await_count == 0
    final = json.loads(await anext(stream))
    assert final["usage"]["completion_tokens"] == 100
    assert chat.billing.charge.await_count == 1
    assert await anext(stream) == "[DONE]"
    await stream.aclose()
    assert chat.billing.charge.await_count == 1


def test_stream_bills_reported_reasoning_even_without_visible_text(client, monkeypatch):
    def reply(request):
        chunk = {"id": "reasoning-only", "model": MODEL, "choices": [], "usage": USAGE}
        return httpx.Response(200, headers={"x-llmgw-cost-rub": "0.75"},
                              content=f"data: {json.dumps(chunk)}\n\ndata: [DONE]\n\n")

    monkeypatch.setattr(httpx, "HTTPTransport", lambda *a, **kw: httpx.MockTransport(reply))
    result = post(client, True)
    payloads = events(result)
    assert payloads[-1] == "[DONE]"
    assert json.loads(payloads[0])["usage"]["completion_tokens_details"]["reasoning_tokens"] == 80
    billed = chat.billing.charge.await_args.kwargs
    assert billed["tokens_out"] == 100
    assert billed["cost_rub"] == Decimal("0.75")
    assert billed["provider_request_id"] == "reasoning-only"


@pytest.mark.parametrize("status", [200, 500])
def test_upstream_failure_does_not_echo_response_body(client, monkeypatch, capsys, status):
    marker = "private-prompt-in-upstream-error"

    def reply(request):
        return httpx.Response(status, json={"error": marker})

    monkeypatch.setattr(httpx, "HTTPTransport", lambda *a, **kw: httpx.MockTransport(reply))
    result = post(client, False)
    assert result.status_code == 502
    assert marker not in result.text
    assert marker not in capsys.readouterr().out


async def test_cancel_during_price_lookup_still_settles_once(monkeypatch, neutralize_side_effects):
    from yleum_gateway.services import streaming

    monkeypatch.setenv("LLMGW_API_KEY", "test-only-provider-key")
    reset_settings_cache()
    provider(monkeypatch, streaming=True, cost="0")
    original = streaming.resolve_request_cost
    lookup_started = asyncio.Event()
    release_lookup = asyncio.Event()
    lookup_calls = 0

    async def slow_first_lookup(*args, **kwargs):
        nonlocal lookup_calls
        lookup_calls += 1
        lookup_started.set()
        await release_lookup.wait()
        return await original(*args, **kwargs)

    monkeypatch.setattr(streaming, "resolve_request_cost", slow_first_lookup)
    stream = streaming.stream_completion(request=None, model=MODEL,
                                         messages=[{"role": "user", "content": "hi"}],
                                         user_id=uuid4(), project_id=None, message_id=None,
                                         temperature=None, max_tokens=None)
    await anext(stream)
    final = asyncio.create_task(anext(stream))
    await lookup_started.wait()
    final.cancel()
    await asyncio.sleep(0)
    release_lookup.set()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(final, 1)
    assert lookup_calls == 1
    assert chat.billing.charge.await_count == 1
    assert chat.billing.charge.await_args.kwargs["cost_rub"] == Decimal("0")


def test_local_cache_hit_does_not_reuse_previous_provider_charge(client):
    chat.cache.get.return_value = {
        "id": "cached-response", "model": MODEL,
        "choices": [{"message": {"content": "cached answer"}}],
        "usage": {**USAGE, "cost_rub": "3", "cost": "0.02"},
        "metadata": {"cost_rub": "3", "cost_usd": "0.02",
                     "provider_request_id": "old-provider-request",
                     "cost_provenance": {"basis": "provider_reported_rub"}},
    }
    result = post(client, False)
    assert result.status_code == 200
    body = result.json()
    assert body["metadata"]["cache_hit"] is True
    assert body["metadata"]["cost_rub"] == "0"
    assert "cost_provenance" not in body["metadata"]
    assert "provider_request_id" not in body["metadata"]
    assert "cost_usd" not in body["metadata"]
    assert not {"cost", "cost_rub", "cost_usd"}.intersection(body["usage"])
    assert chat.billing.charge.await_count == 0


@pytest.mark.parametrize("cancellation", ["task", "sse_scope"])
async def test_disconnect_during_charge_waits_for_same_transaction(
    monkeypatch, neutralize_side_effects, cancellation,
):
    from yleum_gateway.services import streaming

    monkeypatch.setenv("LLMGW_API_KEY", "test-only-provider-key")
    reset_settings_cache()
    provider(monkeypatch, streaming=True, cost="0")
    charge_started = asyncio.Event()
    release_charge = asyncio.Event()
    committed = []
    aborted = []

    async def charge(**kwargs):
        charge_started.set()
        try:
            await release_charge.wait()
        except asyncio.CancelledError:
            aborted.append(True)
            raise
        committed.append(kwargs)

    chat.billing.charge.side_effect = charge
    stream = streaming.stream_completion(request=None, model=MODEL,
                                         messages=[{"role": "user", "content": "hi"}],
                                         user_id=uuid4(), project_id=None, message_id=None,
                                         temperature=None, max_tokens=None)
    await anext(stream)
    scopes = []

    async def consume_final():
        if cancellation == "sse_scope":
            with CancelScope() as scope:
                scopes.append(scope)
                await anext(stream)
        else:
            await anext(stream)

    final = asyncio.create_task(consume_final())
    await charge_started.wait()
    if cancellation == "sse_scope":
        scopes[0].cancel()
    else:
        final.cancel()
    await asyncio.sleep(0)
    release_charge.set()
    if cancellation == "sse_scope":
        await asyncio.wait_for(final, 1)
    else:
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(final, 1)
    assert not aborted
    assert len(committed) == 1
    assert committed[0]["cost_rub"] == Decimal("0")
    assert chat.billing.charge.await_count == 1


@pytest.mark.parametrize("with_usage", [False, True])
def test_missing_done_preserves_received_provider_receipt(client, monkeypatch, with_usage):
    def reply(request):
        chunk = {"id": "receipt-before-eof", "model": MODEL,
                 "choices": [{"delta": {"content": "answer"}}]}
        if with_usage:
            chunk["usage"] = USAGE
        return httpx.Response(200, headers={"x-llmgw-cost-rub": "0"},
                              content=f"data: {json.dumps(chunk)}\n\n")

    monkeypatch.setattr(httpx, "HTTPTransport", lambda *a, **kw: httpx.MockTransport(reply))
    result = post(client, True)
    assert "[DONE]" not in events(result)
    assert json.loads(events(result)[-1])["error"]["details"]["provider_charge_ambiguous"] is True
    billed = chat.billing.charge.await_args.kwargs
    assert billed["cost_rub"] == Decimal("0")
    assert billed["cost_provenance"]["basis"] == "provider_reported_rub"
    assert billed["provider_request_id"] == "receipt-before-eof"
    if with_usage:
        assert billed["tokens_in"] == 1000
        assert billed["tokens_out"] == 100
        assert billed["cache_read_tokens"] == 600


@pytest.mark.parametrize("streaming", [False, True])
def test_unknown_actual_model_cannot_be_billed_as_requested_model(client, monkeypatch, streaming):
    def reply(request):
        body = {"model": "vendor/unsupported-model", "id": "unknown-model-receipt", "usage": USAGE,
                "choices": [{"message": {"content": "answer"}, "delta": {"content": "answer"}}]}
        if streaming:
            return httpx.Response(200, content=f"data: {json.dumps(body)}\n\ndata: [DONE]\n\n")
        return httpx.Response(200, json=body)

    monkeypatch.setattr(httpx, "HTTPTransport", lambda *a, **kw: httpx.MockTransport(reply))
    result = post(client, streaming)
    if streaming:
        assert "[DONE]" not in events(result)
        error = json.loads(events(result)[-1])["error"]
    else:
        assert result.status_code == 502
        error = result.json()["detail"]["error"]
    assert error["details"]["provider_charge_ambiguous"] is True
    assert error["details"]["provider_request_id"] == "unknown-model-receipt"
    assert chat.billing.charge.await_count == 0
