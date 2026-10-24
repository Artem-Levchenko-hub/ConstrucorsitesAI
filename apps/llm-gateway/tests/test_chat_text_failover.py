from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient

from yleum_gateway.core.errors import BillingReconciliationRequiredError, UpstreamProviderError
from yleum_gateway.main import create_app
from yleum_gateway.providers import llmgw
from yleum_gateway.services import billing, provider_calls, text_failover


@pytest.fixture
def client(neutralize_lifespan, neutralize_side_effects, monkeypatch):
    monkeypatch.setenv("LLMGW_API_KEY", "synthetic-primary-key")
    monkeypatch.setenv("AITUNNEL_TEXT_FALLBACK_ENABLED", "true")
    monkeypatch.setenv("AITUNNEL_TEXT_API_KEY", "synthetic-backup-key")
    with TestClient(create_app()) as client:
        yield client


@pytest.mark.parametrize("status", [500,502,503,504])
def test_stream_primary_refusal_has_one_backup_before_any_output(client, monkeypatch,status):
    scopes = []
    async def stream(model, messages, *, receipt, provider_scope="llmgw", **kwargs):
        scopes.append(provider_scope)
        if provider_scope == "llmgw":
            refusal = httpx.Response(status, json={"error": {500:"Internal Server Error",502:"Bad Gateway",503:"Service Unavailable",504:"Gateway Timeout"}[status]}, request=httpx.Request("POST", text_failover.PRIMARY_URL))
            raise UpstreamProviderError("refused", details={"provider_charge_ambiguous": False,
                "_overload_retry_candidate": text_failover.overload_candidate(refusal, model)})
        receipt.update(model=model, id="ai-receipt", usage={"prompt_tokens": 10, "completion_tokens": 1, "cost_rub": 0.2}, complete=True, finish_reason="stop")
        yield "5", model
    monkeypatch.setattr(llmgw, "astream", stream)
    response = client.post("/v1/chat/completions", json={
        "model": "claude-sonnet-5.5", "stream": True, "user": str(uuid4()),
        "messages": [{"role": "user", "content": "2+3"}],
    })
    assert response.status_code == 200
    assert '"content": "5"' in response.text, response.text
    assert "[DONE]" in response.text
    assert scopes == ["llmgw", "aitunnel"]
    assert provider_calls.start_call.await_count == 2
    assert billing.charge.await_count == 1
    assert billing.charge.await_args.kwargs["provider_scope"] == "aitunnel"


def test_stream_partial_output_never_switches_provider(client, monkeypatch):
    scopes = []
    async def stream(model, messages, *, receipt, provider_scope="llmgw", **kwargs):
        scopes.append(provider_scope)
        receipt.update(id="partial-receipt", model=model)
        yield "partial", model
        raise UpstreamProviderError("broken", details={"provider_charge_ambiguous": True})
    monkeypatch.setattr(llmgw, "astream", stream)
    response = client.post("/v1/chat/completions", json={"model": "claude-sonnet-5.5", "stream": True, "messages": [{"role": "user", "content": "synthetic"}]})
    assert '"content": "partial"' in response.text
    assert "[DONE]" not in response.text
    assert scopes == ["llmgw"]


@pytest.mark.parametrize("primary,backup", [("overload", "ok"), ("overload", "failed"),
    ("overload", "wrong_model"), ("money", "ok"), ("unknown", "ok")])
@pytest.mark.parametrize("status", [500,502,503,504])
def test_nonstream_real_transport_fallback_and_fail_closed(client, monkeypatch, primary, backup,status):
    requests, events = [], []
    async def commit_receipt(*args, **kwargs):
        events.append("receipt_" + kwargs.get("status", "completed"))
    monkeypatch.setattr(provider_calls, "finish_call", AsyncMock(side_effect=commit_receipt))
    def reply(request):
        requests.append(request)
        events.append("post_primary" if len(requests) == 1 else "post_backup")
        if len(requests) == 1:
            if primary == "money":
                return httpx.Response(status, json={"error": {500:"Internal Server Error",502:"Bad Gateway",503:"Service Unavailable",504:"Gateway Timeout"}[status]}, headers={"x-llmgw-cost-rub": "0.3"})
            if primary == "unknown":
                return httpx.Response(status, text="unrecognized upstream failure")
            return httpx.Response(status, json={"error": {500:"Internal Server Error",502:"Bad Gateway",503:"Service Unavailable",504:"Gateway Timeout"}[status]})
        if backup == "failed":
            return httpx.Response(status, json={"error": "Backup unavailable"})
        return httpx.Response(200, json={
            "id": "backup-receipt", "model": "claude-sonnet-5" if backup == "wrong_model" else "claude-sonnet-5.5",
            "usage": {"prompt_tokens": 10, "completion_tokens": 2, "cost_rub": 0.2},
            "choices": [{"finish_reason": "stop", "message": {"content": "5"}}],
        })
    monkeypatch.setattr(httpx, "HTTPTransport", lambda **_: httpx.MockTransport(reply))
    response = client.post("/v1/chat/completions", json={
        "model": "claude-sonnet-5.5", "max_tokens": 128, "user": str(uuid4()),
        "messages": [{"role": "user", "content": "2+3"}],
    })
    succeeds = primary == "overload" and backup == "ok"
    assert (response.status_code == 200) is succeeds, response.text
    assert len(requests) == (2 if primary == "overload" else 1)
    assert provider_calls.start_call.await_count == len(requests)
    assert billing.charge.await_count == int(succeeds)
    if primary == "overload":
        assert str(requests[0].url) == text_failover.PRIMARY_URL
        assert str(requests[1].url) == "https://api.aitunnel.ru/v1/chat/completions"
        import json
        left, right = (json.loads(request.content) for request in requests)
        assert left["messages"] == right["messages"]
        assert right["model"] == "claude-sonnet-5.5"
        assert provider_calls.finish_call.await_args_list[0].kwargs["status"] == "failed"
        assert provider_calls.start_call.await_args_list[1].kwargs["provider_scope"] == "aitunnel"
        assert events.index("receipt_failed") < events.index("post_backup")
        assert provider_calls.finish_call.await_args_list[0].args[0] != provider_calls.finish_call.await_args_list[-1].args[0]
    if succeeds:
        assert response.json()["choices"][0]["message"]["content"] == "5"
        assert billing.charge.await_args.kwargs["provider_scope"] == "aitunnel"
    else:
        assert "llmgw_overload_v1" not in response.text
        assert "llmgw_unavailable_v1" not in response.text


def test_nonstream_failed_primary_receipt_commit_never_sends_backup(client, monkeypatch):
    calls = []
    def reply(request):
        calls.append(request)
        return httpx.Response(502, json={"error": "Bad gateway"})
    monkeypatch.setattr(httpx, "HTTPTransport", lambda **_: httpx.MockTransport(reply))
    monkeypatch.setattr(provider_calls, "finish_call", AsyncMock(side_effect=BillingReconciliationRequiredError("commit unknown")))
    response = client.post("/v1/chat/completions", json={
        "model": "claude-sonnet-5.5", "max_tokens": 128, "user": str(uuid4()),
        "messages": [{"role": "user", "content": "synthetic"}],
    })
    assert response.status_code == 503
    assert len(calls) == 1 and billing.charge.await_count == 0
    assert "llmgw_overload_v1" not in response.text
    assert "llmgw_unavailable_v1" not in response.text

@pytest.mark.parametrize("status", [500, 502, 503, 504])
def test_actual_sse_transport_falls_back_once_after_committed_refusal(client, monkeypatch, status):
    import json

    requests = []

    def reply(request):
        requests.append(request)
        if len(requests) == 1:
            return httpx.Response(status, json={"error": "temporarily unavailable"})
        event = {
            "id": "backup-receipt",
            "model": "claude-sonnet-5.5",
            "choices": [{"delta": {"content": "ok"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 1, "cost_rub": 0.2},
        }
        return httpx.Response(
            200,
            text="data: " + json.dumps(event) + "\n\ndata: [DONE]\n\n",
            headers={"content-type": "text/event-stream"},
        )

    monkeypatch.setattr(httpx, "HTTPTransport", lambda **kw: httpx.MockTransport(reply))
    response = client.post(
        "/v1/chat/completions",
        json={
            "model": "claude-sonnet-5.5",
            "max_tokens": 128,
            "stream": True,
            "user": str(uuid4()),
            "messages": [{"role": "user", "content": "synthetic"}],
        },
    )
    assert response.status_code == 200 and "[DONE]" in response.text
    assert '"content": "ok"' in response.text
    assert len(requests) == 2 and billing.charge.await_count == 1
    assert provider_calls.finish_call.await_args_list[0].kwargs["status"] == "failed"
    assert provider_calls.start_call.await_args_list[1].kwargs["provider_scope"] == "aitunnel"
