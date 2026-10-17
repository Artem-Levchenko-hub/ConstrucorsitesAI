from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock
from uuid import UUID

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sse_starlette.sse import EventSourceResponse

from tests.test_generation_response_store import Ledger, Objects
from yleum_gateway.services import generation_response_capture as capture
from yleum_gateway.services.generation_identity import GenerationProviderIdentity

IDENTITY = GenerationProviderIdentity(*(UUID(int=i) for i in range(1, 5)), "a" * 64, "b" * 64)


@pytest.mark.parametrize("edge", ["native", "collected", "chat", "sse"])
async def test_expired_after_sql_wait_is_rejected_at_physical_post(edge, monkeypatch):
    import httpx

    from yleum_gateway.providers import llmgw, native_stream
    from yleum_gateway.routers import messages_native

    posts = []

    def reply(request):
        posts.append(request)
        return httpx.Response(500)

    monkeypatch.setattr(httpx, "HTTPTransport", lambda **kwargs: httpx.MockTransport(reply))
    monkeypatch.setattr(httpx, "AsyncHTTPTransport", lambda **kwargs: httpx.MockTransport(reply))
    monkeypatch.setattr(llmgw, "_key_and_url", lambda: ("synthetic", "http://provider.test"))
    ledger = SettledLedger()
    ledger.capacity_expires_at = 1
    owned = capture.Capture(IDENTITY, "/v1/chat/completions", objects=Objects(), ledger=ledger)
    context = capture._CURRENT.set(owned)
    try:
        try:
            if edge == "native":
                await asyncio.to_thread(
                    messages_native._post_llmgw, "http://provider.test", {}, {}, evidence={}
                )
            elif edge == "collected":
                await native_stream.collect_response(
                    "http://provider.test", {}, {}, budget_seconds=2, evidence={}
                )
            elif edge == "chat":
                await llmgw.acompletion(
                    model="claude-sonnet-5.5", messages=[], max_tokens=32, retry_preconnect=False
                )
            else:
                async for _ in llmgw.astream("claude-sonnet-5.5", [], retry_preconnect=False):
                    pass
        except Exception:
            pass
        assert posts == []
    finally:
        if owned.stream_task is not None:
            await asyncio.gather(owned.stream_task, return_exceptions=True)
        capture._CURRENT.reset(context)


@pytest.mark.parametrize("route", ["/v1/messages", "/v1/chat/completions"])
@pytest.mark.parametrize("suffix", ["", ',"metadata":{}', ',"metadata":{},"metadata":{}'])
def test_auth_header_cannot_hide_behind_absent_or_duplicate_metadata(
    monkeypatch, neutralize_lifespan, route, suffix
):
    from yleum_gateway.main import create_app
    from yleum_gateway.services import provider_calls

    monkeypatch.setenv("GENERATION_PROVIDER_ATTEMPTS_ENABLED", "true")
    monkeypatch.setenv("GENERATION_RESPONSE_RETENTION_ENABLED", "true")
    start = AsyncMock(side_effect=AssertionError("unverified request reached dispatch"))
    monkeypatch.setattr(provider_calls, "start_call", start)
    with TestClient(create_app()) as client:
        response = client.post(
            route,
            content='{"model":"claude-sonnet-5.5","messages":[{"role":"user","content":"x"}]'
            + suffix
            + "}",
            headers={capture.HEADER: "invalid", "content-type": "application/json"},
        )
    assert response.status_code in {403, 409}
    start.assert_not_called()


class SettledLedger(Ledger):
    reconciled_count = 0

    async def reconciled(self):
        self.reconciled_count += 1


@pytest.mark.parametrize("cancelled", [False, True])
async def test_raw_sse_sink_bounds_handoff_and_preserves_single_provider_call(
    monkeypatch, cancelled
):
    import time

    import httpx

    from yleum_gateway.providers import llmgw

    blocked, release = asyncio.Event(), asyncio.Event()
    objects, ledger = Objects(), SettledLedger()
    ledger.capacity_expires_at = int(time.time()) + 15
    original_put = objects.put

    async def slow_put(key, data):
        blocked.set()
        await release.wait()
        await original_put(key, data)

    objects.put = slow_put
    produced, posts = [], []

    class Wire(httpx.SyncByteStream):
        def __iter__(self):
            for index in range(100):
                produced.append(index)
                yield (
                    "data: "
                    + json.dumps({"choices": [{"delta": {"content": "x" * 65536}}]})
                    + "\n\n"
                ).encode()
            yield b'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n'

    def reply(request):
        posts.append(request)
        return httpx.Response(200, stream=Wire())

    monkeypatch.setattr(httpx, "HTTPTransport", lambda **kwargs: httpx.MockTransport(reply))
    monkeypatch.setattr(llmgw, "_key_and_url", lambda: ("synthetic", "http://provider.test"))
    owned = capture.Capture(IDENTITY, "/v1/chat/completions", objects=objects, ledger=ledger)
    context = capture._CURRENT.set(owned)

    async def consume():
        async for _ in llmgw.astream("claude-sonnet-5.5", [], retry_preconnect=False):
            pass

    consumer = asyncio.create_task(consume())
    try:
        await asyncio.wait_for(blocked.wait(), 3)
        await asyncio.sleep(0.05)
        assert len(produced) < 60  # Slow S3 cannot accumulate the entire 100-event response.
        assert owned.stream_queue.qsize() <= 2
        if cancelled:
            consumer.cancel()
        release.set()
        result = await asyncio.wait_for(asyncio.gather(consumer, return_exceptions=True), 5)
        assert len(posts) == 1
        if cancelled:
            assert isinstance(result[0], asyncio.CancelledError)
            assert ledger.committed is None and ledger.unknown
        else:
            assert result == [None] and ledger.committed is not None
    finally:
        release.set()
        await asyncio.gather(consumer, return_exceptions=True)
        if owned.stream_task is not None:
            await asyncio.gather(owned.stream_task, return_exceptions=True)
        capture._CURRENT.reset(context)


async def test_disconnect_does_not_cancel_gateway_owned_receive_store_settle(monkeypatch):
    objects, ledger = Objects(), SettledLedger()
    monkeypatch.setattr(
        capture, "preflight_response_store", AsyncMock(return_value=(objects, ledger))
    )
    accepted, release = asyncio.Event(), asyncio.Event()
    posts = []

    async def operation():
        await capture.before_dispatch(IDENTITY)
        posts.append("physical POST")
        accepted.set()
        await release.wait()
        capture.confirm_response_accounting()
        return {"choices": [{"finish_reason": "stop", "message": {"content": "retained"}}]}

    caller = asyncio.create_task(capture.run_retained(IDENTITY, "/v1/chat/completions", operation))
    await accepted.wait()
    caller.cancel()
    with pytest.raises(asyncio.CancelledError):
        await caller
    release.set()
    await capture.drain_captures()
    assert posts == ["physical POST"]
    assert ledger.committed is not None and ledger.reconciled_count == 1


async def test_sse_done_is_withheld_until_durable_complete_and_accounting(monkeypatch):
    objects, ledger = Objects(), SettledLedger()
    monkeypatch.setattr(
        capture, "preflight_response_store", AsyncMock(return_value=(objects, ledger))
    )

    async def events():
        await capture.begin_stream_capture()
        for event in [
            json.dumps({"choices": [{"delta": {"content": "hello"}, "finish_reason": None}]}),
            json.dumps(
                {"choices": [{"delta": {}, "finish_reason": "stop"}], "usage": {"total_tokens": 2}}
            ),
        ]:
            await capture.receive_stream_event(event)
            yield event
        await capture.receive_stream_event("[DONE]")
        await capture.finish_stream_capture()
        capture.confirm_response_accounting()
        yield "[DONE]"

    async def operation():
        await capture.before_dispatch(IDENTITY)
        return EventSourceResponse(events())

    response = await capture.run_retained(IDENTITY, "/v1/chat/completions", operation)
    received = []
    async for event in response.body_iterator:
        if event == "[DONE]":
            assert ledger.committed is not None and ledger.reconciled_count == 1
        received.append(event)
    assert received[-1] == "[DONE]"
    await capture.drain_captures()


async def test_sse_storage_failure_closes_provider_and_never_yields_done(monkeypatch):
    objects, ledger = Objects(), SettledLedger()
    objects.fail = True
    monkeypatch.setattr(
        capture, "preflight_response_store", AsyncMock(return_value=(objects, ledger))
    )
    settled = []

    async def events():
        try:
            await capture.begin_stream_capture()
            event = json.dumps({"choices": [{"delta": {"content": "x" * (1024 * 1024)}}]})
            for _ in range(8):
                await capture.receive_stream_event(event)
                yield event
            pytest.fail("failed capture did not stop bounded provider handoff")
        finally:
            settled.append("financial-finally")

    stream = events()

    async def operation():
        await capture.before_dispatch(IDENTITY)
        return EventSourceResponse(stream)

    response = await capture.run_retained(IDENTITY, "/v1/chat/completions", operation)
    received = [event async for event in response.body_iterator]
    await capture.drain_captures()
    assert settled == ["financial-finally"]
    assert "[DONE]" not in received and ledger.committed is None
    assert ledger.unknown and ledger.reconciled_count == 0


async def test_complete_http_error_keeps_original_semantics_and_owned_body(monkeypatch):
    objects, ledger = Objects(), SettledLedger()
    monkeypatch.setattr(
        capture, "preflight_response_store", AsyncMock(return_value=(objects, ledger))
    )

    async def operation():
        await capture.before_dispatch(IDENTITY)
        capture.confirm_response_accounting()
        raise HTTPException(
            429, {"error": {"code": "safe_rate_limit"}}, headers={"retry-after": "1"}
        )

    response = await capture.run_retained(IDENTITY, "/v1/chat/completions", operation)
    assert response.status_code == 429 and response.headers["retry-after"] == "1"
    assert json.loads(response.body) == {"detail": {"error": {"code": "safe_rate_limit"}}}
    assert ledger.committed is not None and ledger.reconciled_count == 1


@pytest.mark.parametrize(
    "raw", ["{}", '{"metadata":{"generation_provider_identity":null},"metadata":{}}']
)
def test_runner_auth_remains_required_before_reserved_body_validation(
    monkeypatch, neutralize_lifespan, raw
):
    from tests.test_messages_native import _install_runner_auth, _runner_token
    from yleum_gateway.main import create_app

    redis = _install_runner_auth(monkeypatch)
    monkeypatch.setenv("GENERATION_PROVIDER_ATTEMPTS_ENABLED", "true")
    monkeypatch.setenv("GENERATION_RESPONSE_RETENTION_ENABLED", "true")
    with TestClient(create_app()) as client:
        response = client.post(
            "/v1/project-cell/messages",
            content=raw,
            headers={
                "authorization": "Bearer " + _runner_token(),
                capture.HEADER: "invalid",
                "content-type": "application/json",
            },
        )
    assert response.status_code in {403, 409}
    assert redis.calls == []  # Invalid retention does not consume a native JTI.


@pytest.mark.parametrize("purpose", ["record", "expired", "foreign", "missing"])
def test_private_read_rejects_unbound_auth_before_sql_or_objects(
    monkeypatch, neutralize_lifespan, purpose
):
    from datetime import UTC, datetime, timedelta

    from yleum_gateway.core.generation_response_auth import mint_generation_response_token
    from yleum_gateway.main import create_app
    from yleum_gateway.routers import generation_responses

    monkeypatch.setenv("GENERATION_RESPONSE_RETENTION_ENABLED", "true")
    monkeypatch.setenv("GENERATION_RESPONSE_SIGNING_SECRET", "synthetic-response-purpose-secret-32")
    loader = AsyncMock(side_effect=AssertionError("untrusted read reached owned storage"))
    monkeypatch.setattr(generation_responses, "load_generation_response", loader)
    token = mint_generation_response_token(
        IDENTITY,
        purpose="generation_response.record" if purpose == "record" else "generation_response.read",
        now=datetime.now(UTC) - timedelta(seconds=61) if purpose == "expired" else None,
    )
    target = str(UUID(int=99) if purpose == "foreign" else IDENTITY.attempt_id)
    with TestClient(create_app()) as client:
        response = client.get(
            "/_internal/generation-responses/" + target,
            headers={} if purpose == "missing" else {capture.HEADER: token},
        )
    assert response.status_code == 403
    loader.assert_not_called()
