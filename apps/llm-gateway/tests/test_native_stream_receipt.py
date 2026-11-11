"""No live provider calls: a complete stream is one atomic paid response."""

import asyncio
import json
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from starlette.requests import Request

from yleum_gateway.core.config import reset_settings_cache
from yleum_gateway.core.errors import UpstreamProviderError
from yleum_gateway.main import create_app
from yleum_gateway.providers import llmgw, native_stream
from yleum_gateway.routers import chat

# The test-specific transport below permits only its loopback server.
_HANDLE_ASYNC_REQUEST = httpx.AsyncHTTPTransport.handle_async_request


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


@pytest.mark.parametrize("max_tokens", [None, 128], ids=["provider-default", "explicit-cap"])
async def test_aitunnel_can_receive_late_headers_within_the_request_deadline(monkeypatch, max_tokens):
    monkeypatch.setenv("AITUNNEL_TEXT_FALLBACK_ENABLED", "true")
    monkeypatch.setenv("AITUNNEL_TEXT_API_KEY", "synthetic-backup-key")
    calls = []

    def reply(request):
        calls.append(request)
        timeouts = request.extensions["timeout"]
        assert timeouts["connect"] == 30
        read_timeout = timeouts["read"]
        # Simulate the long first-byte delay without sleeping through it.
        if read_timeout is not None and read_timeout < 550:
            raise httpx.ReadTimeout("provider headers have not arrived", request=request)
        payload = json.loads(request.content)
        assert payload["stream"] is True
        assert payload["stream_options"] == {"include_usage": True}
        assert payload.get("max_tokens") == max_tokens
        assert ("max_tokens" in payload) == (max_tokens is not None)
        return httpx.Response(200, content=encode(completed()))

    monkeypatch.setattr(httpx, "AsyncHTTPTransport", lambda **_: httpx.MockTransport(reply))
    response = await llmgw.acompletion(
        model="claude-sonnet-5.5", messages=[], max_tokens=max_tokens,
        provider_scope="aitunnel", provider_deadline=asyncio.get_running_loop().time() + 600,
        response_budget=600,
    )
    assert response["choices"][0]["message"]["content"] == "hello"
    assert len(calls) == 1


async def test_complete_receipt_records_protocol_trace_without_body(monkeypatch):
    _, evidence, _ = await collect(monkeypatch, completed())
    assert evidence["transport_stage"] == "complete"
    assert evidence["finish_reason"] == "stop"
    assert evidence["prompt_tokens"] == 12 and evidence["completion_tokens"] == 3
    assert evidence["max_tokens_mode"] == "omitted"
    assert "requested_max_tokens" not in evidence
    assert evidence["transport_mode"] == "sse"
    assert evidence["transport_started_unix_ms"] > 1_000_000_000_000
    assert evidence["response_budget_ms"] == 600_000
    assert evidence["connect_timeout_ms"] == 30_000
    assert evidence["read_timeout_ms"] == 240_000
    assert 0 <= evidence["headers_received_ms"] <= evidence["first_sse_data_ms"] <= evidence["response_completed_ms"]
    assert "hello" not in str(evidence)


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
    body, evidence, _ = await collect(monkeypatch, events)
    assert body["choices"][0]["finish_reason"] == "length"
    assert body["choices"][0]["message"] == {"role": "assistant", "content": ""}
    assert body["usage"]["completion_tokens"] == 65536
    assert evidence["finish_reason"] == "length"
    assert evidence["transport_stage"] == "complete"
    assert evidence["completion_tokens"] == 65536


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
    assert evidence["provider_request_id"] == "header-id"
    assert evidence["provider_cost_rub"] is None and evidence["provider_cost_usd"] is None
    assert evidence["transport_stage"] == "headers"
    assert "finish_reason" not in evidence and "completion_tokens" not in evidence
    assert stream.closed


async def test_disconnected_receipt_keeps_observed_facts_without_claiming_completion(monkeypatch):
    evidence = {}
    stream = Chunks([encode([chunk(finish="length", usage={
        "prompt_tokens": 12, "completion_tokens": 99})])], httpx.ReadTimeout("private-body"))
    monkeypatch.setattr(httpx, "AsyncHTTPTransport", lambda **kw: httpx.MockTransport(
        lambda request: httpx.Response(200, stream=stream)))
    with pytest.raises(httpx.ReadTimeout):
        await native_stream.collect_response("https://provider.test", {"max_tokens": 128000}, {},
                                             budget_seconds=600, evidence=evidence)
    assert evidence["transport_stage"] == "body"
    assert evidence["finish_reason"] == "length" and evidence["completion_tokens"] == 99
    assert evidence["max_tokens_mode"] == "sent" and evidence["requested_max_tokens"] == 128000
    assert "private-body" not in str(evidence) and stream.closed


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


@pytest.mark.parametrize("events,reason", [
    (completed()[:1], "receipt_missing_completion"),
    ([chunk(), {"error": {"message": "private-provider-detail"}, "upstream_http_status": 502}],
     "receipt_upstream_error"),
    (["private-invalid-json"], "receipt_invalid_event"),
    ([chunk(), chunk(id="conflicting-private-id")], "receipt_conflicting_identity"),
    ([chunk(), "[DONE]"], "receipt_incomplete"),
])
def test_route_retains_bounded_receipt_failure_without_replay_or_billing(
    monkeypatch, neutralize_lifespan, neutralize_side_effects, events, reason,
):
    setup_route(monkeypatch)
    requests = []

    def reply(request):
        requests.append(request)
        return httpx.Response(200, content=encode(events), headers={
            "x-llmgw-request-id": "stable-provider-id",
        })

    monkeypatch.setattr(httpx, "AsyncHTTPTransport", lambda **kw: httpx.MockTransport(reply))
    with TestClient(create_app()) as client:
        result = client.post("/v1/chat/completions", json={"model": "claude-sonnet-5.5",
            "messages": [{"role": "user", "content": "synthetic"}]})

    assert result.status_code == 502 and len(requests) == 1
    chat.provider_calls.finish_call.assert_awaited_once()
    receipt = chat.provider_calls.finish_call.await_args.kwargs
    assert receipt["error_type"] == reason
    assert receipt["status"] == "ambiguous"
    assert receipt["provider_request_id"] == "stable-provider-id"
    assert receipt["provider_cost_rub"] is None and receipt["provider_cost_usd"] is None
    chat.billing.charge.assert_not_awaited()
    details = result.json()["detail"]["error"]["details"]
    assert details["provider_receipt_failure"] == reason
    assert details["provider_transport_stage"] == "body"
    assert details["provider_charge_ambiguous"] is True
    assert "upstream_http_status" not in details and "retry_safe" not in details
    assert "private" not in result.text and "private" not in str(receipt)


@pytest.mark.parametrize("failure", ["timeout", "oversize"])
@pytest.mark.parametrize("reported_cost", [None, "1.23"])
def test_error_headers_status_survives_native_body_failure(
    monkeypatch, neutralize_lifespan, neutralize_side_effects, failure, reported_cost,
):
    setup_route(monkeypatch)
    requests = []
    stream = Chunks([b"private-oversize" * 1200] if failure == "oversize" else [],
                    httpx.ReadTimeout("private-timeout") if failure == "timeout" else None)
    headers = {"x-llmgw-request-id": "native-502-id"}
    if reported_cost is not None:
        headers["x-llmgw-cost-rub"] = reported_cost

    def reply(request):
        requests.append(request)
        return httpx.Response(502, stream=stream, headers=headers)

    monkeypatch.setattr(httpx, "AsyncHTTPTransport", lambda **kw: httpx.MockTransport(reply))
    with TestClient(create_app()) as client:
        response = client.post("/v1/chat/completions", json={"model": "claude-sonnet-5.5",
            "messages": [{"role": "user", "content": "synthetic"}]})

    assert response.status_code == 502 and len(requests) == 1 and stream.closed
    details = response.json()["detail"]["error"]["details"]
    assert details.get("upstream_http_status") == 502
    assert details["provider_request_id"] == "native-502-id"
    assert details["provider_cost_rub"] == reported_cost
    assert details["provider_charge_ambiguous"] is True and "retry_safe" not in details
    chat.provider_calls.finish_call.assert_awaited_once()
    journal = chat.provider_calls.finish_call.await_args.kwargs
    assert journal["status"] == "ambiguous"
    assert journal["error_type"] == ("ReadTimeout" if failure == "timeout" else "receipt_size_limit")
    assert journal["provider_request_id"] == "native-502-id"
    assert journal["provider_cost_rub"] == (Decimal(reported_cost) if reported_cost else None)
    assert journal["provider_cost_usd"] is None
    chat.billing.charge.assert_not_awaited()
    assert "private" not in response.text and "private" not in str(journal)


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
    assert result.status_code == 504
    assert result.json()["detail"]["error"]["code"] == "deadline_before_dispatch"
    details = result.json()["detail"]["error"]["details"]
    assert details["provider_charge_ambiguous"] is False
    assert details["retry_safe"] is False and "retry_policy" not in details
    assert provider.await_count == 0
    receipt = chat.provider_calls.finish_call.await_args.kwargs
    assert receipt["status"] == "failed"
    assert receipt["provider_request_id"] is None and receipt["provider_cost_rub"] is None


@pytest.mark.parametrize("partial", [b"data: " + b"x" * 100, b":comment\n" * 100])
async def test_unbounded_line_or_event_is_rejected(monkeypatch, partial):
    monkeypatch.setattr(native_stream, "MAX_EVENT_BYTES", 32)
    with pytest.raises(native_stream.StreamReceiptError):
        await collect(monkeypatch, [], stream=Chunks([partial]))


@pytest.mark.parametrize("provider_scope", ["llmgw", "aitunnel"])
async def test_total_deadline_cancels_body_and_closes_transport(monkeypatch, provider_scope):
    class Hung(Chunks):
        async def __aiter__(self):
            await asyncio.Event().wait()
            yield b""
    stream = Hung([])
    header_name, header_id = (
        ("x-request-id", "chat-0123456789ABCDEFGHIJKL") if provider_scope == "aitunnel"
        else ("x-llmgw-request-id", "header")
    )
    monkeypatch.setattr(httpx, "AsyncHTTPTransport", lambda **kw: httpx.MockTransport(
        lambda request: httpx.Response(200, stream=stream, headers={header_name: header_id})))
    evidence = {}
    deadline = asyncio.get_running_loop().time() + 0.03 if provider_scope == "aitunnel" else None
    with pytest.raises(TimeoutError):
        await native_stream.collect_response("https://provider.test", {}, {},
                                             budget_seconds=0.03, evidence=evidence,
                                             provider_scope=provider_scope, deadline=deadline)
    assert stream.closed and evidence["provider_request_id"] == header_id


async def test_aitunnel_proxy_header_is_not_a_provider_receipt(monkeypatch):
    evidence = {}
    stream = Chunks([], httpx.ReadTimeout("interrupted before first event"))
    monkeypatch.setattr(httpx, "AsyncHTTPTransport", lambda **_: httpx.MockTransport(
        lambda request: httpx.Response(200, stream=stream, headers={
            "x-request-id": "proxy-correlation-id", "x-llmgw-request-id": "other-provider-id",
        })))
    with pytest.raises(httpx.ReadTimeout):
        await native_stream.collect_response(
            "https://provider.test", {}, {}, budget_seconds=30, evidence=evidence,
            provider_scope="aitunnel",
        )
    assert evidence.get("provider_request_id") is None


@pytest.mark.parametrize("after_headers", [False, True])
async def test_aitunnel_stream_connect_error_is_safe_only_before_send(monkeypatch, after_headers):
    monkeypatch.setenv("AITUNNEL_TEXT_FALLBACK_ENABLED", "true")
    monkeypatch.setenv("AITUNNEL_TEXT_API_KEY", "synthetic-backup-key")
    posts = []

    def reply(request):
        posts.append(request)
        error = httpx.ConnectTimeout("synthetic connection timeout", request=request)
        if after_headers:
            return httpx.Response(200, stream=Chunks([], error))
        raise error

    monkeypatch.setattr(httpx, "AsyncHTTPTransport", lambda **_: httpx.MockTransport(reply))
    with pytest.raises(UpstreamProviderError) as raised:
        async for _ in llmgw.astream("claude-sonnet-5.5", [], provider_scope="aitunnel"):
            pytest.fail("The failed stream cannot emit output")
    details = raised.value.details
    assert details["provider_charge_ambiguous"] is after_headers
    assert details["provider_failure_kind"] == ("transport" if after_headers else "preconnect")
    assert len(posts) == 1


async def test_final_metadata_cost_and_usage_survive_collection(monkeypatch):
    events = completed()
    events[2]["metadata"] = {"cost_rub": "7.25", "private_unused": "discard"}
    body, evidence, _ = await collect(monkeypatch, events)
    assert body["metadata"] == {"cost_rub": "7.25"}
    assert evidence["provider_cost_rub"] == Decimal("7.25")


def _route_request(req, timeout):
    async def receive():
        return {"type": "http.request", "body": req.model_dump_json(exclude_unset=True).encode(),
                "more_body": False}
    return Request({"type": "http", "headers": [
        (b"x-yleum-provider-timeout-seconds", timeout),
    ]}, receive=receive)


def setup_route(monkeypatch):
    monkeypatch.setenv("LLMGW_API_KEY", "synthetic")
    reset_settings_cache()
    req = chat.ChatCompletionRequest(model="claude-sonnet-5.5",
                                    messages=[{"role": "user", "content": "synthetic"}])
    request = _route_request(req, b"600")
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


async def test_aitunnel_sent_request_without_headers_keeps_typed_deadline(
    monkeypatch, neutralize_side_effects,
):
    posts, diagnostics = [], []
    disconnected = asyncio.Event()

    async def receive_without_reply(reader, writer):
        try:
            headers = await reader.readuntil(b"\r\n\r\n")
            length = next(line.split(b":", 1)[1] for line in headers.lower().splitlines()
                          if line.startswith(b"content-length:"))
            posts.append(json.loads(await reader.readexactly(int(length))))
            await reader.read()
        finally:
            writer.close()
            await writer.wait_closed()
            disconnected.set()

    server = await asyncio.start_server(receive_without_reply, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]

    class LoopbackTransport(httpx.AsyncHTTPTransport):
        async def handle_async_request(self, request):
            assert request.url.host == "127.0.0.1" and request.url.port == port
            return await _HANDLE_ASYNC_REQUEST(self, request)

    monkeypatch.setattr(httpx, "AsyncHTTPTransport", LoopbackTransport)
    monkeypatch.setenv("AITUNNEL_TEXT_FALLBACK_ENABLED", "true")
    monkeypatch.setenv("AITUNNEL_TEXT_API_KEY", "synthetic-backup-key")
    monkeypatch.setattr(chat.text_failover, "select_scope", AsyncMock(return_value="aitunnel"))
    monkeypatch.setattr(llmgw, "_key_and_url", lambda scope: ("synthetic", f"http://127.0.0.1:{port}/chat/completions"))
    monkeypatch.setattr(chat.log, "warning", lambda event, **fields: diagnostics.append(fields))
    req = chat.ChatCompletionRequest(model="claude-sonnet-5.5", max_tokens=128,
                                     messages=[{"role": "user", "content": "synthetic"}])
    async with server:
        with pytest.raises(HTTPException) as raised:
            await asyncio.wait_for(chat.chat_completions(req, _route_request(req, b"15.5")), timeout=2)
        await asyncio.wait_for(disconnected.wait(), timeout=1)

    assert len(posts) == 1 and posts[0]["max_tokens"] == 128
    assert raised.value.status_code == 504
    error = raised.value.detail["error"]
    assert error["code"] == "response_deadline_exceeded"
    details = error["details"]
    assert details["provider_charge_ambiguous"] is True and details["retry_safe"] is False
    assert details["provider_scope"] == "aitunnel" and details["transport_stage"] == "awaiting_headers"
    assert "upstream_http_status" not in details
    chat.provider_calls.finish_call.assert_awaited_once()
    assert details["provider_call_id"] == str(chat.provider_calls.finish_call.await_args.args[0])
    receipt = chat.provider_calls.finish_call.await_args.kwargs
    assert receipt["status"] == "ambiguous" and receipt["provider_request_id"] is None
    chat.billing.charge.assert_not_awaited()
    record, = diagnostics
    assert record["provider_call_id"] == details["provider_call_id"]
    assert 0 <= record["request_sent_ms"] <= record["response_budget_ms"] <= 500
    assert record["transport_mode"] == "sse" and record["requested_max_tokens"] == 128
    assert record["read_timeout_ms"] is None
    assert "headers_received_ms" not in record and "first_sse_data_ms" not in record


@pytest.mark.parametrize("value,ceiling", [("nan", 255), ("inf", 255), ("-1", 255),
                                           ("bad", 255), ("999999", 1485), ("40", 25)])
async def test_header_cannot_create_unbounded_provider_wait(
    monkeypatch, neutralize_side_effects, value, ceiling,
):
    req, _ = setup_route(monkeypatch)
    request = _route_request(req, value.encode())
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


@pytest.mark.parametrize("scope", ["aitunnel", "llmgw"])
@pytest.mark.parametrize("failure", ["disconnect", "missing_done"])
async def test_interrupted_receipt_preserves_provider_currency(monkeypatch, scope, failure):
    evidence, posts = {}, []
    stream = Chunks([encode([chunk(finish="stop", usage={
        "prompt_tokens": 10, "completion_tokens": 2, "cost": 0.3, "cost_rub": 0.3,
    })])], httpx.ReadTimeout("synthetic") if failure == "disconnect" else None)
    def reply(request):
        posts.append(request)
        return httpx.Response(200, stream=stream)
    monkeypatch.setattr(httpx, "AsyncHTTPTransport", lambda **_: httpx.MockTransport(reply))
    options = {"provider_scope": scope} if scope == "aitunnel" else {}
    with pytest.raises((httpx.ReadTimeout, native_stream.StreamReceiptError)):
        await native_stream.collect_response("https://provider.test", {}, {},
            budget_seconds=30, evidence=evidence, **options)
    assert evidence["provider_cost_rub"] == Decimal("0.3")
    assert evidence["provider_cost_usd"] == (None if scope == "aitunnel" else Decimal("0.3"))
    assert evidence["transport_stage"] == "body"
    assert len(posts) == 1 and stream.closed


@pytest.mark.parametrize("scope", ["aitunnel", "llmgw"])
async def test_lost_body_header_costs_use_actual_provider_scope(monkeypatch, scope):
    evidence = {}
    stream = Chunks([], httpx.ReadTimeout("synthetic"))
    monkeypatch.setattr(httpx, "AsyncHTTPTransport", lambda **_: httpx.MockTransport(
        lambda request: httpx.Response(200, stream=stream,
            headers={"x-llmgw-cost-rub": "77", "x-cost-usd": "99"})))
    options = {"provider_scope": scope} if scope == "aitunnel" else {}
    with pytest.raises(httpx.ReadTimeout):
        await native_stream.collect_response("https://provider.test", {}, {},
            budget_seconds=30, evidence=evidence, **options)
    assert evidence["provider_cost_rub"] == (None if scope == "aitunnel" else Decimal("77"))
    assert evidence["provider_cost_usd"] == (None if scope == "aitunnel" else Decimal("99"))
    assert stream.closed
