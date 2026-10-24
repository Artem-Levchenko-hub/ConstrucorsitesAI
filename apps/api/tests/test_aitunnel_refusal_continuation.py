"""A primary refusal permits one owned child; unknown outcomes never replay."""

import json
from unittest.mock import AsyncMock
from uuid import UUID

import httpx
import pytest

from yleum_api.services import agent_native, llm_client
from yleum_api.services.generation import provider_attempts
from yleum_api.services.generation.provider_identity import GenerationProviderIdentity

URL = "https://gateway.test/v1/messages"
PROOF = {
    "provider_failure_kind": "upstream_refused",
    "provider_charge_ambiguous": False,
    "retry_safe": True,
    "retry_policy": "llmgw_overload_v1",
    "upstream_http_status": 502,
}


def refusal(native=True, changes=None, status=503):
    error = {"code": "api_error", "details": {**PROOF, **(changes or {})}}
    if native:
        error["type"] = "api_error"
    body = {"error": error}
    return httpx.Response(
        status,
        json=body if native else {"detail": body},
        request=httpx.Request(
            "POST", URL if native else "https://gateway.test/v1/chat/completions"
        ),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("repeat", [False, True])
@pytest.mark.parametrize("preconnect", [False, True])
async def test_native_primary_overload_creates_exactly_one_child(monkeypatch, repeat, preconnect):
    root = GenerationProviderIdentity(*(UUID(int=i) for i in range(1, 5)), "a" * 64, "b" * 64)
    child = GenerationProviderIdentity(*(UUID(int=i) for i in (1, 2, 3, 5)), "a" * 64, "b" * 64)
    prepared, sent = [], []

    async def prepare(body, route, *, existing=None, parent=None):
        prepared.append(parent)
        return child if parent else existing or root

    def reply(request):
        sent.append(json.loads(request.content))
        if preconnect and len(sent) == 1:
            raise httpx.ConnectError("synthetic", request=request)
        if len(sent) == (2 if preconnect else 1) or repeat:
            return refusal(changes={
                "retry_policy": "llmgw_unavailable_v1", "upstream_http_status": 500,
            })
        return httpx.Response(200, json={"content": [], "stop_reason": "end_turn"})

    monkeypatch.setattr(provider_attempts, "prepare_generation_attempt", prepare)
    monkeypatch.setattr(agent_native.asyncio, "sleep", AsyncMock())
    async with httpx.AsyncClient(transport=httpx.MockTransport(reply)) as client:
        if repeat:
            with pytest.raises(RuntimeError):
                await agent_native._call_messages(client, URL, [], "s")
        else:
            await agent_native._call_messages(client, URL, [], "s")
    assert len(sent) == (3 if preconnect else 2)
    assert sent[0]["metadata"]["generation_provider_identity"] == root.to_wire()
    assert sent[-1]["metadata"]["generation_provider_identity"] == child.to_wire()
    assert prepared == ([None, None, root] if preconnect else [None, root])


@pytest.mark.parametrize("changes", [{}, {"provider_call_id": str(UUID(int=9))}])
def test_chat_positive_policy_is_retained_as_safe_refusal(changes):
    response = refusal(native=False, changes=changes)
    failure = llm_client._gateway_failure(response)
    assert failure.retry_safe is True and failure.provider_charge_ambiguous is False
    assert failure.code == "upstream_refused" and failure.upstream_http_status == 502


@pytest.mark.parametrize(
    "changes",
    [
        {"retry_safe": 1},
        {"provider_charge_ambiguous": True},
        {"retry_policy": "aitunnel_overload_v1"},
        {"upstream_http_status": 503},
        {"upstream_http_status": True},
        {"provider_call_id": "not-uuid"},
        {"cost": 0},
    ],
)
def test_untrusted_or_conflicting_policy_never_safe(changes):
    failure = llm_client._gateway_failure(refusal(native=False, changes=changes))
    assert failure.retry_safe is False


@pytest.mark.asyncio
async def test_sql_parent_accepts_only_first_primary_refusal():
    root = GenerationProviderIdentity(*(UUID(int=i) for i in range(1, 5)), "a" * 64, "b" * 64)

    class Result:
        def __init__(self, row):
            self.row = row

        def mappings(self):
            return self

        def first(self):
            return self.row

    class Session:
        def __init__(self, parent):
            self.parent = parent

        async def execute(self, query, args):
            assert "upstream_refused" in str(query)
            return Result(
                {
                    key: getattr(root, key)
                    for key in ("run_id", "project_id", "user_id", "prompt_hash", "request_sha256")
                }
                | {
                    "terminal_reason": "upstream_refused",
                    "parent_attempt_id": self.parent,
                    "provider_scope": "llmgw",
                    "prior_overload": self.parent is not None,
                }
            )

    await provider_attempts.require_closed_provider_parent(Session(None), root, root)
    with pytest.raises(ValueError):
        await provider_attempts.require_closed_provider_parent(Session(UUID(int=10)), root, root)


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("repeat", [False, True])
async def test_chat_overload_http_uses_one_child(monkeypatch, stream, repeat):
    from types import SimpleNamespace

    root = GenerationProviderIdentity(*(UUID(int=i) for i in range(1, 5)), "a" * 64, "b" * 64)
    child = GenerationProviderIdentity(*(UUID(int=i) for i in (1, 2, 3, 5)), "a" * 64, "b" * 64)
    sent = []

    async def prepare(body, route, *, existing=None, parent=None):
        return child if parent else existing or root

    def reply(request):
        sent.append(json.loads(request.content))
        if len(sent) == 1 or repeat:
            return refusal(native=False)
        if stream:
            return httpx.Response(
                200, text='data: {"choices":[{"delta":{"content":"ok"}}]}\n\ndata: [DONE]\n\n'
            )
        return httpx.Response(
            200, json={"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}]}
        )

    real = httpx.AsyncClient
    monkeypatch.setattr(
        httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(reply), **kw)
    )
    monkeypatch.setattr(provider_attempts, "prepare_generation_attempt", prepare)
    monkeypatch.setattr(
        llm_client,
        "get_settings",
        lambda: SimpleNamespace(mock_llm=False, llm_gateway_url="https://gateway.test"),
    )
    monkeypatch.setattr(llm_client.asyncio, "sleep", AsyncMock())
    if stream:
        result = [
            event
            async for event in llm_client.stream_chat_completion(
                [], "claude-sonnet-5.5", "u", "p", "m"
            )
        ]
        assert bool(result[-1].get("error")) is repeat
    elif repeat:
        with pytest.raises(llm_client.LLMError):
            await llm_client.complete_chat([], "claude-sonnet-5.5", max_tokens=None)
    else:
        assert await llm_client.complete_chat([], "claude-sonnet-5.5", max_tokens=None) == "ok"
    assert len(sent) == 2
    assert sent[1]["metadata"]["generation_provider_identity"] == child.to_wire()


@pytest.mark.parametrize("native", [False, True])
def test_duplicate_fields_and_redirect_never_grant_refusal(native):
    response = refusal(native=native)
    body = response.content.replace(b'"retry_safe":true', b'"retry_safe":true,"retry_safe":true')
    assert body != response.content
    duplicate = httpx.Response(503, content=body, request=response.request)
    if native:
        assert not agent_native._gateway_overload_refused(duplicate, URL)
    else:
        assert not llm_client._response_overload_refused(duplicate)
    response.history = [httpx.Response(307)]
    assert not llm_client._response_overload_refused(response)


@pytest.mark.asyncio
@pytest.mark.parametrize("first", [None, "role", "content", "tool_calls", "usage"])
async def test_stream_cutover_only_before_any_outward_event(monkeypatch, first):
    from types import SimpleNamespace

    root = GenerationProviderIdentity(*(UUID(int=i) for i in range(1, 5)), "a" * 64, "b" * 64)
    child = GenerationProviderIdentity(*(UUID(int=i) for i in (1, 2, 3, 5)), "a" * 64, "b" * 64)
    sent = []

    async def prepare(body, route, *, existing=None, parent=None):
        return child if parent else existing or root

    def reply(request):
        sent.append(json.loads(request.content))
        documents = []
        if len(sent) == 1:
            if first == "usage":
                documents.append({"usage": {"prompt_tokens": 1, "completion_tokens": 1}})
            elif first is not None:
                documents.append({"choices": [{"delta": {first: "accepted"}}]})
            documents.append({"error": {"code": "api_error", "details": PROOF}})
        else:
            documents.append({"choices": [{"delta": {"content": "ok"}}]})
        return httpx.Response(
            200,
            text="".join("data: " + json.dumps(v) + "\n\n" for v in documents) + "data: [DONE]\n\n",
        )

    real = httpx.AsyncClient
    monkeypatch.setattr(
        httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(reply), **kw)
    )
    monkeypatch.setattr(provider_attempts, "prepare_generation_attempt", prepare)
    monkeypatch.setattr(
        llm_client,
        "get_settings",
        lambda: SimpleNamespace(mock_llm=False, llm_gateway_url="https://gateway.test"),
    )
    monkeypatch.setattr(llm_client.asyncio, "sleep", AsyncMock())
    result = [
        item
        async for item in llm_client.stream_chat_completion([], "claude-sonnet-5.5", "u", "p", "m")
    ]
    assert len(sent) == (2 if first is None else 1)
    if first is None:
        assert {"delta": "ok"} in result


@pytest.mark.asyncio
async def test_cold_chat_refusal_reads_same_parent_then_sends_one_child(monkeypatch):
    from types import SimpleNamespace

    from yleum_api.services.generation import recovery_reconciliation as rec

    root = GenerationProviderIdentity(*(UUID(int=i) for i in range(1, 5)), "a" * 64, "b" * 64)
    child = GenerationProviderIdentity(*(UUID(int=i) for i in (1, 2, 3, 5)), "a" * 64, "b" * 64)
    parents, sent = [], []

    async def prepare(body, route, *, existing=None, parent=None):
        parents.append(parent)
        return child if parent else root

    def reply(request):
        sent.append(json.loads(request.content))
        return httpx.Response(
            200, json={"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}]}
        )

    real = httpx.AsyncClient
    monkeypatch.setattr(
        httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(reply), **kw)
    )
    monkeypatch.setattr(provider_attempts, "prepare_generation_attempt", prepare)
    monkeypatch.setattr(rec, "provider_reconciliation_requested", lambda: True)
    monkeypatch.setattr(rec, "provider_dispatch_remaining", lambda: 20)
    resumed = AsyncMock(return_value=(1, True))
    monkeypatch.setattr(provider_attempts, "resume_generation_wire", resumed)
    failure = llm_client._gateway_failure(refusal(native=False))
    monkeypatch.setattr(llm_client, "require_recovered_response", AsyncMock(side_effect=failure))
    monkeypatch.setattr(
        llm_client,
        "get_settings",
        lambda: SimpleNamespace(mock_llm=False, llm_gateway_url="https://gateway.test"),
    )
    monkeypatch.setattr(llm_client.asyncio, "sleep", AsyncMock())
    assert await llm_client.complete_chat([], "claude-sonnet-5.5", max_tokens=None) == "ok"
    resumed.assert_awaited_once_with(root, allow_provider_refusal=True)
    assert parents == [None, root]
    assert len(sent) == 1
    assert sent[0]["metadata"]["generation_provider_identity"] == child.to_wire()


def test_duplicate_or_charged_sse_refusal_does_not_grant_child():
    clean = json.dumps({"error": {"code": "api_error", "details": PROOF}})
    assert llm_client._stream_overload_refused(clean)
    duplicate = clean.replace('"retry_safe": true', '"retry_safe": true, "retry_safe": true')
    assert not llm_client._stream_overload_refused(duplicate)
    assert not llm_client._stream_overload_refused(
        json.dumps({"error": {"code": "api_error", "details": {**PROOF, "usage": {"tokens": 1}}}})
    )


@pytest.mark.asyncio
async def test_cold_native_refusal_reuses_saved_root_and_child(monkeypatch):
    from yleum_api.services.generation import recovery_reconciliation as rec

    root = GenerationProviderIdentity(*(UUID(int=i) for i in range(1, 5)), "a" * 64, "b" * 64)
    child = GenerationProviderIdentity(*(UUID(int=i) for i in (1, 2, 3, 5)), "a" * 64, "b" * 64)
    parents, sent = [], []

    async def prepare(body, route, *, existing=None, parent=None):
        parents.append(parent)
        return child if parent else root

    def reply(request):
        sent.append(json.loads(request.content))
        return httpx.Response(200, json={"content": [], "stop_reason": "end_turn"})

    monkeypatch.setattr(provider_attempts, "prepare_generation_attempt", prepare)
    monkeypatch.setattr(rec, "provider_reconciliation_requested", lambda: True)
    monkeypatch.setattr(rec, "provider_dispatch_remaining", lambda: 20)
    resumed = AsyncMock(return_value=(1, True))
    monkeypatch.setattr(provider_attempts, "resume_generation_wire", resumed)
    failure = llm_client._gateway_failure(refusal())
    recovered = AsyncMock(side_effect=[failure, {"content": [], "stop_reason": "end_turn"}])
    monkeypatch.setattr(llm_client, "require_recovered_response", recovered)
    monkeypatch.setattr(agent_native.asyncio, "sleep", AsyncMock())
    async with httpx.AsyncClient(transport=httpx.MockTransport(reply)) as client:
        await agent_native._call_messages(client, URL, [], "s")
    resumed.assert_awaited_once_with(root, allow_provider_refusal=True)
    assert parents == [None, root]
    # Cold child is itself read before any dispatch, preserving no-replay semantics.
    assert sent == []
    assert recovered.await_args_list[1].args == (child,)


def test_legacy_chat_history_keeps_its_original_wire_budget():
    from datetime import UTC, datetime, timedelta

    from yleum_api.services.generation import recovery_reconciliation as rec

    def plan(kind, allowance):
        return rec.ProviderDispatchPlan(
            kind=kind,
            deadline=datetime.now(UTC) + timedelta(seconds=30),
            outer_attempt=0,
            outer_allowance=3,
            wire_allowance=allowance,
        )

    current, saved = plan("chat", 2), plan("chat", 1)
    restored = rec._preserve_legacy_chat_allowance(current, saved)
    assert restored.wire_allowance == 1 and saved.wire_allowance == 1
    assert restored.deadline == current.deadline
    assert current.wire_allowance == 2
    assert (
        rec._preserve_legacy_chat_allowance(plan("native", 2), plan("native", 1)).wire_allowance
        == 2
    )
    assert rec._preserve_legacy_chat_allowance(plan("chat", 3), saved).wire_allowance == 3

@pytest.mark.parametrize("status", [500, 502, 503, 504])
@pytest.mark.parametrize("native", [False, True])
def test_new_temporary_refusal_shared_live_and_cold_reader(status, native):
    response = refusal(
        native=native,
        changes={"retry_policy": "llmgw_unavailable_v1", "upstream_http_status": status},
    )
    assert llm_client._response_overload_refused(response)
    failure = llm_client._gateway_failure(response)
    assert failure.code == "upstream_refused" and failure.retry_safe
    assert failure.upstream_http_status == status
    if native:
        assert agent_native._gateway_overload_refused(response, URL)
    document = response.json()
    error = document.get("detail", document)["error"]
    assert llm_client._stream_overload_refused(json.dumps({"error": error}))


@pytest.mark.parametrize(
    "policy,status",
    [
        ("llmgw_overload_v1", 500),
        ("llmgw_overload_v1", 503),
        ("llmgw_overload_v1", 504),
        ("llmgw_unavailable_v1", 501),
        ("llmgw_unavailable_v1", 505),
        ("llmgw_unavailable_v1", 599),
    ],
)
def test_policy_status_pairs_fail_closed(policy, status):
    response = refusal(changes={"retry_policy": policy, "upstream_http_status": status})
    assert not llm_client._response_overload_refused(response)


@pytest.mark.parametrize("policy", [[], {}, ["llmgw_unavailable_v1"], None, True])
def test_malformed_policy_is_never_permission(policy):
    assert not llm_client._response_overload_refused(refusal(changes={"retry_policy": policy}))


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [False, True])
async def test_preconnect_then_confirmed_refusal_keeps_last_backup_slot(monkeypatch, stream):
    import asyncio

    root = GenerationProviderIdentity(*(UUID(int=i) for i in range(1, 5)), "a" * 64, "b" * 64)
    child = GenerationProviderIdentity(*(UUID(int=i) for i in (1, 2, 3, 5)), "a" * 64, "b" * 64)
    parents = []

    async def prepare(*args, parent=None, **kwargs):
        parents.append(parent)
        return child if parent else root

    sent = []

    def reply(request):
        sent.append(json.loads(request.content))
        if len(sent) == 1:
            raise httpx.ConnectError("synthetic", request=request)
        if len(sent) == 2:
            return refusal(
                native=False,
                changes={"retry_policy": "llmgw_unavailable_v1", "upstream_http_status": 500},
            )
        return httpx.Response(
            200,
            text='data: {"choices":[{"delta":{"content":"ok"}}]}\n\ndata: [DONE]\n\n'
            if stream
            else "{}",
        )

    real = httpx.AsyncClient
    monkeypatch.setattr(
        httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(reply), **kw)
    )
    monkeypatch.setattr(provider_attempts, "prepare_generation_attempt", prepare)
    monkeypatch.setattr(llm_client.asyncio, "sleep", AsyncMock())
    payload = {
        "model": "claude-sonnet-5.5",
        "messages": [],
        "max_tokens": 128,
        "stream": stream,
        "metadata": {},
    }
    if stream:
        result = [
            event
            async for event in llm_client._chat_stream_data(
                payload, URL, root, asyncio.get_running_loop().time() + 30
            )
        ]
        assert result[0]["choices"][0]["delta"]["content"] == "ok"
    else:
        assert (await llm_client._chat_post(payload, URL, 30)).status_code == 200
    assert len(sent) == 3
    assert len([parent for parent in parents if parent is not None]) == 1


@pytest.mark.parametrize("kind", ["chat", "sse"])
def test_previous_two_wire_plan_is_preserved_without_refill(kind):
    from datetime import UTC, datetime, timedelta

    from yleum_api.services.generation import recovery_reconciliation as rec

    def plan(allowance):
        return rec.ProviderDispatchPlan(
            kind=kind,
            deadline=datetime.now(UTC) + timedelta(seconds=30),
            outer_attempt=0,
            outer_allowance=1,
            wire_allowance=allowance,
        )

    saved = plan(2)
    assert rec._preserve_legacy_chat_allowance(plan(3), saved).wire_allowance == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [False, True])
async def test_cold_preserved_budget_exhaustion_stops_before_dispatch(monkeypatch, stream):
    import asyncio

    from yleum_api.services.generation import recovery_reconciliation as rec

    root = GenerationProviderIdentity(*(UUID(int=i) for i in range(1, 5)), "a" * 64, "b" * 64)
    monkeypatch.setattr(
        provider_attempts, "prepare_generation_attempt", AsyncMock(return_value=root)
    )
    monkeypatch.setattr(
        provider_attempts, "resume_generation_wire", AsyncMock(return_value=(2, True))
    )
    monkeypatch.setattr(rec, "provider_reconciliation_requested", lambda: True)
    monkeypatch.setattr(rec, "provider_dispatch_remaining", lambda: 20)
    monkeypatch.setattr(rec, "provider_dispatch_wire_allowance", lambda: 2)
    monkeypatch.setattr(
        llm_client,
        "require_recovered_response",
        AsyncMock(side_effect=llm_client._gateway_failure(refusal())),
    )
    monkeypatch.setattr(llm_client.asyncio, "sleep", AsyncMock())
    client = AsyncMock(side_effect=AssertionError("no remaining wire may dispatch"))
    monkeypatch.setattr(httpx, "AsyncClient", client)
    payload = {
        "model": "claude-sonnet-5.5",
        "stream": stream,
        "max_tokens": 128,
        "messages": [],
        "metadata": {},
    }
    with pytest.raises(llm_client.LLMError, match="budget is exhausted"):
        if stream:
            async for _ in llm_client._chat_stream_data(
                payload, URL, root, asyncio.get_running_loop().time() + 30
            ):
                pass
        else:
            await llm_client._chat_post(payload, URL, 30)
    client.assert_not_called()


@pytest.mark.asyncio
async def test_cold_stream_cannot_use_second_backup_child(monkeypatch):
    import asyncio

    from yleum_api.services.generation import recovery_reconciliation as rec

    root = GenerationProviderIdentity(*(UUID(int=i) for i in range(1, 5)), "a" * 64, "b" * 64)
    prepare = AsyncMock(return_value=root)
    monkeypatch.setattr(provider_attempts, "prepare_generation_attempt", prepare)
    monkeypatch.setattr(
        provider_attempts, "resume_generation_wire", AsyncMock(return_value=(1, True))
    )
    monkeypatch.setattr(rec, "provider_reconciliation_requested", lambda: True)
    monkeypatch.setattr(rec, "provider_dispatch_remaining", lambda: 20)
    monkeypatch.setattr(
        llm_client,
        "require_recovered_response",
        AsyncMock(side_effect=llm_client._gateway_failure(refusal())),
    )
    monkeypatch.setattr(llm_client.asyncio, "sleep", AsyncMock())
    sent = []

    def reply(request):
        sent.append(request)
        return refusal(
            changes={"retry_policy": "llmgw_unavailable_v1", "upstream_http_status": 503}
        )

    real = httpx.AsyncClient
    monkeypatch.setattr(
        httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(reply), **kw)
    )
    payload = {"model": "claude-sonnet-5.5", "stream": True, "messages": [], "metadata": {}}
    with pytest.raises(llm_client.LLMError):
        async for _ in llm_client._chat_stream_data(
            payload, URL, root, asyncio.get_running_loop().time() + 30
        ):
            pass
    assert len(sent) == 1 and prepare.await_count == 1
