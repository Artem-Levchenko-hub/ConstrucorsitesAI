"""No live provider calls: a complete stream is one atomic paid response."""

import asyncio
import json
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request

from yleum_gateway.core.config import reset_settings_cache
from yleum_gateway.main import create_app
from yleum_gateway.providers import native_stream
from yleum_gateway.routers import chat


def chunk(delta=None, finish=None, **extra):
    return {"id": "completion-id", "model": "anthropic/claude-sonnet-5.5",
            "choices": [{"index": 0, "delta": delta or {}, "finish_reason": finish}], **extra}


def encode(events):
    return "".join("data: " + (event if isinstance(event, str) else json.dumps(event))
                   + "\n\n" for event in events).encode()


def completed():
    return [chunk({"content": "hello"}), chunk(finish="stop"),
            chunk(usage={"prompt_tokens": 12, "completion_tokens": 3}), "[DONE]"]


async def collect(monkeypatch, events, *, headers=None, stream=None):
    calls = []

    def reply(request):
        calls.append(request)
        return httpx.Response(200, content=encode(events) if stream is None else None,
                              stream=stream, headers=headers or {})

    monkeypatch.setattr(native_stream.httpx, "AsyncHTTPTransport",
                        lambda **kw: httpx.MockTransport(reply))
    evidence = {}
    response = await native_stream.collect_response(
        "https://provider.test/chat/completions", {"model": "anthropic/claude-sonnet-5.5"},
        {}, budget_seconds=600, evidence=evidence,
    )
    assert len(calls) == 1
    assert json.loads(calls[0].content)["stream"] is True
    return response.json(), evidence, calls[0]


async def test_complete_response_has_usage_and_no_240_second_total_cap(monkeypatch):
    body, evidence, request = await collect(monkeypatch, completed())
    assert body["choices"][0]["message"]["content"] == "hello"
    assert body["usage"]["completion_tokens"] == 3
    assert evidence["provider_request_id"] == "completion-id"
    assert request.extensions["timeout"]["read"] == 240


async def test_fragmented_interleaved_tools_are_assembled_atomically(monkeypatch):
    events = [chunk({"tool_calls": [
        {"index": 0, "id": "tool-a", "type": "function", "function": {"name": "write", "arguments": '{"a":'}},
        {"index": 1, "id": "tool-b", "type": "function", "function": {"name": "read", "arguments": '{"b":'}},
    ]}), chunk({"tool_calls": [
        {"index": 1, "function": {"arguments": "2}"}},
        {"index": 0, "function": {"arguments": "1}"}},
    ]}), chunk(finish="tool_calls", usage={"prompt_tokens": 12, "completion_tokens": 3}), "[DONE]"]
    body, _, _ = await collect(monkeypatch, events)
    tools = body["choices"][0]["message"]["tool_calls"]
    assert [json.loads(tool["function"]["arguments"]) for tool in tools] == [{"a": 1}, {"b": 2}]


@pytest.mark.parametrize("events", [
    completed()[:-1], completed()[:2] + ["[DONE]"],
    [chunk(), "[DONE]"],
    [chunk(), chunk(id="different"), *completed()[1:]],
    [chunk(), chunk(model="different"), *completed()[1:]],
    [chunk(finish="stop"), chunk(finish="length"), *completed()[2:]],
])
async def test_incomplete_or_conflicting_stream_fails_closed(monkeypatch, events):
    with pytest.raises(native_stream.StreamReceiptError):
        await collect(monkeypatch, events)


async def test_length_discards_partial_actions_but_retains_usage(monkeypatch):
    events = [chunk({"tool_calls": [{"index": 0, "id": "tool", "function": {
        "name": "write", "arguments": '{"partial":'}}]}),
        chunk(finish="length", usage={"prompt_tokens": 12, "completion_tokens": 65536}), "[DONE]"]
    body, _, _ = await collect(monkeypatch, events)
    assert body["choices"][0]["finish_reason"] == "length"
    assert body["choices"][0]["message"] == {"role": "assistant", "content": ""}
    assert body["usage"]["completion_tokens"] == 65536


class Chunks(httpx.AsyncByteStream):
    def __init__(self, chunks, failure=None):
        self.chunks = chunks
        self.failure = failure
        self.closed = False

    async def __aiter__(self):
        for value in self.chunks:
            yield value
        if self.failure:
            raise self.failure

    async def aclose(self):
        self.closed = True


async def test_response_can_complete_after_old_wall_limits_with_regular_chunks(monkeypatch):
    clock = [0]

    class Budget:
        def __init__(self, seconds):
            assert seconds == 600
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            assert clock[0] <= 600

    class SlowChunks(Chunks):
        async def __aiter__(self):
            for event in completed():
                clock[0] += 100
                yield encode([event])

    monkeypatch.setattr(native_stream, "asyncio", SimpleNamespace(timeout=Budget))
    stream = SlowChunks([])
    body, _, _ = await collect(monkeypatch, [], stream=stream)
    assert clock == [400] and body["usage"]["completion_tokens"] == 3 and stream.closed


@pytest.mark.parametrize("failure", [httpx.ReadTimeout("synthetic"), httpx.RemoteProtocolError("synthetic"), None])
async def test_headers_survive_lost_body_without_invented_cost(monkeypatch, failure):
    evidence = {}
    stream = Chunks([], failure)
    transport = httpx.MockTransport(lambda request: httpx.Response(200, stream=stream,
        headers={"x-llmgw-request-id": "header-id"}))
    monkeypatch.setattr(httpx, "AsyncHTTPTransport", lambda **kw: transport)
    with pytest.raises((httpx.HTTPError, native_stream.StreamReceiptError)):
        await native_stream.collect_response("https://provider.test", {}, {},
                                             budget_seconds=600, evidence=evidence)
    assert evidence == {"provider_request_id": "header-id", "provider_cost_rub": None,
                        "provider_cost_usd": None}
    assert stream.closed


@pytest.mark.parametrize("complete", [False, True])
async def test_generic_proxy_id_never_overrides_received_completion_id(monkeypatch, complete):
    evidence = {}
    events = completed() if complete else completed()[:1]
    transport = httpx.MockTransport(lambda request: httpx.Response(
        200, content=encode(events), headers={"x-request-id": "proxy-correlation-id"},
    ))
    monkeypatch.setattr(httpx, "AsyncHTTPTransport", lambda **kw: transport)
    try:
        await native_stream.collect_response("https://provider.test", {}, {},
                                             budget_seconds=600, evidence=evidence)
    except native_stream.StreamReceiptError:
        assert not complete
    assert evidence["provider_request_id"] == "completion-id"


@pytest.mark.parametrize("failure", [httpx.ReadTimeout("synthetic"), None])
def test_paid_partial_receipt_returns_serializable_http_error(
    monkeypatch, neutralize_lifespan, neutralize_side_effects, failure,
):
    setup_route(monkeypatch)
    calls = []
    def reply(request):
        calls.append(request)
        return httpx.Response(200, stream=Chunks([encode(completed()[:1])], failure), headers={
            "x-request-id": "proxy-id", "x-llmgw-cost-rub": "1.23", "x-cost-usd": "0.01",
        })
    monkeypatch.setattr(httpx, "AsyncHTTPTransport", lambda **kw: httpx.MockTransport(reply))
    with TestClient(create_app()) as client:
        result = client.post("/v1/chat/completions", json={"model": "claude-sonnet-5.5",
            "messages": [{"role": "user", "content": "synthetic"}]})
    assert result.status_code == 502 and len(calls) == 1
    details = result.json()["detail"]["error"]["details"]
    assert details["provider_request_id"] == "completion-id"
    assert details["provider_cost_rub"] == "1.23" and details["provider_cost_usd"] == "0.01"
    assert details["provider_charge_ambiguous"] is True and "retry_safe" not in details
    receipt = chat.provider_calls.finish_call.await_args.kwargs
    assert receipt["provider_request_id"] == "completion-id"
    assert receipt["provider_cost_rub"] == Decimal("1.23")
    chat.provider_calls.finish_call.assert_awaited_once()


def test_exhausted_receipt_reserve_never_dispatches_or_creates_ambiguous_charge(
    monkeypatch, neutralize_lifespan, neutralize_side_effects,
):
    setup_route(monkeypatch)
    provider = AsyncMock(side_effect=AssertionError("No provider dispatch allowed"))
    monkeypatch.setattr(native_stream, "collect_response", provider)
    with TestClient(create_app()) as client:
        result = client.post("/v1/chat/completions",
            headers={"x-yleum-provider-timeout-seconds": "10"},
            json={"model": "claude-sonnet-5.5",
                  "messages": [{"role": "user", "content": "synthetic"}]})
    assert result.status_code == 502
    details = result.json()["detail"]["error"]["details"]
    assert details["provider_charge_ambiguous"] is False
    assert "retry_safe" not in details and "retry_policy" not in details
    assert provider.await_count == 0
    receipt = chat.provider_calls.finish_call.await_args.kwargs
    assert receipt["status"] == "failed"
    assert receipt["provider_request_id"] is None and receipt["provider_cost_rub"] is None


@pytest.mark.parametrize("partial", [b"data: " + b"x" * 100, b":comment\n" * 100])
async def test_unbounded_line_or_event_is_rejected(monkeypatch, partial):
    monkeypatch.setattr(native_stream, "MAX_EVENT_BYTES", 32)
    with pytest.raises(native_stream.StreamReceiptError):
        await collect(monkeypatch, [], stream=Chunks([partial]))


async def test_total_deadline_cancels_body_and_closes_transport(monkeypatch):
    class Hung(Chunks):
        async def __aiter__(self):
            await asyncio.Event().wait()
            yield b""
    stream = Hung([])
    monkeypatch.setattr(httpx, "AsyncHTTPTransport", lambda **kw: httpx.MockTransport(
        lambda request: httpx.Response(200, stream=stream, headers={"x-llmgw-request-id": "header"})))
    evidence = {}
    with pytest.raises(TimeoutError):
        await native_stream.collect_response("https://provider.test", {}, {},
                                             budget_seconds=0.01, evidence=evidence)
    assert stream.closed and evidence["provider_request_id"] == "header"


async def test_final_metadata_cost_and_usage_survive_collection(monkeypatch):
    events = completed()
    events[2]["metadata"] = {"cost_rub": "7.25", "private_unused": "discard"}
    body, evidence, _ = await collect(monkeypatch, events)
    assert body["metadata"] == {"cost_rub": "7.25"}
    assert evidence["provider_cost_rub"] == Decimal("7.25")


def setup_route(monkeypatch):
    monkeypatch.setenv("LLMGW_API_KEY", "synthetic")
    reset_settings_cache()
    req = chat.ChatCompletionRequest(model="claude-sonnet-5.5",
                                    messages=[{"role": "user", "content": "synthetic"}])
    request = Request({"type": "http", "headers": [(b"x-yleum-provider-timeout-seconds", b"600")]})
    return req, request


async def test_real_route_one_post_one_settlement_and_receipt_budget(monkeypatch, neutralize_side_effects):
    req, request = setup_route(monkeypatch)
    calls = []
    def reply(request):
        calls.append(request)
        return httpx.Response(200, content=encode(completed()), headers={
            "x-llmgw-request-id": "header-id", "x-llmgw-cost-rub": "1.23"})
    monkeypatch.setattr(httpx, "AsyncHTTPTransport", lambda **kw: httpx.MockTransport(reply))
    actual = native_stream.collect_response
    budgets = []
    async def observed(*args, **kwargs):
        budgets.append(kwargs["budget_seconds"])
        return await actual(*args, **kwargs)
    monkeypatch.setattr(native_stream, "collect_response", observed)
    result = await chat.chat_completions(req, request)
    assert len(calls) == 1 and 580 < budgets[0] <= 585
    assert result["choices"][0]["message"]["content"] == "hello"
    chat.provider_calls.finish_call.assert_awaited_once()
    receipt = chat.provider_calls.finish_call.await_args.kwargs
    assert receipt["provider_request_id"] == "header-id"
    assert receipt["provider_cost_rub"] == Decimal("1.23")


@pytest.mark.parametrize("value,ceiling", [("nan", 255), ("inf", 255), ("-1", 255),
                                           ("bad", 255), ("999999", 1485), ("40", 25)])
async def test_header_cannot_create_unbounded_provider_wait(
    monkeypatch, neutralize_side_effects, value, ceiling,
):
    req, _ = setup_route(monkeypatch)
    request = Request({"type": "http", "headers": [
        (b"x-yleum-provider-timeout-seconds", value.encode()),
    ]})
    budgets = []
    async def reply(*args, **kwargs):
        budgets.append(kwargs["budget_seconds"])
        return httpx.Response(200, json={"id": "id", "model": "anthropic/claude-sonnet-5.5",
            "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 12, "completion_tokens": 3}},
            request=httpx.Request("POST", "https://provider.test"))
    monkeypatch.setattr(native_stream, "collect_response", reply)
    await chat.chat_completions(req, request)
    assert len(budgets) == 1 and ceiling - 2 < budgets[0] <= ceiling


@pytest.mark.parametrize("phase", ["before_headers", "chunks", "receipt"])
async def test_route_cancel_owns_one_journal_task_without_orphan_post(
    monkeypatch, neutralize_side_effects, phase,
):
    req, request = setup_route(monkeypatch)
    entered, receipt_entered, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
    calls = []
    stream = None
    class Waiting(Chunks):
        async def __aiter__(self):
            entered.set()
            await asyncio.Event().wait()
            yield b""
    async def reply(request):
        nonlocal stream
        calls.append(request)
        if phase == "before_headers":
            entered.set()
            await asyncio.Event().wait()
        if phase == "chunks":
            stream = Waiting([])
            return httpx.Response(200, stream=stream, headers={"x-llmgw-request-id": "header"})
        return httpx.Response(200, content=encode(completed()))
    async def finish(*args, **kwargs):
        receipt_entered.set()
        await release.wait()
    monkeypatch.setattr(httpx, "AsyncHTTPTransport", lambda **kw: httpx.MockTransport(reply))
    monkeypatch.setattr(chat.provider_calls, "finish_call", AsyncMock(side_effect=finish))
    task = asyncio.create_task(chat.chat_completions(req, request))
    await asyncio.wait_for(receipt_entered.wait() if phase == "receipt" else entered.wait(), 1)
    task.cancel()
    await asyncio.wait_for(receipt_entered.wait(), 1)
    task.cancel()
    await asyncio.sleep(0)
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert len(calls) == 1
    chat.provider_calls.finish_call.assert_awaited_once()
    receipt = chat.provider_calls.finish_call.await_args.kwargs
    assert receipt["status"] == ("completed" if phase == "receipt" else "ambiguous")
    if phase == "chunks":
        assert receipt["provider_request_id"] == "header" and stream.closed
    if phase == "before_headers":
        assert receipt["provider_request_id"] is None
