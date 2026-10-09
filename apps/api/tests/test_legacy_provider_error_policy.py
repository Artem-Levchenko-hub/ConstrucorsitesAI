"""Actual nonstream gateway client -> legacy agent retry/failure boundary."""

from types import SimpleNamespace

import httpx
import pytest

from yleum_api.services import agent_builder, llm_client


def install_transport(monkeypatch, handler):
    original = httpx.AsyncClient
    monkeypatch.setattr(llm_client, "get_settings", lambda: SimpleNamespace(
        mock_llm=False, llm_gateway_url="http://gateway.test",
    ))
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: original(
        transport=httpx.MockTransport(handler), **kwargs,
    ))


def rejection(details=None, code="model_unavailable"):
    return httpx.Response(502, json={"detail": {"error": {
        "code": code,
        "message": "synthetic-private-key https://private.test/secret",
        "details": details or {},
    }}})


async def run_agent(monkeypatch, **kwargs):
    sleeps, events = [], []

    async def sleep(delay):
        sleeps.append(delay)

    async def emit(kind, payload):
        events.append((kind, payload))

    async def execute(action):
        return {"ok": True}

    monkeypatch.setattr(agent_builder.asyncio, "sleep", sleep)
    result = await agent_builder.run_agent_build(
        system_prompt="s", user_prompt="edit", model="m", max_steps=3,
        complete=llm_client.complete_chat, execute=execute, emit=emit, **kwargs,
    )
    return result, sleeps, events


@pytest.mark.parametrize("status", [401, 403, 429, 500, 502])
async def test_ambiguous_http_failure_is_one_call_with_safe_terminal_cause(monkeypatch, status):
    calls = []

    def handler(request):
        calls.append(request)
        return rejection({"upstream_http_status": status, "provider_charge_ambiguous": True})

    install_transport(monkeypatch, handler)
    result, sleeps, events = await run_agent(monkeypatch)
    assert len(calls) == 1
    assert sleeps == []
    assert not result.done and not result.needs_finalization
    assert result.stop_reason == "provider_error"
    assert str(status) in result.summary
    assert "synthetic-private-key" not in result.summary and "private.test" not in result.summary
    assert not any(kind == "agent.retry" for kind, _ in events)
    diagnostic = [payload for kind, payload in events if kind == "agent.provider_error"]
    assert len(diagnostic) == 1
    assert diagnostic[0]["upstream_http_status"] == status
    assert diagnostic[0]["provider_charge_ambiguous"] is True
    from yleum_api.services.generation_failure import failure_for_error

    public = failure_for_error(result.summary)
    assert public.code == ("provider_access" if status in {401, 403} else "provider_unavailable")
    assert public.retryable is False
    assert "synthetic-private-key" not in public.model_dump_json()


@pytest.mark.parametrize("details", [
    {},
    {"provider_charge_ambiguous": False},
    {"provider_charge_ambiguous": "false", "provider_failure_kind": "preconnect"},
    {"provider_charge_ambiguous": 0, "provider_failure_kind": "preconnect"},
    {"provider_charge_ambiguous": False, "provider_failure_kind": "preconnect",
     "upstream_http_status": 401},
])
async def test_absent_malformed_or_contradictory_retry_proof_does_not_replay(monkeypatch, details):
    calls = []

    def handler(request):
        calls.append(request)
        return rejection(details)

    install_transport(monkeypatch, handler)
    result, sleeps, _ = await run_agent(monkeypatch)
    assert len(calls) == 1 and sleeps == []
    assert result.stop_reason == "provider_error"


@pytest.mark.parametrize("kind", [httpx.ReadTimeout, httpx.WriteTimeout, httpx.RemoteProtocolError])
async def test_ambiguous_transport_failure_is_not_replayed(monkeypatch, kind):
    calls = []

    def handler(request):
        calls.append(request)
        raise kind("synthetic-private-key", request=request)

    install_transport(monkeypatch, handler)
    result, sleeps, _ = await run_agent(monkeypatch)
    assert len(calls) == 1 and sleeps == []
    assert result.stop_reason == "provider_error"
    assert "synthetic-private-key" not in result.summary


@pytest.mark.parametrize("kind", [httpx.ConnectError, httpx.ConnectTimeout])
async def test_preconnect_failure_can_retry_without_final_unused_sleep(monkeypatch, kind):
    calls = []

    def handler(request):
        calls.append(request)
        raise kind("synthetic-private-key", request=request)

    install_transport(monkeypatch, handler)
    result, sleeps, events = await run_agent(monkeypatch)
    assert len(calls) == 5
    assert sleeps == [4.0, 8.0, 12.0, 16.0]
    assert len([e for e in events if e[0] == "agent.retry"]) == 4
    assert result.stop_reason == "provider_error"
    assert "synthetic-private-key" not in result.summary


@pytest.mark.parametrize("status", [True, "401", 401.0, 399, 600, None])
async def test_invalid_upstream_status_is_not_promoted_to_trusted_metadata(monkeypatch, status):
    install_transport(monkeypatch, lambda request: rejection({
        "upstream_http_status": status, "provider_charge_ambiguous": True,
    }))
    with pytest.raises(llm_client.LLMError) as failed:
        await llm_client.complete_chat([], "m")
    assert getattr(failed.value, "upstream_http_status", "missing") is None
    assert "synthetic-private-key" not in str(failed.value)


async def test_unknown_complete_exception_is_not_blindly_replayed(monkeypatch):
    calls = []

    async def sleep(delay):
        pass

    monkeypatch.setattr(agent_builder.asyncio, "sleep", sleep)

    async def complete(*args, **kwargs):
        calls.append(1)
        raise RuntimeError("synthetic-private-key")

    async def execute(action):
        return {"ok": True}

    result = await agent_builder.run_agent_build(
        system_prompt="s", user_prompt="x", model="m", execute=execute,
        complete=complete, max_steps=2,
    )
    assert len(calls) == 1
    assert result.stop_reason == "provider_error"
    assert "synthetic-private-key" not in result.summary


async def test_proven_gateway_preconnect_can_retry_and_finish(monkeypatch):
    calls = []

    def handler(request):
        calls.append(request)
        if len(calls) == 1:
            return rejection({
                "provider_charge_ambiguous": False, "provider_failure_kind": "preconnect",
            })
        return httpx.Response(200, json={"choices": [{"message": {
            "content": '<omnia:action name="done">{"summary":"ok"}</omnia:action>',
        }}]})

    install_transport(monkeypatch, handler)
    result, sleeps, events = await run_agent(monkeypatch)
    assert result.done
    assert len(calls) == 2 and sleeps == [4.0]
    assert len([e for e in events if e[0] == "agent.retry"]) == 1
    assert "synthetic-private-key" not in repr(events)


async def test_provider_failure_after_green_is_not_salvaged(monkeypatch):
    install_transport(monkeypatch, lambda request: rejection({
        "upstream_http_status": 401, "provider_charge_ambiguous": True,
    }))
    calls = []

    async def complete(*args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            return '<omnia:action name="build"></omnia:action>'
        return await llm_client.complete_chat(*args, **kwargs)

    async def execute(action):
        return {"ok": True}

    async def sleep(delay):
        pass

    monkeypatch.setattr(agent_builder.asyncio, "sleep", sleep)
    result = await agent_builder.run_agent_build(
        system_prompt="s", user_prompt="edit", model="m", complete=complete,
        execute=execute, max_steps=3, coordinator_handoff=True,
    )
    assert len(calls) == 2
    assert not result.done and not result.needs_finalization
    assert result.stop_reason == "provider_error"


async def test_cancellation_propagates_without_retry_or_failure_conversion(monkeypatch):
    import asyncio

    async def complete(*args, **kwargs):
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await agent_builder.run_agent_build(
            system_prompt="s", user_prompt="edit", model="m", complete=complete,
            execute=None, max_steps=2,
        )


@pytest.mark.parametrize("response", [
    httpx.Response(502, text="synthetic-private-key"),
    httpx.Response(502, json={"detail": "synthetic-private-key"}),
    httpx.Response(502, json={"detail": {"error": {"code": "synthetic-private-key"}}}),
])
async def test_malformed_or_unknown_gateway_error_is_not_replayed_or_exposed(
    monkeypatch, response,
):
    calls = []

    def handler(request):
        calls.append(request)
        return response

    install_transport(monkeypatch, handler)
    result, sleeps, _ = await run_agent(monkeypatch)
    assert len(calls) == 1 and sleeps == []
    assert result.stop_reason == "provider_error"
    assert "synthetic-private-key" not in result.summary
