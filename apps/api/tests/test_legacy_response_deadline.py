"""Existing editing time reaches the one paid provider response, without replay."""

import asyncio
import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from yleum_api.services import agent_builder, llm_client

from .test_legacy_provider_error_policy import install_transport


@pytest.mark.parametrize("model,cap,bounded_repair,expected_budget,expected_cap", [
    ("claude-sonnet-5.5", None, False, 600, None),
    ("anthropic/claude-sonnet-5.5", None, False, 600, None),
    ("claude-sonnet-5.5", 4096, False, 300, 4096),
    ("claude-sonnet-5.5", None, True, 600, 8192),
    ("claude-sonnet-5.5", 4096, True, 300, 4096),
    ("claude-sonnet-5", None, False, 300, 16384),
])
async def test_agent_forwards_sonnet_deadline_with_and_without_output_limit(
    monkeypatch, model, cap, bounded_repair, expected_budget, expected_cap,
):
    requests = []
    def reply(request):
        requests.append(request)
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {
            "content": '<omnia:action name="done">{}</omnia:action>',
        }}]})
    install_transport(monkeypatch, reply)
    async def execute(action):
        pytest.fail("done must not execute an action")
    result = await agent_builder.run_agent_build(
        system_prompt="s", user_prompt="t", model=model, execute=execute,
        edit_deadline=datetime.now(UTC) + timedelta(seconds=600), max_steps=1,
        bounded_repair=bounded_repair,
        **({"max_tokens": cap} if cap else {}),
    )
    assert result.done and len(requests) == 1
    budget = requests[0].extensions["timeout"]["read"]
    assert expected_budget - 2 < budget <= expected_budget
    assert json.loads(requests[0].content).get("max_tokens") == expected_cap
    if model.endswith("claude-sonnet-5.5"):
        assert float(requests[0].headers["x-yleum-provider-timeout-seconds"]) == budget
    else:
        assert "x-yleum-provider-timeout-seconds" not in requests[0].headers


@pytest.mark.parametrize("max_tokens", [None, 8192])
async def test_client_total_deadline_is_terminal_no_replay(monkeypatch, max_tokens):
    requests = []
    async def hung(request):
        requests.append(request)
        await asyncio.Event().wait()
    install_transport(monkeypatch, hung)
    # Avoid an actual one-second wait: intercept only this module's timeout
    # factory, leaving the HTTP client's asyncio runtime untouched.
    from types import SimpleNamespace
    monkeypatch.setattr(llm_client, "asyncio", SimpleNamespace(
        get_running_loop=asyncio.get_running_loop,
        timeout_at=lambda value: asyncio.timeout_at(asyncio.get_running_loop().time() + 0.01),
    ))
    with pytest.raises(llm_client.LLMError) as caught:
        await llm_client.complete_chat(
            [], "claude-sonnet-5.5", max_tokens=max_tokens, timeout_seconds=600,
        )
    assert caught.value.provider_charge_ambiguous is True
    assert caught.value.retry_safe is False and len(requests) == 1
