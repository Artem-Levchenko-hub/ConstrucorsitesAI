from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient

from yleum_gateway.main import create_app
from yleum_gateway.routers import messages_native
from yleum_gateway.services import billing, provider_calls


@pytest.fixture
def client(neutralize_lifespan, neutralize_side_effects, monkeypatch):
    monkeypatch.setenv("AITUNNEL_TEXT_FALLBACK_ENABLED", "true")
    monkeypatch.setenv("AITUNNEL_TEXT_API_KEY", "synthetic-backup-key")
    monkeypatch.setattr(messages_native, "native_messages_route", lambda: ("synthetic-primary", "https://api.llmgw.ru/v1"))
    with TestClient(create_app()) as client:
        yield client


@pytest.mark.parametrize("status", [500,502,503,504])
def test_native_refusal_continues_same_tools_with_separate_expense_and_one_charge(client, monkeypatch,status):
    sent = []

    def post(url, payload, headers, **kwargs):
        sent.append((url, dict(payload)))
        if len(sent) == 1:
            return httpx.Response(status, json={"error": {500:"Internal Server Error",502:"Bad Gateway",503:"Service Unavailable",504:"Gateway Timeout"}[status]}, request=httpx.Request("POST", url))
        return httpx.Response(200, request=httpx.Request("POST", url), json={
            "id": "backup-receipt", "model": "claude-sonnet-5.5",
            "usage": {"prompt_tokens": 10, "completion_tokens": 2, "cost_rub": 0.2},
            "choices": [{"finish_reason": "tool_calls", "message": {"content": None, "tool_calls": [
                {"id": "tool-1", "type": "function", "function": {"name": "read_file", "arguments": '{"path":"main.py"}'}}
            ]}}],
        })

    monkeypatch.setattr(messages_native, "_post_llmgw", post)
    response = client.post("/v1/messages", json={
        "model": "claude-sonnet-5.5", "user": str(uuid4()),
        "messages": [{"role": "user", "content": "Read main.py"}],
        "tools": [{"name": "read_file", "input_schema": {"type": "object"}}],
        "tool_choice": {"type": "auto"}, "max_tokens": 128,
    })
    assert response.status_code == 200, response.text
    assert [url for url, _ in sent] == ["https://api.llmgw.ru/v1/chat/completions", "https://api.aitunnel.ru/v1/chat/completions"]
    assert sent[0][1]["messages"] == sent[1][1]["messages"]
    assert sent[0][1]["tools"] == sent[1][1]["tools"]
    assert sent[1][1]["model"] == "claude-sonnet-5.5"
    assert response.json()["content"][0]["type"] == "tool_use"
    assert response.json()["metadata"]["fallback_used"] is True
    assert provider_calls.start_call.await_count == 2
    assert provider_calls.finish_call.await_args_list[0].kwargs["status"] == "failed"
    assert provider_calls.start_call.await_args_list[1].kwargs["provider_scope"] == "aitunnel"
    assert billing.charge.await_count == 1
    assert billing.charge.await_args.kwargs["provider_scope"] == "aitunnel"


def test_failed_primary_receipt_commit_stops_backup(client, monkeypatch):
    calls = []
    def post(url, *args, **kwargs):
        calls.append(url)
        return httpx.Response(502, json={"error": "Bad gateway"}, request=httpx.Request("POST", url))
    monkeypatch.setattr(messages_native, "_post_llmgw", post)
    monkeypatch.setattr(provider_calls, "finish_call", AsyncMock(side_effect=RuntimeError("unconfirmed commit")))
    response = client.post("/v1/messages", json={"model": "claude-sonnet-5.5", "messages": [{"role": "user", "content": "synthetic"}]})
    assert response.status_code == 503
    assert len(calls) == 1
    assert billing.charge.await_count == 0


@pytest.mark.parametrize("status", [500,502,503,504])
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
        return httpx.Response(status, json={"error": {500:"Internal Server Error",502:"Bad Gateway",503:"Service Unavailable",504:"Gateway Timeout"}[status]})
    monkeypatch.setattr(httpx, "HTTPTransport", lambda **_: httpx.MockTransport(reply))
    response = client.post("/v1/messages", json=body)
    assert response.status_code == 503, response.text
    wire = response.json()
    assert set(wire) == {"type", "error"} and wire["type"] == "error"
    error = wire["error"]
    assert error["type"] == error["code"] == "api_error"
    assert error["details"] == {
        "provider_failure_kind": "upstream_refused", "provider_charge_ambiguous": False,
        "retry_safe": True, "retry_policy": "llmgw_unavailable_v1", "upstream_http_status": status,
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
    response = client.post("/v1/messages", json={
        "model": "claude-sonnet-5.5", "max_tokens": 128, "user": str(uuid4()),
        "messages": [{"role": "user", "content": "synthetic"}],
    })
    assert response.status_code >= 400
    assert len(posts) == 2 and billing.charge.await_count == 0
    assert "llmgw_overload_v1" not in response.text
    assert "llmgw_unavailable_v1" not in response.text
