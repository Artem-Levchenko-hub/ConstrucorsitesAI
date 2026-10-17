"""Durable provider identity is independent of transport and never model authority."""

import hashlib
import importlib
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID

import pytest


def protocol():
    module = "yleum_api.services.generation.provider_identity"
    assert importlib.util.find_spec(module), "missing generation provider protocol"
    return importlib.import_module(module)


def test_exact_canonical_wire_and_omission_vector():
    body = {
        "model": "model",
        "messages": [{"role": "user", "content": "Привет"}],
        "stream": False,
        "temperature": 0.0,
    }
    expected = (
        '{"body":{"messages":[{"content":"Привет","role":"user"}],'
        '"model":"model","stream":false,"temperature":0.0},'
        '"route":"/v1/chat/completions","version":1}'
    ).encode()
    p = protocol()
    assert p.canonical_request("/v1/chat/completions", body) == expected
    assert p.request_digest("/v1/chat/completions", body) == hashlib.sha256(expected).hexdigest()
    assert p.request_digest("/v1/chat/completions", {**body, "max_tokens": None}) != (
        p.request_digest("/v1/chat/completions", body)
    )
    assert (
        p.request_digest(
            "/v1/chat/completions", {**body, "metadata": {"retry_count": 8}, "user": "owner"}
        )
        == hashlib.sha256(expected).hexdigest()
    )


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), {"bad"}, (1, 2)])
def test_noncanonical_values_never_get_a_digest(bad):
    with pytest.raises(ValueError):
        protocol().request_digest("/v1/messages", {"model": "m", "messages": [bad]})


@pytest.mark.parametrize(
    "raw",
    [
        '{"model":"m","model":"other","messages":[]}',
        '{"model":"m","messages":[],"temperature":NaN}',
        "[]",
    ],
)
def test_raw_json_rejects_duplicate_keys_nonfinite_and_nonobject(raw):
    with pytest.raises(ValueError):
        protocol().decode_request(raw.encode())


def test_unknown_semantic_fields_fail_closed():
    with pytest.raises(ValueError):
        protocol().request_digest("/v1/messages", {"model": "m", "messages": [], "new": 1})


def test_identity_roundtrip_is_strict_and_frozen():
    p = protocol()
    value = {
        "version": 1,
        **{
            key: str(UUID(int=i))
            for i, key in enumerate(
                ("run_id", "project_id", "user_id", "attempt_id"),
                1,
            )
        },
        "prompt_hash": "a" * 64,
        "request_sha256": "b" * 64,
    }
    identity = p.GenerationProviderIdentity.from_wire(value)
    assert identity.to_wire() == value
    assert p.GenerationProviderIdentity.from_wire(json.loads(json.dumps(value))) == identity
    for update in (
        {"version": True},
        {"extra": "forged"},
        {"run_id": "bad"},
        {"request_sha256": "B" * 64},
    ):
        with pytest.raises(ValueError):
            p.GenerationProviderIdentity.from_wire({**value, **update})
    with pytest.raises((AttributeError, TypeError)):
        identity.prompt_hash = "c" * 64


@pytest.mark.parametrize("stream", [False, True])
async def test_actual_chat_caller_never_sends_before_intent_commit(monkeypatch, stream):
    import httpx

    from yleum_api.services import llm_client

    name = "yleum_api.services.generation.provider_attempts"
    assert importlib.util.find_spec(name), "missing durable API intent writer"
    writer = importlib.import_module(name)
    monkeypatch.setattr(
        writer,
        "prepare_generation_attempt",
        AsyncMock(
            side_effect=RuntimeError("intent commit unknown"),
        ),
    )
    monkeypatch.setattr(
        llm_client,
        "get_settings",
        lambda: SimpleNamespace(
            mock_llm=False,
            llm_gateway_url="https://gateway.invalid",
        ),
    )
    sent = []

    async def post(*args, **kwargs):
        sent.append(True)
        raise AssertionError("intent was not committed")

    monkeypatch.setattr(httpx.AsyncClient, "post", post)
    monkeypatch.setattr(httpx.AsyncClient, "stream", post)
    with pytest.raises(RuntimeError, match="intent commit unknown"):
        if stream:
            async for _ in llm_client.stream_chat_completion([], "m", "u", "p", "msg"):
                pass
        else:
            await llm_client.complete_chat([], "m", user_id="u", project_id="p")
    assert sent == []


@pytest.mark.parametrize("failure", ["transport", "closed_preconnect", "ambiguous"])
async def test_native_identity_reuses_transport_but_not_definitively_closed_attempt(
    monkeypatch,
    failure,
):
    import httpx

    from tests.test_native_transport_retry_safety import PROOF
    from yleum_api.services import agent_native
    from yleum_api.services.generation import provider_attempts

    p = protocol()
    root = p.GenerationProviderIdentity(*(UUID(int=i) for i in range(1, 5)), "a" * 64, "b" * 64)
    child = p.GenerationProviderIdentity(*(UUID(int=i) for i in (1, 2, 3, 5)), "a" * 64, "b" * 64)
    preparations, sent = [], []

    async def prepare(body, route, *, existing=None, parent=None):
        preparations.append((existing, parent))
        return child if parent else existing or root

    def response(request):
        sent.append(json.loads(request.content))
        if len(sent) == 1:
            if failure == "transport":
                raise httpx.ConnectError("no connection", request=request)
            return httpx.Response(
                502,
                json={
                    "error": {
                        "type": "api_error",
                        "details": PROOF if failure == "closed_preconnect" else {},
                    }
                },
            )
        return httpx.Response(200, json={"content": [], "stop_reason": "end_turn"})

    monkeypatch.setattr(provider_attempts, "prepare_generation_attempt", prepare)
    monkeypatch.setattr(agent_native.asyncio, "sleep", AsyncMock())
    async with httpx.AsyncClient(transport=httpx.MockTransport(response)) as client:
        if failure == "ambiguous":
            with pytest.raises(RuntimeError):
                await agent_native._call_messages(
                    client, "https://gateway.test/v1/messages", [], "s"
                )
        else:
            await agent_native._call_messages(client, "https://gateway.test/v1/messages", [], "s")
    assert sent[0]["metadata"]["generation_provider_identity"] == root.to_wire()
    if failure == "ambiguous":
        assert len(sent) == 1
    else:
        assert len(sent) == 2
        expected = child if failure == "closed_preconnect" else root
        assert sent[1]["metadata"]["generation_provider_identity"] == expected.to_wire()
        assert preparations[-1][1] == (root if failure == "closed_preconnect" else None)


@pytest.mark.parametrize("failure", ["transport", "closed_preconnect", "ambiguous"])
@pytest.mark.parametrize("default_output", [False, True])
async def test_complete_transfers_only_existing_preconnect_allowance(
    monkeypatch, failure, default_output
):
    import httpx

    from yleum_api.services import llm_client
    from yleum_api.services.generation import provider_attempts

    root = protocol().GenerationProviderIdentity(
        *(UUID(int=i) for i in range(1, 5)), "a" * 64, "b" * 64
    )
    child = protocol().GenerationProviderIdentity(
        *(UUID(int=i) for i in (1, 2, 3, 5)), "a" * 64, "b" * 64
    )
    sent, preparations = [], []

    async def prepare(body, route, *, existing=None, parent=None):
        preparations.append((existing, parent))
        return child if parent else existing or root

    def reply(request):
        sent.append((json.loads(request.content), request.extensions["timeout"]))
        if len(sent) == 1:
            if failure == "transport":
                raise httpx.ConnectError("synthetic", request=request)
            return httpx.Response(
                502,
                json={
                    "detail": {
                        "error": {
                            "code": "model_unavailable",
                            "details": {
                                "provider_failure_kind": "preconnect",
                                "provider_charge_ambiguous": False,
                                "retry_safe": True,
                                "retry_policy": "llmgw_preconnect_v1",
                            }
                            if failure == "closed_preconnect"
                            else {},
                        }
                    }
                },
            )
        return httpx.Response(
            200, json={"choices": [{"finish_reason": "stop", "message": {"content": "ok"}}]}
        )

    monkeypatch.setattr(provider_attempts, "prepare_generation_attempt", prepare)
    monkeypatch.setattr(
        llm_client,
        "get_settings",
        lambda: SimpleNamespace(mock_llm=False, llm_gateway_url="https://gateway.test"),
    )
    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx, "AsyncClient", lambda **kw: real_client(transport=httpx.MockTransport(reply), **kw)
    )
    sleeps = []

    async def sleep(delay):
        sleeps.append(delay)

    monkeypatch.setattr(llm_client.asyncio, "sleep", sleep)
    kwargs = {"max_tokens": None if default_output else 1024, "timeout_seconds": 30}
    expected_retry = not default_output and failure != "ambiguous"
    if expected_retry:
        assert await llm_client.complete_chat([], "claude-sonnet-5.5", **kwargs) == "ok"
    else:
        with pytest.raises(llm_client.LLMError):
            await llm_client.complete_chat([], "claude-sonnet-5.5", **kwargs)
    assert len(sent) == (2 if expected_retry else 1)
    assert sleeps == ([0.5] if expected_retry else [])
    if expected_retry:
        identity = child if failure == "closed_preconnect" else root
        assert sent[1][0]["metadata"]["generation_provider_identity"] == identity.to_wire()
        assert sent[1][1]["read"] < sent[0][1]["read"] <= 30


@pytest.mark.parametrize("first_event", [None, "role", "usage", "text"])
async def test_stream_retry_stops_after_any_outward_provider_event(monkeypatch, first_event):
    import httpx

    from yleum_api.services import llm_client
    from yleum_api.services.generation import provider_attempts

    root = protocol().GenerationProviderIdentity(
        *(UUID(int=i) for i in range(1, 5)), "a" * 64, "b" * 64
    )
    child = protocol().GenerationProviderIdentity(
        *(UUID(int=i) for i in (1, 2, 3, 5)), "a" * 64, "b" * 64
    )
    sent = []

    async def prepare(body, route, *, existing=None, parent=None):
        return child if parent else existing or root

    error = {
        "error": {
            "code": "model_unavailable",
            "message": "preconnect",
            "details": {
                "provider_failure_kind": "preconnect",
                "provider_charge_ambiguous": False,
                "retry_safe": True,
                "retry_policy": "llmgw_preconnect_v1",
            },
        }
    }

    def reply(request):
        sent.append(json.loads(request.content))
        events = []
        if len(sent) == 1:
            if first_event == "role":
                events.append({"choices": [{"delta": {"role": "assistant"}}]})
            if first_event == "usage":
                events.append({"usage": {"prompt_tokens": 1}})
            if first_event == "text":
                events.append({"choices": [{"delta": {"content": "partial"}}]})
            events.append(error)
        else:
            events.append({"choices": [{"delta": {"content": "ok"}}]})
        return httpx.Response(200, text="".join("data: " + json.dumps(e) + "\n\n" for e in events))

    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx, "AsyncClient", lambda **kw: real_client(transport=httpx.MockTransport(reply), **kw)
    )
    monkeypatch.setattr(provider_attempts, "prepare_generation_attempt", prepare)
    monkeypatch.setattr(
        llm_client,
        "get_settings",
        lambda: SimpleNamespace(mock_llm=False, llm_gateway_url="https://gateway.test"),
    )
    monkeypatch.setattr(llm_client.asyncio, "sleep", AsyncMock())
    result = [
        event
        async for event in llm_client.stream_chat_completion(
            [], "claude-sonnet-5.5", "u", "p", "msg"
        )
    ]
    assert len(sent) == (2 if first_event is None else 1)
    if first_event is None:
        assert result == [{"delta": "ok"}]
        assert sent[1]["metadata"]["generation_provider_identity"] == child.to_wire()
    else:
        assert "error" in result[-1]


@pytest.mark.parametrize("failure", ["http", "transport"])
async def test_legacy_stream_preserves_public_failure_without_identity(monkeypatch, failure):
    import httpx

    from yleum_api.services import llm_client
    from yleum_api.services.generation import provider_attempts

    calls = []

    def reply(request):
        calls.append(request)
        if failure == "transport":
            raise httpx.ConnectError("synthetic transport", request=request)
        return httpx.Response(503, content=b"synthetic refusal")

    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx, "AsyncClient", lambda **kw: real_client(transport=httpx.MockTransport(reply), **kw)
    )
    monkeypatch.setattr(
        provider_attempts, "prepare_generation_attempt", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(
        llm_client,
        "get_settings",
        lambda: SimpleNamespace(mock_llm=False, llm_gateway_url="https://gateway.test"),
    )
    result = [
        event
        async for event in llm_client.stream_chat_completion([], "claude-sonnet-5.5", "u", "p", "m")
    ]
    expected = (
        "http: synthetic transport"
        if failure == "transport"
        else "gateway 503: b'synthetic refusal'"
    )
    assert result == [{"error": expected}]
    assert len(calls) == 1


@pytest.mark.parametrize("stream", [False, True])
async def test_cancellation_during_retry_backoff_never_prepares_or_sends_child(monkeypatch, stream):
    import asyncio

    import httpx

    from yleum_api.services import llm_client
    from yleum_api.services.generation import provider_attempts

    identity = protocol().GenerationProviderIdentity(
        *(UUID(int=i) for i in range(1, 5)), "a" * 64, "b" * 64
    )
    prepare = AsyncMock(return_value=identity)
    calls = []

    def reply(request):
        calls.append(request)
        raise httpx.ConnectError("synthetic preconnect", request=request)

    async def cancelled_sleep(delay):
        assert delay == 0.5
        raise asyncio.CancelledError

    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx, "AsyncClient", lambda **kw: real_client(transport=httpx.MockTransport(reply), **kw)
    )
    monkeypatch.setattr(provider_attempts, "prepare_generation_attempt", prepare)
    monkeypatch.setattr(llm_client.asyncio, "sleep", cancelled_sleep)
    monkeypatch.setattr(
        llm_client,
        "get_settings",
        lambda: SimpleNamespace(mock_llm=False, llm_gateway_url="https://gateway.test"),
    )
    with pytest.raises(asyncio.CancelledError):
        if stream:
            _ = [
                event
                async for event in llm_client.stream_chat_completion(
                    [], "claude-sonnet-5.5", "u", "p", "m"
                )
            ]
        else:
            await llm_client.complete_chat([], "claude-sonnet-5.5", max_tokens=1024)
    assert len(calls) == 1
    assert prepare.await_count == 1


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("default_output", [False, True])
@pytest.mark.parametrize("second_failure", ["body", "connect"])
async def test_accepted_gateway_headers_never_become_safe_preconnect(
    monkeypatch, stream, default_output, second_failure
):
    import httpx

    from yleum_api.services import llm_client
    from yleum_api.services.generation import provider_attempts

    identity = protocol().GenerationProviderIdentity(
        *(UUID(int=i) for i in range(1, 5)), "a" * 64, "b" * 64
    )
    prepare = AsyncMock(return_value=identity)
    calls = []

    class BrokenBody(httpx.AsyncByteStream):
        async def __aiter__(self):
            raise httpx.ConnectError("synthetic fault after accepted headers")
            yield b""  # pragma: no cover

    def reply(request):
        calls.append(request)
        if len(calls) > 1 and second_failure == "connect":
            raise httpx.ConnectError("synthetic second genuine preconnect", request=request)
        return httpx.Response(200, stream=BrokenBody())

    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx, "AsyncClient", lambda **kw: real_client(transport=httpx.MockTransport(reply), **kw)
    )
    monkeypatch.setattr(provider_attempts, "prepare_generation_attempt", prepare)
    monkeypatch.setattr(llm_client.asyncio, "sleep", AsyncMock())
    monkeypatch.setattr(
        llm_client,
        "get_settings",
        lambda: SimpleNamespace(mock_llm=False, llm_gateway_url="https://gateway.test"),
    )
    if stream:
        result = [
            event
            async for event in llm_client.stream_chat_completion(
                [], "claude-sonnet-5.5", "u", "p", "m"
            )
        ]
        assert result == [{"error": "LLM stream outcome could not be confirmed"}]
    else:
        with pytest.raises(llm_client.LLMError) as caught:
            await llm_client.complete_chat(
                [], "claude-sonnet-5.5", max_tokens=None if default_output else 1024
            )
        assert caught.value.retry_safe is False  # builder's exact outer retry predicate
        assert caught.value.provider_charge_ambiguous is True
        assert caught.value.code == "transport"
    assert len(calls) == 1
    assert prepare.await_count == 1
