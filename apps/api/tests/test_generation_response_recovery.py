from __future__ import annotations

from types import SimpleNamespace
from uuid import UUID

import httpx
import pytest
from pydantic import SecretStr

from yleum_api.services import llm_client
from yleum_api.services.generation import response_auth
from yleum_api.services.generation.provider_identity import GenerationProviderIdentity

IDENTITY = GenerationProviderIdentity(*(UUID(int=i) for i in range(1, 5)), "a" * 64, "b" * 64)


def test_record_header_preserves_runner_auth_and_default_none(monkeypatch):
    settings = SimpleNamespace(generation_response_retention_enabled=False)
    monkeypatch.setattr("yleum_api.core.config.get_settings", lambda: settings)
    assert response_auth.record_headers(IDENTITY) is None
    settings.generation_response_retention_enabled = True
    settings.generation_response_signing_secret = SecretStr("synthetic-test-secret-32-bytes-long")
    result = response_auth.record_headers(IDENTITY, {"Authorization": "runner-token"})
    assert result["Authorization"] == "runner-token"
    assert (
        response_auth.verify_generation_response_token(
            result[response_auth.HEADER], purpose="generation_response.record"
        )
        == IDENTITY
    )


@pytest.mark.parametrize("state", ["pending", "indeterminate", "missing", "wrong_binding"])
async def test_recovery_none_only_from_explicit_bound_pending(monkeypatch, state):
    settings = SimpleNamespace(
        generation_response_retention_enabled=True,
        generation_response_signing_secret=SecretStr("synthetic-test-secret-32-bytes-long"),
        llm_gateway_url="http://gateway.test",
    )
    monkeypatch.setattr("yleum_api.core.config.get_settings", lambda: settings)
    monkeypatch.setattr(llm_client, "get_settings", lambda: settings)

    def reply(request):
        assert request.method == "GET"
        assert (
            response_auth.verify_generation_response_token(
                request.headers[response_auth.HEADER], purpose="generation_response.read"
            )
            == IDENTITY
        )
        if state == "missing":
            return httpx.Response(404)
        return httpx.Response(
            200,
            json={
                "version": 1,
                "identity": IDENTITY.to_wire() if state != "wrong_binding" else {},
                "state": state,
            },
        )

    original = httpx.AsyncClient
    monkeypatch.setattr(
        httpx, "AsyncClient", lambda **kw: original(transport=httpx.MockTransport(reply), **kw)
    )
    if state == "pending":
        assert await llm_client.recover_generation_response(IDENTITY) is None
    else:
        with pytest.raises(llm_client.LLMError) as exc:
            await llm_client.recover_generation_response(IDENTITY)
        assert exc.value.provider_charge_ambiguous is True and not exc.value.retry_safe


@pytest.mark.parametrize("edge", ["complete", "sse", "native"])
async def test_actual_callers_send_exact_record_purpose_without_output_cap(monkeypatch, edge):
    import asyncio
    import json
    from unittest.mock import AsyncMock

    from yleum_api.services import agent_native
    from yleum_api.services.generation import provider_attempts

    settings = SimpleNamespace(
        generation_response_retention_enabled=True,
        generation_response_signing_secret=SecretStr("synthetic-test-secret-32-bytes-long"),
    )
    monkeypatch.setattr("yleum_api.core.config.get_settings", lambda: settings)
    monkeypatch.setattr(
        provider_attempts, "prepare_generation_attempt", AsyncMock(return_value=IDENTITY)
    )
    sent = []

    def reply(request):
        payload = json.loads(request.content)
        assert "max_tokens" not in payload
        assert (
            response_auth.verify_generation_response_token(
                request.headers[response_auth.HEADER], purpose="generation_response.record"
            )
            == IDENTITY
        )
        assert payload["metadata"]["generation_provider_identity"] == IDENTITY.to_wire()
        sent.append(payload)
        if edge == "sse":
            return httpx.Response(
                200,
                text='data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n',
            )
        if edge == "native":
            assert request.headers["Authorization"] == "synthetic-runner"
            return httpx.Response(200, json={"content": [], "stop_reason": "end_turn"})
        return httpx.Response(
            200, json={"choices": [{"finish_reason": "stop", "message": {"content": "ok"}}]}
        )

    original = httpx.AsyncClient
    monkeypatch.setattr(
        httpx, "AsyncClient", lambda **kw: original(transport=httpx.MockTransport(reply), **kw)
    )
    payload = {
        "model": "claude-sonnet-5.5",
        "messages": [{"role": "user", "content": "synthetic"}],
        "metadata": {},
    }
    if edge == "native":
        async with httpx.AsyncClient() as client:
            await agent_native._call_messages(
                client,
                "http://gateway.test/v1/messages",
                [],
                "synthetic",
                model="claude-sonnet-5.5",
                headers={"Authorization": "synthetic-runner"},
            )
    elif edge == "sse":
        assert [
            event
            async for event in llm_client._chat_stream_data(
                payload,
                "http://gateway.test/v1/chat/completions",
                IDENTITY,
                asyncio.get_running_loop().time() + 30,
            )
        ]
    else:
        assert (
            await llm_client._chat_post(payload, "http://gateway.test/v1/chat/completions", 30)
        ).status_code == 200
    assert len(sent) == 1


@pytest.mark.parametrize(
    "events", [[], ["partial"], ["[DONE]", "tail"], [{"dict": "not event"}, "[DONE]"]]
)
async def test_recovery_rejects_incomplete_or_malformed_sse(monkeypatch, events):
    settings = SimpleNamespace(
        generation_response_retention_enabled=True,
        generation_response_signing_secret=SecretStr("synthetic-test-secret-32-bytes-long"),
        llm_gateway_url="http://gateway.test",
    )
    monkeypatch.setattr("yleum_api.core.config.get_settings", lambda: settings)
    monkeypatch.setattr(llm_client, "get_settings", lambda: settings)
    original = httpx.AsyncClient
    reply = httpx.Response(
        200,
        json={
            "version": 1,
            "identity": IDENTITY.to_wire(),
            "state": "stored",
            "response": {"format": "sse", "events": events},
        },
    )
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kw: original(transport=httpx.MockTransport(lambda _: reply), **kw),
    )
    with pytest.raises(llm_client.LLMError) as exc:
        await llm_client.recover_generation_response(IDENTITY)
    assert exc.value.provider_charge_ambiguous and not exc.value.retry_safe
