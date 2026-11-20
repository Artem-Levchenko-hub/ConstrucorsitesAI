"""Existing editing time reaches the one paid provider response, without replay."""

import asyncio
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import httpx
import pytest

from yleum_api.services import agent_builder, llm_client

from .test_legacy_provider_error_policy import install_transport


def short_total_timeout(deadline):
    return asyncio.timeout_at(
        asyncio.get_running_loop().time() + 0.01 if deadline is not None else None,
    )


async def collect_stream(model):
    return [event async for event in llm_client.stream_chat_completion([], model, "u", "p", "m")]


@pytest.mark.parametrize("model,cap,bounded_repair,expected_budget,expected_cap", [
    ("claude-sonnet-5.5", None, False, 600, None),
    ("anthropic/claude-sonnet-5.5", None, False, 600, None),
    ("claude-sonnet-5.5", 4096, False, 300, 4096),
    ("claude-sonnet-5.5", None, True, 600, 128_000),
    ("claude-sonnet-5.5", 4096, True, 300, 4096),
    ("claude-sonnet-5", None, False, 300, 16384),
    ("gpt-6.2-sol", None, False, 600, None),
    ("openai/gpt-6.2-sol", None, False, 600, None),
    ("gpt-6.2-sol", 4096, False, 300, 4096),
    ("gpt-6.2-sol", None, True, 600, None),
])
async def test_agent_forwards_selected_model_deadline_with_and_without_output_limit(
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
    if model != "claude-sonnet-5":
        assert float(requests[0].headers["x-yleum-provider-timeout-seconds"]) == budget
    else:
        assert "x-yleum-provider-timeout-seconds" not in requests[0].headers


@pytest.mark.parametrize("max_tokens", [None, 8192])
@pytest.mark.parametrize("model", ["claude-sonnet-5.5", "gpt-6.2-sol"])
async def test_client_total_deadline_is_terminal_no_replay(monkeypatch, max_tokens, model):
    requests = []
    async def hung(request):
        requests.append(request)
        await asyncio.Event().wait()
    install_transport(monkeypatch, hung)
    # Avoid an actual one-second wait: intercept only this module's timeout
    # factory, leaving the HTTP client's asyncio runtime untouched.
    monkeypatch.setattr(llm_client, "asyncio", SimpleNamespace(
        get_running_loop=asyncio.get_running_loop,
        timeout_at=short_total_timeout,
    ))
    with pytest.raises(llm_client.LLMError) as caught:
        await asyncio.wait_for(
            llm_client.complete_chat([], model, max_tokens=max_tokens, timeout_seconds=600), 0.5,
        )
    assert caught.value.provider_charge_ambiguous is True
    assert caught.value.retry_safe is False and len(requests) == 1


def virtual_clock(monkeypatch):
    clock = SimpleNamespace(seconds=100.0, deadlines=[])

    def timeout_at(deadline):
        clock.deadlines.append(deadline)
        # The tests below explicitly advance virtual provider/SQL time. Separate
        # real cancellation tests exercise the actual total timeout context.
        return asyncio.timeout(None)

    monkeypatch.setattr(llm_client, "asyncio", SimpleNamespace(
        get_running_loop=lambda: SimpleNamespace(time=lambda: clock.seconds),
        timeout_at=timeout_at,
        sleep=asyncio.sleep,
    ))
    return clock


def completed_response(stream):
    if stream:
        return httpx.Response(200, text=(
            'data: {"choices":[{"delta":{"content":"complete"}}]}\n\n'
            'data: {"choices":[{"delta":{},"finish_reason":"stop"}],'
            '"usage":{"prompt_tokens":10,"completion_tokens":3}}\n\n'
            'data: [DONE]\n\n'
        ))
    return httpx.Response(200, json={"choices": [{
        "finish_reason": "stop", "message": {"content": "complete"},
    }]})


@pytest.mark.parametrize("model,timed", [
    ("gpt-6.2-sol", True), ("claude-sonnet-5.5", True), ("claude-sonnet-5", False),
])
@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("durable_seconds", [None, 200.0])
async def test_forwarded_http_budget_spends_reservation_wait_and_keeps_durable_bound(
    monkeypatch, model, timed, stream, durable_seconds,
):
    from yleum_api.services.generation import provider_attempts, recovery_reconciliation

    clock = virtual_clock(monkeypatch)
    requests = []

    async def reserve(identity, *, wire_attempt):
        assert identity is None and wire_attempt == 0
        clock.seconds += 45.0
        return None

    def reply(request):
        requests.append(request)
        return completed_response(stream)

    install_transport(monkeypatch, reply)
    monkeypatch.setattr(provider_attempts, "reserve_generation_wire", reserve)
    monkeypatch.setattr(
        recovery_reconciliation, "provider_dispatch_remaining", lambda: durable_seconds,
    )
    caller_seconds = 300.0 if stream else 600.0
    if stream:
        events = await collect_stream(model)
        assert events[-1]["usage"]["tokens_out"] == 3
    else:
        text = await llm_client.complete_chat(
            [], model, max_tokens=None, timeout_seconds=caller_seconds,
        )
        assert text == "complete"
    assert len(requests) == 1
    expected = caller_seconds - 45.0 if timed else caller_seconds
    if durable_seconds is not None:
        expected = min(expected, durable_seconds)
    assert requests[0].extensions["timeout"]["read"] == expected
    if timed:
        assert float(requests[0].headers["x-yleum-provider-timeout-seconds"]) == expected
    else:
        assert "x-yleum-provider-timeout-seconds" not in requests[0].headers
    assert clock.deadlines == [100.0 + caller_seconds if timed else None]


@pytest.mark.parametrize("model", ["gpt-6.2-sol", "claude-sonnet-5.5"])
@pytest.mark.parametrize("stream", [False, True])
async def test_partial_gateway_body_can_finish_after_old_default_with_forwarded_budget(
    monkeypatch, model, stream,
):
    clock = virtual_clock(monkeypatch)
    requests = []

    class PartialBody(httpx.AsyncByteStream):
        def __init__(self, request):
            self.request = request

        async def __aiter__(self):
            if stream:
                yield b'data: {"choices":[{"delta":{"content":"partial"}}]}\n\n'
            else:
                yield b'{"choices":[{"message":{"content":"complete"},'
            # Model the gateway's finite default when no deadline header exists.
            # 280s fits each caller's window but exceeds the old gateway default.
            clock.seconds += 280.0
            forwarded = float(self.request.headers.get("x-yleum-provider-timeout-seconds", "270"))
            if 280.0 > forwarded:
                raise httpx.ReadTimeout("synthetic unconfirmed body deadline", request=self.request)
            if stream:
                yield (b'data: {"choices":[{"delta":{},"finish_reason":"stop"}],'
                       b'"usage":{"prompt_tokens":10,"completion_tokens":3}}\n\ndata: [DONE]\n\n')
            else:
                yield b'"finish_reason":"stop"}]}'

    def reply(request):
        requests.append(request)
        return httpx.Response(200, stream=PartialBody(request))

    install_transport(monkeypatch, reply)
    if stream:
        events = await collect_stream(model)
        assert events[0] == {"delta": "partial"}
        assert events[-1]["usage"]["tokens_out"] == 3
        assert not any("error" in event for event in events)
        expected = 300.0
    else:
        text = await llm_client.complete_chat([], model, max_tokens=None, timeout_seconds=600)
        assert text == "complete"
        expected = 600.0
    assert len(requests) == 1
    assert float(requests[0].headers["x-yleum-provider-timeout-seconds"]) == expected
    assert clock.deadlines == [100.0 + expected]


@pytest.mark.parametrize("model", ["gpt-6.2-sol", "claude-sonnet-5.5"])
@pytest.mark.parametrize("stream", [False, True])
async def test_total_timeout_after_partial_body_is_unconfirmed_and_never_replayed(
    monkeypatch, model, stream,
):
    requests = []

    class HungBody(httpx.AsyncByteStream):
        async def __aiter__(self):
            if stream:
                yield b'data: {"choices":[{"delta":{"content":"partial"}}]}\n\n'
            else:
                yield b'{"choices":['
            await asyncio.Event().wait()

    def reply(request):
        requests.append(request)
        return httpx.Response(200, stream=HungBody())

    install_transport(monkeypatch, reply)
    monkeypatch.setattr(llm_client, "asyncio", SimpleNamespace(
        get_running_loop=asyncio.get_running_loop,
        timeout_at=short_total_timeout,
    ))
    if stream:
        events = await asyncio.wait_for(collect_stream(model), 0.5)
        assert events[0] == {"delta": "partial"}
        assert "error" in events[-1] and not any("usage" in event for event in events)
    else:
        with pytest.raises(llm_client.LLMError) as caught:
            await asyncio.wait_for(
                llm_client.complete_chat([], model, max_tokens=None, timeout_seconds=600), 0.5,
            )
        assert caught.value.provider_charge_ambiguous is True and caught.value.retry_safe is False
    assert len(requests) == 1


@pytest.mark.parametrize("model", ["gpt-6.2-sol", "claude-sonnet-5.5"])
@pytest.mark.parametrize("stream", [False, True])
async def test_exhausted_window_during_reservation_never_reaches_post(monkeypatch, model, stream):
    from yleum_api.services.generation import provider_attempts

    clock = virtual_clock(monkeypatch)
    reservations = []

    async def reserve(identity, *, wire_attempt):
        assert identity is None
        reservations.append(wire_attempt)
        clock.seconds += 601.0
        return None

    install_transport(
        monkeypatch, lambda request: pytest.fail("spent deadline must not dispatch a POST"),
    )
    monkeypatch.setattr(provider_attempts, "reserve_generation_wire", reserve)
    if stream:
        events = await collect_stream(model)
        assert len(events) == 1 and "error" in events[0]
    else:
        with pytest.raises(llm_client.LLMError) as caught:
            await llm_client.complete_chat([], model, max_tokens=None, timeout_seconds=600)
        assert caught.value.retry_safe is False
    assert reservations == [0]
