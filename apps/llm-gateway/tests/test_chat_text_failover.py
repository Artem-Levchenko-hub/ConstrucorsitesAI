import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient

from tests.test_inflight_budget_retry import wrapped
from tests.test_messages_native import _install_runner_auth, _runner_token
from yleum_gateway.core.errors import BillingReconciliationRequiredError, UpstreamProviderError
from yleum_gateway.main import create_app
from yleum_gateway.providers import llmgw
from yleum_gateway.routers import messages_native
from yleum_gateway.services import billing, provider_calls, text_failover


@pytest.fixture
def client(neutralize_lifespan, neutralize_side_effects, monkeypatch):
    monkeypatch.setenv("LLMGW_API_KEY", "synthetic-primary-key")
    monkeypatch.setenv("AITUNNEL_TEXT_FALLBACK_ENABLED", "true")
    monkeypatch.setenv("AITUNNEL_TEXT_API_KEY", "synthetic-backup-key")
    with TestClient(create_app()) as client:
        yield client


@pytest.mark.parametrize(
    ("route", "stream"),
    [
        ("/v1/chat/completions", False), ("/v1/chat/completions", True),
        ("/v1/messages", False), ("/v1/project-cell/messages", False),
    ],
)
@pytest.mark.parametrize(
    ("status", "error", "stays_on_backup"),
    [
        pytest.param(402, {"code": "insufficient_balance"}, True, id="payment"),
        pytest.param(401, {"code": "insufficient_funds"}, True, id="structured-funds"),
        pytest.param(401, {
            "type": "auth_error", "code": "401", "param": "balance",
            "message": "Недостаточно средств на балансе. Пополните баланс для продолжения.",
        }, True, id="llmgw-balance-401"),
        pytest.param(402, None, True, id="bare-payment"),
        pytest.param(401, {"code": "authentication_error"}, False, id="auth"),
        pytest.param(401, {
            "type": "auth_error", "code": "401", "param": "api_key", "message": "Invalid API key.",
        }, False, id="llmgw-auth-401"),
        pytest.param(402, {"code": "insufficient_balance", "type": "authentication_error"}, False,
                     id="contradictory-payment"),
        pytest.param(402, wrapped()["error"], False, id="inflight-without-valid-delay"),
    ],
)
def test_confirmed_balance_refusal_routes_following_steps_of_the_same_generation(
    client, monkeypatch, route, stream, status, error, stays_on_backup,
):
    calls = {}
    hosts = []
    user, project, run = uuid4(), uuid4(), uuid4()
    is_cell = route == "/v1/project-cell/messages"
    session, workspace = uuid4(), uuid4()
    if is_cell:
        _install_runner_auth(monkeypatch, redis_results=[True] * 3)
    if error is not None and error.get("code") == "402":
        error = {**error, "message": error["message"].replace('"120"', '"0"')}

    async def start_call(**fields):
        call_id = uuid4()
        calls[call_id] = {"provider_scope": "llmgw", "status": "started", **fields}
        return call_id

    async def finish_call(call_id, **fields):
        calls[call_id].update(fields)
        calls[call_id]["receipt_hash"] = "committed"

    async def balance_refused(_query, run_id, project_id, user_id, model, refusal, cell_route=None):
        return any(
            call["run_id"] == run_id
            and call["project_id"] == project_id
            and call["user_id"] == user_id
            and (user_id is not None or call["route"] == cell_route)
            and call["model"] == model
            and call["provider_scope"] == "llmgw"
            and call["status"] == "failed"
            and call.get("error_type") == refusal
            and call.get("receipt_hash")
            for call in calls.values()
        )

    @asynccontextmanager
    async def acquire():
        yield SimpleNamespace(fetchval=balance_refused)

    monkeypatch.setattr(text_failover, "get_pool", lambda: SimpleNamespace(acquire=acquire))
    monkeypatch.setattr(provider_calls, "start_call", start_call)
    monkeypatch.setattr(provider_calls, "finish_call", finish_call)

    def upstream(request):
        hosts.append(request.url.host)
        if request.url.host == "api.llmgw.ru" and b"new generation" not in request.content:
            return httpx.Response(status, json={"error": error} if error is not None else None)
        identity = {"id": str(uuid4()), "model": "claude-sonnet-5.5"}
        usage = {"prompt_tokens": 10, "completion_tokens": 2, "cost_rub": 0.2}
        if json.loads(request.content).get("stream"):
            data = [
                {**identity, "choices": [{"finish_reason": None, "delta": {"content": "OK"}}]},
                {**identity, "choices": [{"finish_reason": "stop", "delta": {}}], "usage": usage},
            ]
            content = "".join(f"data: {json.dumps(event)}\n\n" for event in data) + "data: [DONE]\n\n"
            return httpx.Response(200, content=content, headers={"content-type": "text/event-stream"})
        return httpx.Response(200, json={
            **identity, "usage": usage,
            "choices": [{"finish_reason": "stop", "message": {"content": "OK"}}],
        })

    def native_post(url, payload, _headers, **_options):
        request = httpx.Request("POST", url, json=payload)
        response = upstream(request)
        response.request = request
        return response

    monkeypatch.setattr(httpx, "HTTPTransport", lambda **_: httpx.MockTransport(upstream))
    monkeypatch.setattr(httpx, "AsyncHTTPTransport", lambda **_: httpx.MockTransport(upstream))
    monkeypatch.setattr(messages_native, "_post_llmgw", native_post)

    def send(run_id, content):
        metadata = {"project_id": str(project), "run_id": str(run_id)}
        headers = {}
        if is_cell:
            message_id = str(uuid4())
            metadata.update(session_id=str(session), workspace_id=str(workspace),
                            fencing_epoch=4, cancel_epoch=0, message_id=message_id)
            token = _runner_token(overrides={**metadata, "jti": message_id})
            headers["Authorization"] = f"Bearer {token}"
        return client.post(route, headers=headers, json={
            "model": "claude-sonnet-5.5", "max_tokens": 128, "user": str(user), "stream": stream,
            "metadata": metadata,
            "messages": [{"role": "user", "content": content}],
        })

    for run_id, content in [(run, "first step"), (run, "next step"), (uuid4(), "new generation")]:
        response = send(run_id, content)
        assert response.status_code == 200, response.text
        if stream:
            assert "[DONE]" in response.text, response.text

    next_hosts = ["api.aitunnel.ru"] if stays_on_backup else ["api.llmgw.ru", "api.aitunnel.ru"]
    assert hosts == ["api.llmgw.ru", "api.aitunnel.ru", *next_hosts, "api.llmgw.ru"]
    assert billing.charge.await_count == (0 if is_cell else 3)


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


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("first_is_uncertain", [False, True])
def test_sticky_chat_backup_switch_requires_confirmed_preconnect(
    client, monkeypatch, stream, first_is_uncertain,
):
    scopes = []
    use = {"prompt_tokens": 10, "completion_tokens": 1, "cost_rub": 0.2}

    def before_completion(provider_scope):
        scopes.append(provider_scope)
        if provider_scope == "aitunnel":
            raise UpstreamProviderError("synthetic reserve fault", details={
                "provider_charge_ambiguous": first_is_uncertain,
                "provider_failure_kind": "transport" if first_is_uncertain else "preconnect",
                "transport_stage": "awaiting_headers" if first_is_uncertain else "connect",
            })
        assert provider_calls.finish_call.await_args.kwargs["status"] == "failed"

    async def complete(*, provider_scope="llmgw", **kwargs):
        before_completion(provider_scope)
        return {
            "id": "primary-receipt", "model": "claude-sonnet-5.5", "usage": use,
            "choices": [{"finish_reason": "stop", "message": {"content": "resumed"}}],
        }

    async def stream_source(model, messages, *, receipt, provider_scope="llmgw", **kwargs):
        before_completion(provider_scope)
        receipt.update(id="primary-receipt", model=model, usage=use, complete=True, finish_reason="stop")
        yield "resumed", model

    monkeypatch.setattr(text_failover, "select_scope", AsyncMock(return_value="aitunnel"))
    monkeypatch.setattr(llmgw, "acompletion", complete)
    monkeypatch.setattr(llmgw, "astream", stream_source)
    response = client.post("/v1/chat/completions", json={
        "model": "claude-sonnet-5.5", "user": str(uuid4()), "stream": stream,
        "messages": [{"role": "user", "content": "synthetic"}],
    })

    if first_is_uncertain:
        assert scopes == ["aitunnel"]
        assert billing.charge.await_count == 0
        assert "resumed" not in response.text
        assert provider_calls.finish_call.await_args.kwargs["status"] == "ambiguous"
    else:
        assert scopes == ["aitunnel", "llmgw"]
        assert billing.charge.await_count == 1
        assert response.status_code == 200
        assert "resumed" in response.text


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
        event = {
            "id": "backup-receipt", "model": "claude-sonnet-5" if backup == "wrong_model" else "claude-sonnet-5.5",
            "usage": {"prompt_tokens": 10, "completion_tokens": 2, "cost_rub": 0.2},
            "choices": [{"finish_reason": "stop", "delta": {"content": "5"}}],
        }
        return httpx.Response(200, text=f"data: {json.dumps(event)}\n\ndata: [DONE]\n\n")
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
        left, right = (json.loads(request.content) for request in requests)
        assert left["messages"] == right["messages"]
        assert right["model"] == "claude-sonnet-5.5"
        assert left["stream"] is False and right["stream"] is True
        assert left["max_tokens"] == right["max_tokens"] == 128
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
            "choices": [{"finish_reason": "stop", "delta": {"content": "ok"}}],
        }
        return httpx.Response(200, text=f"data: {json.dumps(event)}\n\ndata: [DONE]\n\n")

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
