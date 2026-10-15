"""Model-specific output ceilings reach the real agent request boundaries."""

import json

import httpx
import pytest

from yleum_api.services import agent_builder, agent_native

from .test_legacy_provider_error_policy import install_transport


@pytest.mark.parametrize("reason", ["stop", "length"])
async def test_legacy_gateway_payload_raises_ceiling_without_accepting_truncated_actions(
    monkeypatch, reason,
):
    payloads = []
    actions = []

    def gateway(request):
        payloads.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{
            "finish_reason": reason,
            "message": {"content": '<omnia:action name="bash">{"cmd":"CHECK"}</omnia:action>'},
        }]})

    async def execute(action):
        actions.append(action.name)
        return {"ok": True}

    install_transport(monkeypatch, gateway)
    result = await agent_builder.run_agent_build(
        system_prompt="s", user_prompt="t", model="claude-sonnet-5.5",
        execute=execute, max_steps=1,
    )

    assert len(payloads) == 1
    assert payloads[0]["max_tokens"] == 128000
    assert actions == (["bash"] if reason == "stop" else [])
    if reason == "length":
        assert result.stop_reason == "output_limit"


@pytest.mark.parametrize("entry", [agent_builder.run_agent_build, agent_builder._run_agent_segment])
@pytest.mark.parametrize("model,expected", [
    ("claude-sonnet-5.5", 128000),
    ("anthropic/claude-sonnet-5.5", 128000),
    ("claude-sonnet-5", 16384),
    ("claude-sonnet-5.50", 16384),
])
@pytest.mark.parametrize("override", [None, 4096])
async def test_legacy_model_default_and_explicit_override_reach_completion(
    entry, model, expected, override,
):
    calls = []

    async def complete(messages, selected_model, **kwargs):
        calls.append((selected_model, kwargs["max_tokens"]))
        return '<omnia:action name="done">{"summary":"ready"}</omnia:action>'

    async def execute(action):
        pytest.fail("done must not execute a workspace tool")

    options = {"max_tokens": override} if override is not None else {}
    result = await entry(
        system_prompt="s", user_prompt="t", model=model,
        execute=execute, complete=complete, max_steps=1, **options,
    )

    assert result.done
    assert calls == [(model, expected if override is None else override)]


@pytest.mark.parametrize("override,expected", [(None, [16384, 16384, 128000]),
                                               (4096, [4096, 4096, 4096])])
async def test_legacy_escalation_uses_active_model_budget_unless_overridden(override, expected):
    calls = []

    async def complete(messages, model, **kwargs):
        calls.append(kwargs["max_tokens"])
        return "no action" if len(calls) < 3 else '<omnia:action name="done">{}</omnia:action>'

    async def execute(action):
        pytest.fail("no workspace tool was requested")

    options = {"max_tokens": override} if override is not None else {}
    result = await agent_builder.run_agent_build(
        system_prompt="s", user_prompt="t", model="claude-sonnet-5",
        escalate_model="claude-sonnet-5.5", execute=execute,
        complete=complete, max_steps=3, **options,
    )

    assert result.done
    assert calls == expected


@pytest.mark.parametrize("model,expected", [
    ("claude-sonnet-5.5", 128000),
    ("anthropic/claude-sonnet-5.5", 128000),
    ("claude-sonnet-5", 20000),
    ("claude-sonnet-5.50", 20000),
])
async def test_native_request_uses_model_ceiling_and_matching_request_profile(model, expected):
    payloads = []
    reply = {"stop_reason": "end_turn", "content": [{"type": "text", "text": "ready"}]}

    class Gateway:
        async def post(self, url, **kwargs):
            payloads.append(kwargs["json"])
            return httpx.Response(200, request=httpx.Request("POST", url), json=reply)

    result = await agent_native._call_messages(
        Gateway(), "https://gateway.test/v1/messages", [], "s", model=model,
        tool_choice={"type": "any"},
    )

    assert result == reply
    assert len(payloads) == 1
    assert payloads[0]["max_tokens"] == expected
    if expected == 128000:
        assert "thinking" not in payloads[0]
        assert payloads[0]["tool_choice"] == {"type": "auto"}
    else:
        assert payloads[0]["thinking"] == {"type": "enabled", "budget_tokens": 8000}
        assert payloads[0]["tool_choice"] == {"type": "any"}


@pytest.mark.parametrize("source_repair", [False, True])
async def test_native_build_and_source_repair_send_the_large_ceiling(monkeypatch, source_repair):
    payloads = []

    def gateway(request):
        payloads.append(json.loads(request.content))
        # A definitive refusal prevents tools/retries while exercising the full request path.
        return httpx.Response(400, json={"error": {"type": "invalid_request_error"}})

    client = httpx.AsyncClient
    monkeypatch.setattr(
        agent_native.httpx, "AsyncClient",
        lambda: client(transport=httpx.MockTransport(gateway)),
    )

    async def execute(action):
        pytest.fail("refused provider request must not execute workspace tools")

    result = await agent_native.run_native_build(
        model="claude-sonnet-5.5", system="s", task="Repair source", execute=execute,
        source_repair=source_repair, max_steps=1,
        messages_url="https://gateway.test/v1/messages",
    )

    assert result.stop_reason == "provider_error"
    assert len(payloads) == 1
    assert payloads[0]["max_tokens"] == 128000
