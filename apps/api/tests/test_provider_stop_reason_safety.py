"""Unconfirmed completion cannot execute actions or trigger another paid call."""

from unittest.mock import AsyncMock

import httpx
import pytest

from yleum_api.services import agent_builder, agent_native, llm_client

from .test_legacy_provider_error_policy import install_transport


@pytest.mark.parametrize("reason", [None, "content_filter", "pause_turn", "unknown", []])
async def test_classic_invalid_finish_does_not_reach_action_executor(monkeypatch, reason):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={"choices": [{
            "finish_reason": reason,
            "message": {"content": '<omnia:action name="bash">{"cmd":"NEVER"}</omnia:action>'},
        }]})

    install_transport(monkeypatch, handler)
    execute = AsyncMock(return_value={"ok": True})
    result = await agent_builder.run_agent_build(
        system_prompt="s", user_prompt="edit", model="m", execute=execute, max_steps=3,
    )
    assert not result.done
    execute.assert_not_awaited()
    assert len(requests) == 1


@pytest.mark.parametrize("reason", ["stop", "tool_calls"])
@pytest.mark.parametrize("content", ["ok", "", None])
async def test_classic_confirmed_finish_preserves_string_contract(monkeypatch, reason, content):
    install_transport(monkeypatch, lambda request: httpx.Response(200, json={
        "choices": [{"finish_reason": reason, "message": {"content": content}}],
    }))
    assert await llm_client.complete_chat([], "m") == (content or "")


@pytest.mark.parametrize("code", ["provider_output_invalid", "provider_output_filtered"])
async def test_classic_gateway_terminal_output_code_is_retained(monkeypatch, code):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(502, json={"detail": {"error": {
            "code": code, "message": "PRIVATE", "retry_safe": False,
            "details": {"retry_safe": False, "provider_charge_ambiguous": False},
        }}})

    install_transport(monkeypatch, handler)
    with pytest.raises(llm_client.LLMError) as err:
        await llm_client.complete_chat([], "m")
    assert err.value.code == code
    assert err.value.retry_safe is False
    assert "PRIVATE" not in str(err.value)
    assert len(calls) == 1


@pytest.mark.parametrize("code", ["provider_output_invalid", "provider_output_filtered"])
@pytest.mark.parametrize("field", ["code", "type"])
async def test_native_gateway_output_failure_never_retries(monkeypatch, code, field):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(502, json={"error": {
            field: code, "retry_safe": False, "message": "PRIVATE rate_limit",
        }})

    sleep = AsyncMock()
    monkeypatch.setattr(agent_native.asyncio, "sleep", sleep)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(RuntimeError, match=code.upper()) as err:
            await agent_native._call_messages(client, "https://gateway.test/v1/messages", [], "s")
    assert "PRIVATE" not in str(err.value)
    assert len(calls) == 1
    sleep.assert_not_awaited()


@pytest.mark.parametrize("reason", [None, "content_filter", "refusal", "pause_turn", "unknown", []])
async def test_native_bad_stop_reason_is_terminal_before_any_tool(monkeypatch, reason):
    provider = AsyncMock(return_value={"stop_reason": reason, "content": [{
        "type": "tool_use", "id": "bad", "name": "bash", "input": {"cmd": "NEVER"},
    }]})
    execute = AsyncMock(return_value={"ok": True})
    monkeypatch.setattr(agent_native, "_call_messages", provider)
    result = await agent_native._run_native_segment(
        system="s", task="edit", execute=execute, max_steps=3,
    )
    assert not result.done and result.stop_reason == "provider_error"
    assert "PROVIDER_OUTPUT_" in result.summary
    execute.assert_not_awaited()
    assert provider.await_count == 1


@pytest.mark.parametrize("reason", ["end_turn", "tool_use", "stop_sequence", "max_tokens"])
async def test_native_known_stop_reason_keeps_original_body(reason):
    body = {"stop_reason": reason, "content": []}
    async with httpx.AsyncClient(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, json=body),
    )) as client:
        assert await agent_native._call_messages(
            client, "https://gateway.test/v1/messages", [], "s",
        ) == body
