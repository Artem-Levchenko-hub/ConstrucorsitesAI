import json
from http import HTTPStatus
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient

from yleum_gateway.main import create_app
from yleum_gateway.routers import messages_native
from yleum_gateway.services import billing, provider_calls, text_failover


@pytest.fixture
def client(neutralize_lifespan, neutralize_side_effects, monkeypatch):
    monkeypatch.setenv("AITUNNEL_TEXT_FALLBACK_ENABLED", "true")
    monkeypatch.setenv("AITUNNEL_TEXT_API_KEY", "synthetic-backup-key")
    monkeypatch.setattr(messages_native, "native_messages_route", lambda: ("synthetic-primary", "https://api.llmgw.ru/v1"))
    with TestClient(create_app()) as client:
        yield client


def primary_error(status, request=None):
    error = {"message": HTTPStatus(status).phrase}
    if status == 402:
        error.update(type="insufficient_balance", code="insufficient_balance")
    return httpx.Response(status, json={"error": error}, request=request)


@pytest.mark.parametrize(("status", "retry_after", "primary_attempts"), [
    (401, None, 1), (402, None, 1), (422, None, 1), (500, None, 2), (501, None, 1),
    (502, None, 2), (503, None, 2), (504, None, 2), (505, None, 1), (None, None, 2), (503, "30", 1),
])
def test_native_refusal_continues_same_tools_with_separate_expense_and_one_charge(
    client, monkeypatch, status, retry_after, primary_attempts,
):
    sent = []

    def wire(request):
        sent.append((str(request.url), json.loads(request.content)))
        if request.url.host == "api.llmgw.ru":
            if status is None:
                raise httpx.ConnectTimeout("synthetic preconnect failure", request=request)
            response = primary_error(status, request=request)
            if retry_after is not None:
                response.headers["retry-after"] = retry_after
            return response
        assert provider_calls.finish_call.await_args.kwargs["status"] == "failed"
        return httpx.Response(200, json={
            "id": "backup-receipt", "model": "claude-sonnet-5.5",
            "usage": {"prompt_tokens": 10, "completion_tokens": 2, "cost_rub": 0.2},
            "choices": [{"finish_reason": "tool_calls", "message": {"content": None, "tool_calls": [
                {"id": "tool-1", "type": "function", "function": {"name": "read_file", "arguments": '{"path":"main.py"}'}}
            ]}}],
        })

    def reject_untimed_post(*args, **kwargs):
        pytest.fail("Generation with metadata must use the shared deadline without a timeout header")

    monkeypatch.setattr(text_failover, "select_scope", AsyncMock(return_value="llmgw"))
    monkeypatch.setattr(httpx, "AsyncHTTPTransport", lambda **_: httpx.MockTransport(wire))
    monkeypatch.setattr(messages_native, "_post_llmgw", reject_untimed_post)
    response = client.post("/v1/messages", json={
        "model": "claude-sonnet-5.5", "user": str(uuid4()),
        "metadata": {"run_id": str(uuid4()), "project_id": str(uuid4())},
        "messages": [{"role": "user", "content": "Read main.py"}],
        "tools": [{"name": "read_file", "input_schema": {"type": "object"}}],
        "tool_choice": {"type": "auto"}, "max_tokens": 128,
    })
    assert response.status_code == 200, response.text
    assert [url for url, _ in sent] == (
        ["https://api.llmgw.ru/v1/chat/completions"] * primary_attempts
        + ["https://api.aitunnel.ru/v1/chat/completions"]
    )
    assert all(payload["messages"] == sent[0][1]["messages"] for _, payload in sent)
    assert all(payload["tools"] == sent[0][1]["tools"] for _, payload in sent)
    assert sent[-1][1]["model"] == "claude-sonnet-5.5"
    assert response.json()["content"][0]["type"] == "tool_use"
    assert response.json()["metadata"]["fallback_used"] is True
    assert provider_calls.start_call.await_count == primary_attempts + 1
    assert all(call.kwargs["status"] == "failed"
               for call in provider_calls.finish_call.await_args_list[:primary_attempts])
    assert provider_calls.start_call.await_args_list[-1].kwargs["provider_scope"] == "aitunnel"
    assert billing.charge.await_count == 1
    assert billing.charge.await_args.kwargs["provider_scope"] == "aitunnel"


@pytest.mark.parametrize("preconnect", [False, True])
def test_failed_primary_receipt_commit_stops_backup(client, monkeypatch, preconnect):
    calls = []
    def post(url, *args, **kwargs):
        calls.append(url)
        if preconnect:
            raise httpx.ConnectTimeout("synthetic preconnect failure")
        return httpx.Response(502, json={"error": "Bad gateway"}, request=httpx.Request("POST", url))
    monkeypatch.setattr(messages_native, "_post_llmgw", post)
    monkeypatch.setattr(provider_calls, "finish_call", AsyncMock(side_effect=RuntimeError("unconfirmed commit")))
    response = client.post("/v1/messages", json={"model": "claude-sonnet-5.5", "messages": [{"role": "user", "content": "synthetic"}]})
    assert response.status_code == 503
    assert len(calls) == 1
    assert billing.charge.await_count == 0


@pytest.mark.parametrize("backup_completes", [False, True])
def test_native_safe_connect_failures_allow_two_primary_and_three_backup_attempts(
    client, monkeypatch, backup_completes,
):
    sent = []

    def wire(request):
        sent.append(request.url.host)
        if len(sent) < 5 or not backup_completes:
            raise httpx.ConnectTimeout("synthetic connection failure before send", request=request)
        return httpx.Response(200, json={
            "id": "backup-receipt", "model": "claude-sonnet-5.5",
            "usage": {"prompt_tokens": 10, "completion_tokens": 2, "cost_rub": 0.2},
            "choices": [{"finish_reason": "stop", "message": {"content": "result"}}],
        }, request=request)

    monkeypatch.setattr(text_failover, "select_scope", AsyncMock(return_value="llmgw"))
    monkeypatch.setattr(httpx, "AsyncHTTPTransport", lambda **_: httpx.MockTransport(wire))
    response = client.post("/v1/messages", headers={"x-yleum-provider-timeout-seconds": "30"}, json={
        "model": "claude-sonnet-5.5", "user": str(uuid4()),
        "metadata": {"run_id": str(uuid4()), "project_id": str(uuid4())},
        "messages": [{"role": "user", "content": "synthetic"}],
    })

    assert sent == ["api.llmgw.ru"] * 2 + ["api.aitunnel.ru"] * 3
    assert provider_calls.start_call.await_count == 5
    assert [call.kwargs["status"] for call in provider_calls.finish_call.await_args_list[:4]] == ["failed"] * 4
    if backup_completes:
        assert response.status_code == 200, response.text
        assert billing.charge.await_count == 1
    else:
        assert response.status_code == 503
        assert response.json()["error"]["details"]["retry_safe"] is False
        billing.charge.assert_not_awaited()


def test_legacy_native_connect_error_after_headers_cannot_use_backup(client, monkeypatch):
    posts = []

    class BrokenBody(httpx.SyncByteStream):
        def __iter__(self):
            raise httpx.ConnectError("synthetic response interrupted after headers")

    def reply(request):
        posts.append(request.url.host)
        return httpx.Response(200, stream=BrokenBody())

    monkeypatch.setattr(httpx, "HTTPTransport", lambda **_: httpx.MockTransport(reply))
    monkeypatch.setattr(httpx, "AsyncHTTPTransport", lambda **_: httpx.MockTransport(reply))
    response = client.post("/v1/messages", json={
        "model": "claude-sonnet-5.5",
        "max_tokens": 128,
        "messages": [{"role": "user", "content": "synthetic"}],
    })

    assert response.status_code >= 400
    assert posts == ["api.llmgw.ru"]
    assert provider_calls.finish_call.await_args.kwargs["status"] == "ambiguous"
    assert billing.charge.await_count == 0
    assert response.json()["error"]["details"]["transport_stage"] == "body"


@pytest.mark.parametrize("primary_refuses", [False, True])
@pytest.mark.parametrize("generation", [False, True])
def test_native_sticky_backup_preconnect_can_return_to_primary(client, monkeypatch, primary_refuses, generation):
    sent = []

    def wire(request):
        sent.append(request.url.host)
        if request.url.host == "api.aitunnel.ru":
            raise httpx.ConnectTimeout("synthetic reserve connect failure", request=request)
        assert provider_calls.finish_call.await_args.kwargs["status"] == "failed"
        if primary_refuses:
            return primary_error(502, request=request)
        return httpx.Response(200, json={
            "id": "primary-receipt", "model": "claude-sonnet-5.5",
            "usage": {"prompt_tokens": 10, "completion_tokens": 2, "cost_rub": 0.2},
            "choices": [{"finish_reason": "stop", "message": {"content": "result"}}],
        }, request=request)

    monkeypatch.setattr(text_failover, "select_scope", AsyncMock(return_value="aitunnel"))
    monkeypatch.setattr(httpx, "AsyncHTTPTransport", lambda **_: httpx.MockTransport(wire))
    response = client.post("/v1/messages", json={
        "model": "claude-sonnet-5.5", "user": str(uuid4()),
        "messages": [{"role": "user", "content": "synthetic"}], "max_tokens": 128,
        "metadata": {"run_id": str(uuid4()), "project_id": str(uuid4())} if generation else {},
    })
    if generation:
        assert sent == ["api.aitunnel.ru"] * 3 + ["api.llmgw.ru"] * (2 if primary_refuses else 1)
    else:
        assert sent == ["api.aitunnel.ru", "api.llmgw.ru"]
    assert provider_calls.start_call.await_count == len(sent)
    if primary_refuses:
        assert response.status_code == 503
        assert response.json()["error"]["details"]["retry_safe"] is False
        assert billing.charge.await_count == 0
    else:
        assert response.status_code == 200, response.text
        assert billing.charge.await_count == 1


def test_native_source_repair_reads_capped_reserve_as_stream(client, monkeypatch):
    sent = []

    def reserve(request):
        sent.append(json.loads(request.content))
        common = {"id": "backup-receipt", "model": "claude-sonnet-5.5"}
        chunks = [
            {**common, "choices": [{"index": 0, "delta": {"content": "result"}, "finish_reason": None}]},
            {**common, "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
            {**common, "choices": [], "usage": {"prompt_tokens": 10, "completion_tokens": 2, "cost_rub": 0.2}},
        ]
        return httpx.Response(200, headers={"content-type": "text/event-stream"},
            text="".join("data: " + json.dumps(chunk) + "\n\n" for chunk in chunks) + "data: [DONE]\n\n")

    monkeypatch.setattr(text_failover, "select_scope", AsyncMock(return_value="aitunnel"))
    monkeypatch.setattr(httpx, "AsyncHTTPTransport", lambda **_: httpx.MockTransport(reserve))
    response = client.post("/v1/messages", json={
        "model": "claude-sonnet-5.5", "user": str(uuid4()), "max_tokens": 8192,
        "messages": [{"role": "user", "content": "synthetic"}], "metadata": {"stage": "source_repair"},
    })
    assert [(body["stream"], body["max_tokens"]) for body in sent] == [(True, 8192)]
    assert response.status_code == 200, response.text
    assert response.json()["content"][0]["text"] == "result"
    assert billing.charge.await_count == 1


def test_legacy_native_skips_both_providers_when_shared_budget_is_exhausted(client, monkeypatch):
    posts = []

    def post(url, *args, **kwargs):
        posts.append(url)
        raise httpx.ConnectTimeout("synthetic preconnect failure")

    monkeypatch.setattr(httpx, "HTTPTransport", lambda **_: httpx.MockTransport(post))
    monkeypatch.setattr(httpx, "AsyncHTTPTransport", lambda **_: httpx.MockTransport(post))
    response = client.post("/v1/messages", headers={"x-yleum-provider-timeout-seconds": "1"}, json={
        "model": "claude-sonnet-5.5",
        "messages": [{"role": "user", "content": "synthetic"}],
    })

    assert response.status_code == 504
    assert response.json()["error"]["type"] == "deadline_before_dispatch"
    assert posts == []
    assert provider_calls.start_call.await_count == 0
    assert billing.charge.await_count == 0


@pytest.mark.parametrize("status", [401, 402, 422, 500, 502, 503, 504])
def test_durable_native_refusal_wire_matches_api_strict_contract(client, monkeypatch,status):
    from types import SimpleNamespace

    from yleum_gateway.services import generation_attempts, text_failover
    from yleum_gateway.services.generation_identity import (
        GenerationProviderIdentity,
        request_digest,
    )
    monkeypatch.setenv("GENERATION_PROVIDER_ATTEMPTS_ENABLED", "true")
    from yleum_gateway.core.config import reset_settings_cache
    reset_settings_cache()
    body = {"model": "claude-sonnet-5.5", "messages": [{"role": "user", "content": "synthetic"}]}
    identity = GenerationProviderIdentity(*(uuid4() for _ in range(4)), "a" * 64,
                                          request_digest("/v1/messages", body))
    body["metadata"] = {"run_id": str(identity.run_id), "project_id": str(identity.project_id),
                        "generation_provider_identity": identity.to_wire()}
    class Connection:
        async def fetchrow(self, *_): return None  # Root attempt has no parent.
        async def fetchval(self, *_): return False  # Run has no balance refusal yet.
    class Acquire:
        async def __aenter__(self): return Connection()
        async def __aexit__(self, *_): return False
    monkeypatch.setattr(text_failover, "get_pool", lambda: SimpleNamespace(acquire=Acquire))
    monkeypatch.setattr(generation_attempts, "reserve_generation_attempt",
                        AsyncMock(return_value=generation_attempts.AttemptAdmission("dispatch", dispatch_token=uuid4())))
    monkeypatch.setattr(generation_attempts, "mark_attempt_dispatching", AsyncMock(return_value=uuid4()))
    closed = AsyncMock()
    monkeypatch.setattr(generation_attempts, "close_attempt", closed)
    posts = []
    def reply(request):
        posts.append(request)
        return primary_error(status)
    monkeypatch.setattr(httpx, "AsyncHTTPTransport", lambda **_: httpx.MockTransport(reply))
    response = client.post("/v1/messages", json=body)
    assert response.status_code == 503, response.text
    wire = response.json()
    assert set(wire) == {"type", "error"} and wire["type"] == "error"
    error = wire["error"]
    assert error["type"] == error["code"] == "api_error"
    assert error["details"] == {
        "provider_failure_kind": "upstream_refused", "provider_charge_ambiguous": False,
        "retry_safe": True,
        "retry_policy": "llmgw_unavailable_v1" if status >= 500 else "llmgw_http_refused_v1",
        "upstream_http_status": status,
    }
    assert len(posts) == 1 and billing.charge.await_count == 0
    assert provider_calls.finish_call.await_args.kwargs["status"] == "failed"
    assert closed.await_args.kwargs["reason"] == "upstream_refused"


def test_native_backup_model_mismatch_is_closed_without_charge(client, monkeypatch):
    posts = []
    def reply(request):
        posts.append(request)
        if len(posts) == 1:
            return httpx.Response(502, json={"error": "Bad gateway"})
        return httpx.Response(200, json={
            "id": "wrong-backup-model", "model": "claude-sonnet-5",
            "usage": {"prompt_tokens": 10, "completion_tokens": 1, "cost_rub": 0.2},
            "choices": [{"finish_reason": "stop", "message": {"content": "wrong model"}}],
        })
    monkeypatch.setattr(httpx, "HTTPTransport", lambda **_: httpx.MockTransport(reply))
    monkeypatch.setattr(httpx, "AsyncHTTPTransport", lambda **_: httpx.MockTransport(reply))
    response = client.post("/v1/messages", json={
        "model": "claude-sonnet-5.5", "max_tokens": 128, "user": str(uuid4()),
        "messages": [{"role": "user", "content": "synthetic"}],
    })
    assert response.status_code >= 400
    assert len(posts) == 2 and billing.charge.await_count == 0
    assert "llmgw_overload_v1" not in response.text
    assert "llmgw_unavailable_v1" not in response.text
