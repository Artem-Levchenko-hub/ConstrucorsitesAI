"""A committed temporary402 waits at the same logical call, never replays tools."""

import asyncio
import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID, uuid5

import httpx
import pytest

from yleum_api.services import agent_builder, agent_native, llm_client
from yleum_api.services.generation import provider_attempts, recovery_reconciliation
from yleum_api.services.generation.provider_identity import GenerationProviderIdentity

URL = "https://gateway.test/v1/chat/completions"
NATIVE_URL = "https://gateway.test/v1/messages"


def evidence(**changes):
    return {
        "upstream_http_status": 402, "provider_refusal_category": "rate_limit",
        "provider_scope": "llmgw", "provider_charge_ambiguous": False,
        "retry_safe": True, "retry_policy": "llmgw_inflight_budget_v1",
        "retry_after_seconds": 120, **changes,
    }


def refusal(*, native=False, details=None):
    error = {"code": "api_error" if native else "model_unavailable",
             "details": evidence() if details is None else details}
    if native:
        error["type"] = "api_error"
    return httpx.Response(
        402 if native else 502, json={"error": error} if native else {"detail": {"error": error}},
        request=httpx.Request("POST", NATIVE_URL if native else URL),
    )


def test_only_complete_committed_budget_contract_grants_wait():
    failure = llm_client._gateway_failure(refusal())
    assert llm_client.inflight_budget_delay(failure) == 120
    assert failure.retry_safe is True and failure.provider_scope == "llmgw"


@pytest.mark.parametrize("changes", [
    {"retry_safe": 1}, {"provider_charge_ambiguous": True},
    {"provider_scope": "aitunnel"}, {"provider_refusal_category": "funds"},
    {"retry_policy": "unknown"}, {"upstream_http_status": 429},
    {"retry_after_seconds": 0}, {"retry_after_seconds": -1},
    {"retry_after_seconds": "120"}, {"retry_after_seconds": True},
    {"retry_after_seconds": 120.0}, {"retry_after_seconds": 86_401},
])
def test_unproven_or_conflicting_budget_is_not_retryable(changes):
    assert llm_client.inflight_budget_delay(llm_client._gateway_failure(
        refusal(details=evidence(**changes)))) is None


def test_duplicate_policy_and_redirect_cannot_grant_wait():
    raw = refusal().content.replace(b'"retry_safe":true', b'"retry_safe":false,"retry_safe":true')
    assert llm_client._gateway_failure(httpx.Response(502, content=raw)).retry_safe is False
    redirected = httpx.Response(502, content=refusal().content,
                               history=[httpx.Response(307)])
    assert llm_client._gateway_failure(redirected).retry_safe is False


def payload():
    return {"model": "claude-sonnet-5.5", "stream": False, "metadata": {},
            "messages": [{"role": "user", "content": "continue saved product"}]}


def identity():
    return GenerationProviderIdentity(*(UUID(int=i) for i in range(1, 5)), "a"*64, "b"*64)


def install(monkeypatch, *, refusals=2, cancel=False, details=None):
    sent, parents, sleeps, waits = [], [], [], []
    root = identity()

    async def prepare(body, route, *, parent=None, existing=None):
        parents.append(parent)
        if parent is None:
            return existing or root
        return GenerationProviderIdentity(
            root.run_id, root.project_id, root.user_id,
            uuid5(parent.attempt_id, "definitive-refusal-retry-v1"),
            root.prompt_hash, root.request_sha256,
        )

    def reply(request):
        sent.append(json.loads(request.content))
        if len(sent) <= refusals:
            return refusal(details=details)
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    async def sleep(delay):
        sleeps.append(delay)
        if cancel:
            raise asyncio.CancelledError

    real_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: real_client(
        transport=httpx.MockTransport(reply), **kw))
    monkeypatch.setattr(provider_attempts, "prepare_generation_attempt", prepare)
    monkeypatch.setattr(llm_client.asyncio, "sleep", sleep)
    return sent, parents, sleeps, waits


async def test_two_refusals_then_success_use_children_and_identical_request(monkeypatch):
    sent, parents, sleeps, waits = install(monkeypatch)

    async def observe(event, data):
        waits.append((event, data["human"]))

    with llm_client.provider_wait_events(observe):
        result = await llm_client._chat_post(payload(), URL, 600)
    assert result.status_code == 200
    assert sleeps == [120, 120] and len(sent) == 3
    identities = [p["metadata"]["generation_provider_identity"] for p in sent]
    assert len({p["attempt_id"] for p in identities}) == 3
    assert all(p["run_id"] == str(identity().run_id) for p in identities)
    assert parents[0] is None and parents[1].attempt_id == UUID(identities[0]["attempt_id"])
    assert parents[2].attempt_id == UUID(identities[1]["attempt_id"])
    for key in ("messages", "model", "stream"):
        assert all(p[key] == sent[0][key] for p in sent)
    assert waits == [("agent.wait", "Жду освобождения бюджета модели")] * 2
    assert llm_client._provider_wait_observer.get() is None


async def test_delay_does_not_fit_no_child_no_early_retry(monkeypatch):
    sent, parents, sleeps, _ = install(monkeypatch)
    with pytest.raises(llm_client.LLMError) as exc:
        await llm_client._chat_post(payload(), URL, 30)
    assert exc.value.retry_safe is False
    assert len(sent) == 1 and parents == [None] and sleeps == []


async def test_cancel_during_wait_does_not_dispatch_child(monkeypatch):
    sent, parents, sleeps, _ = install(monkeypatch, cancel=True)
    observer = AsyncMock()
    with pytest.raises(asyncio.CancelledError), llm_client.provider_wait_events(observer):
        await llm_client._chat_post(payload(), URL, 600)
    assert len(sent) == 1 and parents == [None] and sleeps == [120]
    assert llm_client._provider_wait_observer.get() is None


async def test_ordinary_funds_402_is_not_replayed(monkeypatch):
    sent, parents, sleeps, _ = install(monkeypatch, details={
        "upstream_http_status": 402, "provider_refusal_category": "funds",
        "provider_charge_ambiguous": False,
    })
    with pytest.raises(llm_client.LLMError):
        await llm_client._chat_post(payload(), URL, 600)
    assert len(sent) == 1 and parents == [None] and sleeps == []


async def test_retry_allowance_exhaustion_cannot_reenter_closed_root(monkeypatch):
    sent, parents, sleeps, _ = install(monkeypatch, refusals=20)
    with pytest.raises(llm_client.LLMError) as exc:
        await llm_client._chat_post(payload(), URL, 600)
    assert exc.value.retry_safe is False
    assert len(sent) == 4 and len(sleeps) == 3
    assert all(delay == 120 for delay in sleeps) and parents[0] is None


async def test_overslept_wait_does_not_acquire_child(monkeypatch):
    sent, parents, _, _ = install(monkeypatch)
    clock = [0.0]

    async def sleep(_delay):
        clock[0] = 601

    @asynccontextmanager
    async def no_timeout(_deadline):
        yield

    monkeypatch.setattr(llm_client, "asyncio", SimpleNamespace(
        get_running_loop=lambda: SimpleNamespace(time=lambda: clock[0]),
        sleep=sleep, timeout_at=no_timeout,
    ))
    with pytest.raises(llm_client.LLMError):
        await llm_client._chat_post(payload(), URL, 600)
    assert len(sent) == 1 and parents == [None]


async def test_cold_refusal_preserves_clock_and_reads_then_dispatches_child(monkeypatch):
    sent, parents, sleeps, _ = install(monkeypatch, refusals=0)
    monkeypatch.setattr(recovery_reconciliation, "provider_reconciliation_requested", lambda: True)
    monkeypatch.setattr(recovery_reconciliation, "provider_dispatch_remaining", lambda: 300)
    monkeypatch.setattr(llm_client, "require_recovered_response", AsyncMock(
        side_effect=llm_client._gateway_failure(refusal())))
    resumed = AsyncMock(return_value=(1, True))
    monkeypatch.setattr(provider_attempts, "resume_generation_wire", resumed)
    assert (await llm_client._chat_post(payload(), URL, 600)).status_code == 200
    resumed.assert_awaited_once_with(identity(), allow_provider_refusal=True)
    assert parents == [None, identity()] and sleeps == [120] and len(sent) == 1
    actual = sent[0]["metadata"]["generation_provider_identity"]
    assert actual["attempt_id"] != str(identity().attempt_id)


async def test_native_waits_two_120_second_refusals_then_continues(monkeypatch):
    sent, sleeps = [], []

    def reply(request):
        sent.append(json.loads(request.content))
        if len(sent) <= 2:
            return refusal(native=True)
        return httpx.Response(200, json={"content": [], "stop_reason": "end_turn"})

    async def sleep(delay):
        sleeps.append(delay)

    monkeypatch.setattr(agent_native.asyncio, "sleep", sleep)
    async with httpx.AsyncClient(transport=httpx.MockTransport(reply)) as client:
        assert await agent_native._call_messages(client, NATIVE_URL, [], "system") == {
            "content": [], "stop_reason": "end_turn"}
    assert sleeps == [120, 120] and len(sent) == 3
    assert all(p["messages"] == sent[0]["messages"] for p in sent)


async def test_agent_does_not_replay_tool_before_budget_wait(monkeypatch):
    sent, executed, sleeps = [], [], []
    replies = [
        '<omnia:action name="write_file">'
        '{"path":"src/app/page.tsx","content":"saved"}</omnia:action>',
        None,
        '<omnia:action name="read_file">{"path":"src/app/page.tsx"}</omnia:action>',
    ]

    async def complete(*args, **kwargs):
        sent.append(args[0])
        value = replies[len(sent)-1]
        if value is None:
            raise llm_client._gateway_failure(refusal())
        return value

    async def execute(action):
        executed.append(action.name)
        return {"ok": True, "content": "saved"}

    async def sleep(delay):
        if delay:
            assert executed == ["write_file"]
            sleeps.append(delay)

    monkeypatch.setattr(agent_builder.asyncio, "sleep", sleep)
    await agent_builder.run_agent_build(
        system_prompt="system", user_prompt="continue", model="synthetic", complete=complete,
        execute=execute, max_steps=2,
    )
    assert executed == ["write_file", "read_file"] and sleeps == [120]
    assert sent[1] == sent[2]


async def test_stream_http_refusal_waits_before_any_outward_event(monkeypatch):
    real_client = httpx.AsyncClient
    _, parents, sleeps, _ = install(monkeypatch)
    sent = []

    def reply(request):
        sent.append(json.loads(request.content))
        if len(sent) <= 2:
            return refusal()
        return httpx.Response(
            200, text='data: {"choices":[{"delta":{"content":"ok"}}]}\n\ndata: [DONE]\n\n',
        )

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: real_client(
        transport=httpx.MockTransport(reply), **kw))
    request = {**payload(), "stream": True}
    events = [event async for event in llm_client._chat_stream_data(
        request, URL, identity(), asyncio.get_running_loop().time()+600,
    )]
    assert events == [{"choices": [{"delta": {"content": "ok"}}]}]
    assert sleeps == [120, 120] and len(sent) == 3 and len(parents) == 2
    assert sent[0]["metadata"]["generation_provider_identity"] == identity().to_wire()


async def test_actual_retained_native_402_is_read_then_waits_and_continues(monkeypatch):
    from yleum_api.services.generation import response_auth

    root = identity()
    child = GenerationProviderIdentity(
        root.run_id, root.project_id, root.user_id, UUID(int=9),
        root.prompt_hash, root.request_sha256,
    )
    parents, requests, sleeps = [], [], []
    reconciling = [True]

    async def prepare(body, route, *, existing=None, parent=None):
        parents.append(parent)
        return child if parent is not None else root

    async def resume(original, *, allow_provider_refusal):
        assert original == root and allow_provider_refusal is True
        reconciling[0] = False
        return 1, True

    def reply(request):
        requests.append((request.method, request.url.path))
        if request.method == "GET":
            return httpx.Response(200, json={
                "version": 1, "identity": root.to_wire(), "state": "stored",
                "response": {"format": "json", "status": 402, "body": refusal(native=True).json()},
            })
        assert json.loads(request.content)["metadata"]["generation_provider_identity"] == (
            child.to_wire())
        return httpx.Response(200, json={"content": [], "stop_reason": "end_turn"})

    async def sleep(delay):
        sleeps.append(delay)

    monkeypatch.setattr(provider_attempts, "prepare_generation_attempt", prepare)
    monkeypatch.setattr(provider_attempts, "resume_generation_wire", resume)
    monkeypatch.setattr(recovery_reconciliation, "provider_reconciliation_requested",
                        lambda: reconciling[0])
    monkeypatch.setattr(recovery_reconciliation, "provider_dispatch_remaining", lambda: 260)
    monkeypatch.setattr(llm_client, "get_settings", lambda: SimpleNamespace(
        generation_response_retention_enabled=True, llm_gateway_url="https://gateway.test"))
    monkeypatch.setattr(response_auth, "mint_generation_response_token", lambda *a, **kw: "test")
    monkeypatch.setattr(agent_native.asyncio, "sleep", sleep)
    real_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: real_client(
        transport=httpx.MockTransport(reply), **kw))
    async with httpx.AsyncClient() as client:
        assert await agent_native._call_messages(client, NATIVE_URL, [], "system") == {
            "content": [], "stop_reason": "end_turn"}
    assert requests == [("GET", f"/_internal/generation-responses/{root.attempt_id}"),
                        ("POST", "/v1/messages")]
    assert parents == [None, root] and sleeps == [120]
