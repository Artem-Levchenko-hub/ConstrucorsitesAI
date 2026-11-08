import json
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
                "_overload_retry_candidate": text_failover.primary_refusal_candidate(refusal, model)})
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
    monkeypatch.setattr(httpx, "AsyncHTTPTransport", lambda **_: httpx.MockTransport(reply))
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
    monkeypatch.setattr(httpx, "AsyncHTTPTransport", lambda **kw: httpx.MockTransport(reply))
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


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("failure", [httpx.ConnectTimeout, httpx.ReadTimeout])
def test_chat_reserves_backup_only_for_confirmed_preconnect(client, monkeypatch, stream, failure):
    hosts = []

    def reply(request):
        hosts.append(request.url.host)
        if request.url.host == "api.llmgw.ru":
            raise failure("synthetic transport failure", request=request)
        assert request.url.host == "api.aitunnel.ru"
        assert provider_calls.finish_call.await_args.kwargs["status"] == "failed"
        event = {
            "id": "backup-receipt",
            "model": "claude-sonnet-5.5",
            "usage": {"prompt_tokens": 10, "completion_tokens": 1, "cost_rub": 0.2},
            "choices": [{"finish_reason": "stop", "delta" if stream else "message": {"content": "ok"}}],
        }
        if stream:
            return httpx.Response(200, text=f"data: {json.dumps(event)}\n\ndata: [DONE]\n\n")
        return httpx.Response(200, json=event)

    monkeypatch.setattr(httpx, "HTTPTransport", lambda **_: httpx.MockTransport(reply))
    monkeypatch.setattr(httpx, "AsyncHTTPTransport", lambda **_: httpx.MockTransport(reply))
    response = client.post("/v1/chat/completions", json={
        "model": "claude-sonnet-5.5",
        "max_tokens": 128,
        "stream": stream,
        "user": str(uuid4()),
        "messages": [{"role": "user", "content": "synthetic"}],
    })

    if failure is httpx.ConnectTimeout:
        assert response.status_code == 200 and '"content":' in response.text
        assert hosts[-1] == "api.aitunnel.ru" and hosts.count("api.aitunnel.ru") == 1
        assert billing.charge.await_count == 1
        assert billing.charge.await_args.kwargs["provider_scope"] == "aitunnel"
    else:
        assert hosts == ["api.llmgw.ru"]
        assert provider_calls.finish_call.await_args.kwargs["status"] == "ambiguous"
        assert billing.charge.await_count == 0


@pytest.mark.parametrize("stream", [False, True])
def test_chat_does_not_dispatch_backup_after_deadline(client, monkeypatch, stream):
    hosts = []

    def reply(request):
        hosts.append(request.url.host)
        raise httpx.ConnectTimeout("synthetic preconnect failure", request=request)

    monkeypatch.setattr(httpx, "HTTPTransport", lambda **_: httpx.MockTransport(reply))
    response = client.post(
        "/v1/chat/completions",
        headers={"x-yleum-provider-timeout-seconds": "1"},
        json={
            "model": "claude-sonnet-5.5",
            "max_tokens": 128,
            "stream": stream,
            "messages": [{"role": "user", "content": "synthetic"}],
        },
    )
    assert "deadline_before_dispatch" in response.text
    assert "api.aitunnel.ru" not in hosts
    assert billing.charge.await_count == 0


@pytest.mark.parametrize("failed_step", ["primary_receipt", "backup_admission"])
def test_stream_preconnect_accounting_error_is_returned_without_backup(client, monkeypatch, failed_step):
    scopes = []

    async def stream(model, messages, *, provider_scope="llmgw", **kwargs):
        scopes.append(provider_scope)
        if provider_scope == "llmgw":
            raise UpstreamProviderError("preconnect", details={
                "provider_failure_kind": "preconnect", "provider_charge_ambiguous": False,
            })
        yield "unexpected", model

    monkeypatch.setattr(llmgw, "astream", stream)
    error = BillingReconciliationRequiredError("accounting commit unconfirmed")
    if failed_step == "primary_receipt":
        monkeypatch.setattr(provider_calls, "finish_call", AsyncMock(side_effect=error))
    else:
        monkeypatch.setattr(provider_calls, "start_call", AsyncMock(side_effect=[uuid4(), error]))
    response = client.post("/v1/chat/completions", json={
        "model": "claude-sonnet-5.5",
        "stream": True,
        "messages": [{"role": "user", "content": "synthetic"}],
    })
    assert response.status_code == 200
    assert "billing_reconciliation_required" in response.text
    assert "[DONE]" not in response.text
    assert scopes == ["llmgw"]
    assert billing.charge.await_count == 0
