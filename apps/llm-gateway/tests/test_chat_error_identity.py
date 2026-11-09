"""The terminal backup refusal remains attributable without provider body export."""

import asyncio
from uuid import uuid4

import pytest
from fastapi import HTTPException, Request

from yleum_gateway.core.errors import UpstreamProviderError
from yleum_gateway.routers import chat
from yleum_gateway.services import billing, provider_calls


async def test_legacy_backup_failure_keeps_scope_stage_and_local_call_identity(
    monkeypatch, neutralize_side_effects,
):
    calls, diagnostics = [], []
    monkeypatch.setattr(chat.text_failover, "enabled", lambda model: True)
    monkeypatch.setattr(chat.text_failover, "credentials", lambda scope: ("private-key", "https://backup.test/v1"))
    async def complete(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            raise UpstreamProviderError("private primary body", details={
                "upstream_http_status": 503, "provider_charge_ambiguous": False,
                "_overload_retry_candidate": {
                    "provider_failure_kind": "upstream_refused", "provider_charge_ambiguous": False,
                    "retry_safe": True, "retry_policy": "llmgw_http_refused_v1",
                    "upstream_http_status": 503,
                },
            })
        raise UpstreamProviderError("private backup body", details={
            "upstream_http_status": 402, "transport_stage": "body",
        })
    monkeypatch.setattr(chat.llmgw, "acompletion", complete)
    monkeypatch.setattr(chat.log, "warning", lambda event, **fields: diagnostics.append((event, fields)))
    req = chat.ChatCompletionRequest(
        model="claude-sonnet-5.5", messages=[{"role": "user", "content": "synthetic"}],
        user=uuid4(), metadata=chat.ChatMetadata(run_id=uuid4(), project_id=uuid4(), message_id=uuid4()),
    )
    with pytest.raises(HTTPException) as raised:
        await chat._dispatch_chat_provider(
            req, Request({"type": "http", "headers": []}), identity=None,
            filtered_messages=[{"role": "user", "content": "synthetic"}],
            received_at=asyncio.get_running_loop().time(),
        )
    details = raised.value.detail["error"]["details"]
    assert details["upstream_http_status"] == 402
    assert details["provider_scope"] == "aitunnel" and details["transport_stage"] == "body"
    assert details["provider_call_id"] == str(provider_calls.finish_call.await_args.args[0])
    assert len(calls) == provider_calls.finish_call.await_count == 2
    assert provider_calls.finish_call.await_args.kwargs["status"] == "ambiguous"
    terminal = diagnostics[-1][1]
    assert terminal["provider_scope"] == "aitunnel" and terminal["upstream_http_status"] == 402
    assert terminal["provider_call_id"] == details["provider_call_id"]
    assert "private" not in str(diagnostics) + str(details)
    assert billing.charge.await_count == 0
