"""A refused primary call is distinct from an uncertain paid response."""

import asyncio
import gzip
import time
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest

from yleum_gateway.core.errors import ModelUnavailableError
from yleum_gateway.services import text_failover
from yleum_gateway.services.generation_identity import GenerationProviderIdentity


@pytest.fixture
def enabled(monkeypatch):
    monkeypatch.setenv("AITUNNEL_TEXT_FALLBACK_ENABLED", "true")
    monkeypatch.setenv("AITUNNEL_TEXT_API_KEY", "synthetic-test-key")


def refusal(status=502, *, url=text_failover.PRIMARY_URL, **kwargs):
    return httpx.Response(status, request=httpx.Request("POST", url), **kwargs)


@pytest.mark.parametrize(("scope", "reason", "tried", "complete", "expected"), [
    ("aitunnel", "preconnect", ["aitunnel"], True, "llmgw"),
    ("aitunnel", "preconnect", ["llmgw", "aitunnel"], True, None),
    ("llmgw", "upstream_refused", ["aitunnel", "llmgw"], True, None),
    ("aitunnel", "preconnect", ["aitunnel"], False, None),
    ("aitunnel", "rate_limit_refused", ["aitunnel"], True, "aitunnel"),
])
async def test_durable_retry_cannot_revisit_provider_after_confirmed_alternate(
    enabled, monkeypatch, scope, reason, tried, complete, expected,
):
    parent = {
        "phase": "closed", "status": "failed", "receipt_hash": "verified",
        "terminal_reason": reason, "provider_scope": scope,
        "tried_scopes": tried, "chain_complete": complete,
    }

    @asynccontextmanager
    async def acquire():
        yield SimpleNamespace(fetchrow=AsyncMock(return_value=parent))

    monkeypatch.setattr(text_failover, "get_pool", lambda: SimpleNamespace(acquire=acquire))
    identity = GenerationProviderIdentity(uuid4(), uuid4(), uuid4(), uuid4(), "a" * 64, "b" * 64)
    if expected is None:
        with pytest.raises(ModelUnavailableError):
            await text_failover.select_scope(identity, text_failover.MODEL)
    else:
        assert await text_failover.select_scope(identity, text_failover.MODEL) == expected


@pytest.mark.parametrize("status", [500,502,503,504])
def test_explicit_primary_refusal_is_bounded_and_provider_specific(enabled, status):
    candidate = text_failover.primary_refusal_candidate(refusal(status,json={"error": "Bad gateway"}), "claude-sonnet-5.5")
    assert candidate == {
        "provider_failure_kind": "upstream_refused", "provider_charge_ambiguous": False,
        "retry_safe": True, "retry_policy": "llmgw_unavailable_v1", "upstream_http_status": status,
    }


@pytest.mark.parametrize(
    ("status", "body"),
    [
        (401, {"error": {"type": "authentication_error"}}),
        (402, {"error": {"code": "insufficient_balance", "type": "insufficient_balance"}}),
        (422, {"detail": [{"loc": ["body", "model"], "type": "value_error"}]}),
        (429, {"error": {"type": "rate_limit_exceeded"}}),
        (501, {"error": "Provider refused the request"}),
    ],
)
def test_any_explicit_primary_http_refusal_can_route_to_backup(enabled, status, body):
    assert text_failover.primary_refusal_candidate(refusal(status, json=body), text_failover.MODEL) == {
        "provider_failure_kind": "upstream_refused", "provider_charge_ambiguous": False,
        "retry_safe": True, "retry_policy": "llmgw_http_refused_v1", "upstream_http_status": status,
    }


@pytest.mark.parametrize("kwargs", [
    {"status": 399}, {"url": "https://api.aitunnel.ru/v1/chat/completions"},
    {"json": {"error": "failed", "usage": {"cost_rub": 0}}},
    {"json": {"choices": [{"message": {"content": "partial"}}]}},
    {"headers": {"x-cost-rub": "0"}}, {"content": b"x" * 16385},
    {"json": {"error": {"usage": {"completion_tokens": 1}}}},
    {"content": b'{"error":{"usage":{"cost_rub":1}},"error":"Bad gateway"}'},
    {"content": b'data: {"choices":[{"delta":{"content":"accepted"}}]}\nBad gateway'},
    {"status": 402, "json": {"detail": "No funds", "usage": {}}},
    {"status": 402, "headers": {"content-type": "text/event-stream"}},
    {"status": 422, "json": {"error": {"completion_tokens": 7}}},
    {"status": 422, "json": {"error": {"provider_cost_rub": "1.20"}}},
    {"status": 422, "json": {"error": {"delta": {"text": "partial"}}}},
    {"status": 422, "json": {"error": {"message": "failed", "charged": True}}},
    {"status": 422, "json": {"error": {"message": "failed", "billed": True}}},
    {"status": 422, "json": {"error": {"message": "failed", "charge": {"amount": "1.20"}}}},
    {"status": 422, "json": {"error": {"message": "failed", "provider_charge_ambiguous": True}}},
    {"status": 422, "content": b'{"error": NaN}'},
])
def test_uncertain_or_other_provider_response_is_not_refusal(enabled, kwargs):
    assert text_failover.primary_refusal_candidate(refusal(**kwargs), "claude-sonnet-5.5") is None


def test_only_explicitly_enabled_same_model_can_use_backup(enabled, monkeypatch):
    response = refusal(json={"error": "Bad gateway"})
    assert text_failover.primary_refusal_candidate(response, "claude-sonnet-5") is None
    monkeypatch.setenv("AITUNNEL_TEXT_FALLBACK_ENABLED", "false")
    from yleum_gateway.core.config import reset_settings_cache
    reset_settings_cache()
    assert text_failover.primary_refusal_candidate(response, "claude-sonnet-5.5") is None


def test_large_streamed_refusal_is_stopped_before_whole_body_is_read():
    produced = []
    class Body(httpx.SyncByteStream):
        def __iter__(self):
            for _ in range(1000):
                produced.append(4096)
                yield b"x" * 4096
    response = refusal(stream=Body())
    assert text_failover.read_refusal_body(response) is None
    assert sum(produced) == 20_480


@pytest.mark.parametrize("native", [False, True])
async def test_interrupted_backup_headers_do_not_claim_llmgw_money(enabled, monkeypatch, native):
    from yleum_gateway.core.errors import UpstreamProviderError
    from yleum_gateway.providers import llmgw
    from yleum_gateway.routers import messages_native

    class BrokenBody(httpx.AsyncByteStream):
        async def __aiter__(self):
            raise httpx.ReadTimeout("body interrupted")
            yield b""

    def reply(request):
        return httpx.Response(200, headers={"x-cost-usd": "99", "x-llmgw-cost-rub": "77"}, stream=BrokenBody())
    monkeypatch.setattr(httpx, "AsyncHTTPTransport", lambda **kwargs: httpx.MockTransport(reply))
    evidence = {}
    if native:
        with pytest.raises(httpx.ReadTimeout):
            async with messages_native.open_aitunnel_response(
                "https://api.aitunnel.ru/v1/chat/completions", {}, {},
                deadline=asyncio.get_running_loop().time() + 5, evidence=evidence,
            ) as response:
                await response.aread()
        proof = evidence
    else:
        with pytest.raises(UpstreamProviderError) as caught:
            await llmgw.acompletion(model="claude-sonnet-5.5", messages=[], max_tokens=128,
                                    provider_scope="aitunnel", retry_preconnect=False, evidence=evidence)
        proof = caught.value.details
    assert proof.get("provider_cost_rub") is None
    assert proof.get("provider_cost_usd") is None


async def test_reserve_checks_deadline_after_request_preparation(monkeypatch):
    from yleum_gateway.core.errors import DispatchDeadlineError
    from yleum_gateway.providers.native_stream import open_aitunnel_response

    sent = []
    build_request = httpx.AsyncClient.build_request

    def slow_build(self, *args, **kwargs):
        time.sleep(0.06)
        return build_request(self, *args, **kwargs)

    def reply(request):
        sent.append(request)
        return httpx.Response(200, json={})

    monkeypatch.setattr(httpx.AsyncClient, "build_request", slow_build)
    monkeypatch.setattr(httpx, "AsyncHTTPTransport", lambda **_: httpx.MockTransport(reply))
    with pytest.raises(DispatchDeadlineError):
        async with open_aitunnel_response(
            "https://api.aitunnel.ru/v1/chat/completions", {}, {},
            deadline=asyncio.get_running_loop().time() + 0.04, evidence={},
        ):
            pytest.fail("expired reserve request opened a response")
    assert sent == []


async def test_reserve_closes_a_trickling_body_at_absolute_deadline(monkeypatch):
    from yleum_gateway.providers.native_stream import open_aitunnel_response

    class Trickle(httpx.AsyncByteStream):
        closed = False
        chunks = 0

        async def __aiter__(self):
            while True:
                self.chunks += 1
                yield b"incomplete"
                await asyncio.sleep(0.01)

        async def aclose(self):
            self.closed = True

    body = Trickle()
    monkeypatch.setattr(httpx, "AsyncHTTPTransport", lambda **_: httpx.MockTransport(
        lambda request: httpx.Response(200, stream=body),
    ))
    with pytest.raises(TimeoutError):
        async with open_aitunnel_response(
            "https://api.aitunnel.ru/v1/chat/completions", {}, {},
            deadline=asyncio.get_running_loop().time() + 0.25, evidence={},
        ) as response:
            await response.aread()
    assert body.chunks > 1
    assert body.closed


@pytest.mark.parametrize("status", [200, 302, 399, 600])
def test_non_error_status_never_authorizes_backup(enabled, status):
    assert (
        text_failover.primary_refusal_candidate(
            refusal(status, json={"error": "Unavailable"}), text_failover.MODEL
        )
        is None
    )


@pytest.mark.parametrize("status", [500, 502, 503, 504])
@pytest.mark.parametrize(
    "kwargs",
    [
        {"json": {"error": "failed", "usage": {}}},
        {"json": {"error": {"content": "partial"}}},
        {"headers": {"x-cost-usd": "0"}, "json": {"error": "failed"}},
        {"content": b"x" * 16385},
        {"content": b'data: {"content":"partial"}\nBad gateway'},
    ],
)
def test_temporary_status_never_discards_uncertain_evidence(enabled, status, kwargs):
    assert text_failover.primary_refusal_candidate(refusal(status, **kwargs), text_failover.MODEL) is None


@pytest.mark.parametrize("status", [402, 422, 500, 502, 503, 504])
@pytest.mark.parametrize("mode", ["sync", "collect", "sse"])
async def test_actual_transport_recognizes_full_refusal_without_retry(
    enabled, monkeypatch, status, mode
):
    from yleum_gateway.core.errors import UpstreamProviderError
    from yleum_gateway.providers import llmgw

    calls = []

    def reply(request):
        calls.append(request)
        if status == 402:
            return httpx.Response(
                status, headers={"content-encoding": "gzip"},
                content=gzip.compress(b'{"error":{"code":"insufficient_balance"}}'),
            )
        return httpx.Response(status, json={"error": "temporarily unavailable"})

    monkeypatch.setattr(
        llmgw, "_key_and_url", lambda *args: ("synthetic", text_failover.PRIMARY_URL)
    )
    monkeypatch.setattr(httpx, "HTTPTransport", lambda **kwargs: httpx.MockTransport(reply))
    monkeypatch.setattr(httpx, "AsyncHTTPTransport", lambda **kwargs: httpx.MockTransport(reply))
    with pytest.raises(UpstreamProviderError) as caught:
        if mode == "sse":
            async for _ in llmgw.astream(
                model=text_failover.MODEL, messages=[], receipt={}, max_tokens=128
            ):
                pytest.fail("refusal emitted output")
        else:
            await llmgw.acompletion(
                model=text_failover.MODEL, messages=[], max_tokens=128 if mode == "sync" else None
            )
    assert len(calls) == 1
    assert caught.value.details["_overload_retry_candidate"]["upstream_http_status"] == status
    assert caught.value.details["provider_charge_ambiguous"] is False


@pytest.mark.parametrize(
    "status,title",
    [
        (500, "Internal Server Error"),
        (502, "Bad Gateway"),
        (503, "Service Unavailable"),
        (504, "Gateway Timeout"),
    ],
)
@pytest.mark.parametrize("document", ["plain", "nginx"])
def test_canonical_status_specific_refusal_documents(enabled, status, title, document):
    text = (
        f"{status} {title}"
        if document == "plain"
        else f"<html><head><title>{status} {title}</title></head><body><center><h1>{status} {title}</h1></center><hr><center>nginx/1.24.0</center></body></html>"
    )
    assert text_failover.primary_refusal_candidate(refusal(status, text=text), text_failover.MODEL)
    wrong = refusal(status, text=text.replace(title, "Wrong status text"))
    assert text_failover.primary_refusal_candidate(wrong, text_failover.MODEL) is None

@pytest.mark.parametrize("status,title", [(503,"Service Temporarily Unavailable"),(504,"Gateway Time-out")])
def test_actual_nginx_documents_are_exact_and_cannot_hide_output(enabled,status,title):
    document=f"<html><head><title>{status} {title}</title></head><body><center><h1>{status} {title}</h1></center><hr><center>nginx</center></body></html>"
    assert text_failover.primary_refusal_candidate(refusal(status,text=document),text_failover.MODEL)
    for bad in (document.replace(f"<h1>{status}","<h1>500"),document.replace(f"<h1>{status} {title}",f"<h1>{status} Wrong title"),'data: {"content":"partial"}\n'+document):
        assert text_failover.primary_refusal_candidate(refusal(status,text=bad),text_failover.MODEL) is None
