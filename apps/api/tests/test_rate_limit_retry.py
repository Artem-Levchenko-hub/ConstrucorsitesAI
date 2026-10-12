"""Only durable, documented provider refusals may replay a paid POST."""

import asyncio
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import httpx
import pytest

from yleum_api.services import agent_builder, agent_native, llm_client

from .test_legacy_provider_error_policy import install_transport


def evidence(**changes):
    return {
        "upstream_http_status": 429,
        "provider_refusal_category": "rate_limit",
        "provider_charge_ambiguous": False,
        "retry_safe": True,
        "retry_policy": "llmgw_key_rate_limit_v1",
        "retry_after_seconds": 17,
        **changes,
    }


def classic_failure(details):
    return llm_client._gateway_failure(httpx.Response(502, json={
        "detail": {"error": {"code": "model_unavailable", "details": details}},
    }))


def test_classic_accepts_complete_rate_rejection_contract():
    failure = classic_failure(evidence())
    assert failure.retry_safe is True
    assert failure.retry_after_seconds == 17


def test_absent_retry_after_uses_existing_backoff():
    details = evidence()
    details.pop("retry_after_seconds")
    failure = classic_failure(details)
    assert failure.retry_safe is True and failure.retry_after_seconds == 0


@pytest.mark.parametrize("status,code", [
    (429, "model_unavailable"), (502, "billing_reconciliation_required"),
])
def test_wrong_classic_envelope_cannot_authorize_retry(status, code):
    failure = llm_client._gateway_failure(httpx.Response(status, json={
        "detail": {"error": {"code": code, "details": evidence()}},
    }))
    assert failure.retry_safe is False


@pytest.mark.parametrize("changes", [
    {"retry_safe": False}, {"retry_safe": 1}, {"retry_policy": "untrusted"},
    {"provider_charge_ambiguous": True}, {"provider_charge_ambiguous": 0},
    {"upstream_http_status": 401}, {"provider_refusal_category": "funds"},
    {"retry_after_seconds": -1}, {"retry_after_seconds": True},
    {"retry_after_seconds": 17.0}, {"retry_after_seconds": "17"},
    {"retry_after_seconds": 86401}, {"retry_after_seconds": None},
])
def test_classic_inconsistent_evidence_is_terminal(changes):
    assert classic_failure(evidence(**changes)).retry_safe is False


async def native_call(
    monkeypatch, details, *, window=270, cancel=False, deadline=None, error_extra=None,
):
    calls, sleeps = [], []
    success = {"content": [], "stop_reason": "end_turn"}

    def reply(request):
        calls.append(request)
        if len(calls) == 2:
            return httpx.Response(200, json=success)
        return httpx.Response(429, json={
            "error": {"type": "api_error", "details": details, **(error_extra or {})},
        })

    async def sleep(delay):
        sleeps.append(delay)
        if cancel:
            raise asyncio.CancelledError

    monkeypatch.setattr(agent_native.asyncio, "sleep", sleep)
    monkeypatch.setattr(agent_native, "_CALL_RETRY_WINDOW_S", window)
    async with httpx.AsyncClient(transport=httpx.MockTransport(reply)) as client:
        try:
            result = await agent_native._call_messages(
                client, "https://gateway.test/v1/messages", [], "s", edit_deadline=deadline,
            )
        except (RuntimeError, asyncio.CancelledError) as exc:
            result = exc
    return result, calls, sleeps


async def test_native_confirmed_429_waits_17_then_retries_once(monkeypatch):
    result, calls, sleeps = await native_call(monkeypatch, evidence())
    assert result == {"content": [], "stop_reason": "end_turn"}
    assert len(calls) == 2
    assert sleeps == [17]


@pytest.mark.parametrize("details", [
    {}, {"upstream_http_status": 429, "provider_refusal_category": "rate_limit"},
    evidence(provider_charge_ambiguous=True), evidence(retry_after_seconds="17"),
    evidence(retry_policy="unknown"),
])
async def test_native_unknown_refusal_never_replays(monkeypatch, details):
    result, calls, sleeps = await native_call(monkeypatch, details)
    assert isinstance(result, RuntimeError)
    assert len(calls) == 1 and sleeps == []


async def test_native_retry_after_must_fit_window_without_early_clamping(monkeypatch):
    result, calls, sleeps = await native_call(monkeypatch, evidence(), window=10)
    assert isinstance(result, RuntimeError)
    assert len(calls) == 1 and sleeps == []


async def test_native_cancel_during_wait_never_replays(monkeypatch):
    result, calls, sleeps = await native_call(monkeypatch, evidence(), cancel=True)
    assert isinstance(result, asyncio.CancelledError)
    assert len(calls) == 1 and sleeps == [17]


async def test_native_retry_after_must_fit_outer_edit_deadline(monkeypatch):
    result, calls, sleeps = await native_call(
        monkeypatch, evidence(), deadline=datetime.now(UTC) + timedelta(seconds=10),
    )
    assert isinstance(result, RuntimeError)
    assert len(calls) == 1 and sleeps == []


async def test_native_conflicting_error_code_cannot_authorize_retry(monkeypatch):
    result, calls, sleeps = await native_call(
        monkeypatch, evidence(), error_extra={"code": "billing_unavailable"},
    )
    assert isinstance(result, RuntimeError)
    assert len(calls) == 1 and sleeps == []


@pytest.mark.parametrize("deadline_seconds,cancel", [(None, False), (10, False), (None, True)])
async def test_classic_retry_wait_and_deadline(monkeypatch, deadline_seconds, cancel):
    calls, sleeps, executed = [], [], []

    async def complete(*args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise classic_failure(evidence())
        return '<omnia:action name="read_file">{"path":"src/app/page.tsx"}</omnia:action>'

    async def execute(action):
        executed.append(action.name)
        return {"ok": True, "content": "existing"}

    async def sleep(delay):
        if delay:
            sleeps.append(delay)
            if cancel:
                raise asyncio.CancelledError

    monkeypatch.setattr(agent_builder.asyncio, "sleep", sleep)
    deadline = datetime.now(UTC) + timedelta(seconds=deadline_seconds) if deadline_seconds else None
    try:
        result = await agent_builder.run_agent_build(
            system_prompt="s", user_prompt="inspect", model="m", complete=complete,
            execute=execute, max_steps=1, edit_deadline=deadline,
        )
    except asyncio.CancelledError as exc:
        result = exc
    if deadline_seconds:
        assert len(calls) == 1 and sleeps == [] and executed == []
    elif cancel:
        assert isinstance(result, asyncio.CancelledError)
        assert len(calls) == 1 and sleeps == [17] and executed == []
    else:
        assert len(calls) == 2 and sleeps == [17] and executed == ["read_file"]


async def test_classic_overslept_retry_window_never_dispatches_again(monkeypatch):
    clock, calls = [0.0], []

    async def sleep(delay):
        if delay:
            clock[0] += 300  # event-loop scheduling may resume later than requested

    async def complete(*args, **kwargs):
        calls.append(1)
        raise classic_failure(evidence())

    async def execute(action):
        pytest.fail("a refused model request cannot execute a tool")

    monkeypatch.setattr(agent_builder, "asyncio", SimpleNamespace(
        sleep=sleep, get_running_loop=lambda: SimpleNamespace(time=lambda: clock[0]),
        timeout=asyncio.timeout,
    ))
    result = await agent_builder.run_agent_build(
        system_prompt="s", user_prompt="inspect", model="m", complete=complete,
        execute=execute, max_steps=1,
    )
    assert len(calls) == 1
    assert result.done is False and result.stop_reason == "provider_error"


async def test_real_classic_http_path_retries_once_without_losing_attribution(monkeypatch):
    requests, sleeps, executed = [], [], []

    def handler(request):
        requests.append(json.loads(request.content))
        if len(requests) == 1:
            return httpx.Response(502, json={"detail": {"error": {
                "code": "model_unavailable", "details": evidence(),
            }}})
        return httpx.Response(200, json={"choices": [{
            "finish_reason": "stop", "message": {
                "content": (
                    '<omnia:action name="read_file">{"path":"src/app/page.tsx"}</omnia:action>'
                ),
            },
        }]})

    async def execute(action):
        executed.append(action.name)
        return {"ok": True, "content": "existing"}

    async def sleep(delay):
        if delay:
            sleeps.append(delay)

    install_transport(monkeypatch, handler)
    monkeypatch.setattr(agent_builder.asyncio, "sleep", sleep)
    token = llm_client._generation_billing_context.set(("run-test", "user-test", "project-test"))
    try:
        await agent_builder.run_agent_build(
            system_prompt="s", user_prompt="inspect", model="m", execute=execute,
            user_id="user-test", project_id="project-test", max_steps=1,
        )
    finally:
        llm_client._generation_billing_context.reset(token)
    assert len(requests) == 2 and sleeps == [17] and executed == ["read_file"]
    assert requests[0] == requests[1]
    assert requests[1]["metadata"]["run_id"] == "run-test"
    assert requests[1]["metadata"]["project_id"] == "project-test"


async def test_classic_active_retry_is_cancelled_at_window_end(monkeypatch):
    clock, calls, cancelled, executed = [0.0], [], [], []

    async def sleep(delay):
        if delay:
            clock[0] = 269.98

    async def complete(*args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise classic_failure(evidence())
        try:
            await asyncio.sleep(0.1)
        except asyncio.CancelledError:
            cancelled.append(True)
            raise
        return '<omnia:action name="read_file">{"path":"src/app/page.tsx"}</omnia:action>'

    async def execute(action):
        executed.append(action.name)
        return {"ok": True}

    monkeypatch.setattr(agent_builder, "asyncio", SimpleNamespace(
        sleep=sleep, get_running_loop=lambda: SimpleNamespace(time=lambda: clock[0]),
        timeout=asyncio.timeout,
    ))
    result = await agent_builder.run_agent_build(
        system_prompt="s", user_prompt="inspect", model="m", complete=complete,
        execute=execute, max_steps=1,
    )
    assert cancelled == [True]
    assert len(calls) == 2 and executed == []
    assert result.done is False and result.stop_reason == "provider_error"
