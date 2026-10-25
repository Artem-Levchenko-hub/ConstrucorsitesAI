"""The API transports font bytes; only the isolated controller parses them."""

import asyncio
import base64
import json
import struct
import subprocess
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from yleum_api.core.errors import ApiError
from yleum_api.services import font_assets, orchestrator_client


def face():
    # Minimal header fixture: transport validation must not invoke a font parser.
    raw = struct.pack(">4s4sIHHIIHHIIIII", b"wOF2", b"\0\1\0\0", 52, 1, 0,
                      100, 4, 1, 0, 0, 0, 0, 0, 0) + b"font"
    return dict(family="Test Font", data=base64.b64encode(raw).decode(), weight=400,
                style="normal", unicode_range="U+20-7E,U+410-44F", license_text="OFL\ntext",
                has_cyrillic=True, has_ruble=False)


def transport(monkeypatch, handler):
    calls, options = [], []
    real_client = httpx.AsyncClient

    async def send(request):
        calls.append(request)
        return await handler(request)

    def client(**kwargs):
        options.append(kwargs)
        return real_client(transport=httpx.MockTransport(send), **kwargs)

    base_url = AsyncMock(return_value="http://core.test")
    monkeypatch.setattr(orchestrator_client, "_base_url", base_url)
    monkeypatch.setattr(orchestrator_client.httpx, "AsyncClient", client)
    monkeypatch.setattr(orchestrator_client, "get_settings", lambda: SimpleNamespace(
        orchestrator_internal_token=SimpleNamespace(get_secret_value=lambda: "test-only-token"),
    ))
    return calls, options, base_url


async def test_binary_core_transport_never_starts_local_python(monkeypatch):
    async def respond(request):
        return httpx.Response(200, json=face())

    calls, options, base_url = transport(monkeypatch, respond)
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: pytest.fail("API spawned subprocess"))
    assert await font_assets._validate(b"raw-font\0bytes") == face()
    assert len(calls) == 1
    assert calls[0].method == "POST" and calls[0].url.path == "/internal/font-validation"
    assert calls[0].content == b"raw-font\0bytes"
    assert calls[0].headers["content-type"] == "application/octet-stream"
    assert calls[0].headers["x-internal-token"] == "test-only-token"
    assert calls[0].headers["accept-encoding"] == "identity"
    assert options == [dict(timeout=30.0, trust_env=False, follow_redirects=False)]
    base_url.assert_awaited_once_with("/internal/font-validation", "core")


@pytest.mark.parametrize("status,expected", [(422, 422), (401, 503), (413, 503),
                                            (429, 503), (500, 503), (503, 503), (302, 503)])
async def test_error_status_is_safe_and_never_retried(monkeypatch, status, expected):
    async def respond(request):
        return httpx.Response(status, text="private diagnostic", headers={"location":"http://other.test"})

    calls, _, _ = transport(monkeypatch, respond)
    with pytest.raises(ApiError) as caught:
        await orchestrator_client.validate_font(b"font")
    assert caught.value.status_code == expected
    expected_code = "validation_failed" if expected == 422 else "orchestrator_unavailable"
    assert caught.value.code == expected_code
    assert "private diagnostic" not in str(caught.value)
    assert len(calls) == 1


@pytest.mark.parametrize("field,value", [
    ("data", "not base64"), ("data", base64.b64encode(b"wOF2" + b"\0" * 48).decode()),
    ("weight", True), ("weight", 0), ("weight", 1001), ("weight", "700 400"),
    ("weight", "400; color:red"), ("style", "oblique"), ("family", "Bad\nFont"),
    ("family", ""), ("license_text", "x" * 30001), ("has_ruble", "false"),
    ("unicode_range", "U+110000"), ("unicode_range", "U+100-20"),
    ("unicode_range", "U+20;src:url(https://example.test)"),
], ids=lambda value: type(value).__name__)
async def test_malformed_face_is_unavailable_not_a_user_font_error(monkeypatch, field, value):
    result = {**face(), field: value}

    async def respond(request):
        return httpx.Response(200, json=result)

    transport(monkeypatch, respond)
    with pytest.raises(ApiError) as caught:
        await orchestrator_client.validate_font(b"font")
    assert caught.value.status_code == 503


@pytest.mark.parametrize("body,headers", [
    (b"[]", {"content-type":"application/json"}),
    (b"{}", {"content-type":"application/json"}),
    (b"not json", {"content-type":"application/json"}),
    (json.dumps(face()).encode(), {"content-type":"text/html"}),
    (json.dumps(face()).encode(), {"content-type":"application/json", "content-length":"2097153"}),
])
async def test_invalid_response_envelope(monkeypatch, body, headers):
    async def respond(request):
        return httpx.Response(200, content=body, headers=headers)

    transport(monkeypatch, respond)
    with pytest.raises(ApiError) as caught:
        await orchestrator_client.validate_font(b"font")
    assert caught.value.status_code == 503


async def test_stream_limit_stops_reading_and_closes(monkeypatch):
    class LargeStream(httpx.AsyncByteStream):
        closed = False
        count = 0

        async def __aiter__(self):
            for _ in range(100):
                self.count += 1
                yield b" " * 65536

        async def aclose(self):
            self.closed = True

    stream = LargeStream()

    async def respond(request):
        return httpx.Response(200, stream=stream, headers={"content-type":"application/json"})

    calls, _, _ = transport(monkeypatch, respond)
    with pytest.raises(ApiError) as caught:
        await orchestrator_client.validate_font(b"font")
    assert caught.value.status_code == 503
    assert stream.closed and stream.count == 33 and len(calls) == 1


@pytest.mark.parametrize("outcome", ["timeout", "cancelled"])
async def test_network_timeout_and_cancellation_no_retry(monkeypatch, outcome):
    async def respond(request):
        if outcome == "cancelled":
            raise asyncio.CancelledError
        raise httpx.ReadTimeout("private endpoint", request=request)

    calls, _, _ = transport(monkeypatch, respond)
    with pytest.raises(asyncio.CancelledError if outcome == "cancelled" else ApiError):
        await orchestrator_client.validate_font(b"font")
    assert len(calls) == 1


@pytest.mark.parametrize("size", [0, 8 * 1024 * 1024 + 1])
async def test_invalid_input_never_calls_controller(monkeypatch, size):
    async def forbidden(request):
        pytest.fail("invalid input reached controller")

    calls, _, _ = transport(monkeypatch, forbidden)
    with pytest.raises(ApiError) as caught:
        await orchestrator_client.validate_font(b"x" * size)
    assert caught.value.status_code == 422 and not calls


async def test_missing_token_fails_before_network(monkeypatch):
    async def forbidden(request):
        pytest.fail("missing token reached controller")

    calls, _, _ = transport(monkeypatch, forbidden)
    monkeypatch.setattr(orchestrator_client, "get_settings", lambda: SimpleNamespace(
        orchestrator_internal_token=None,
    ))
    with pytest.raises(ApiError) as caught:
        await orchestrator_client.validate_font(b"font")
    assert caught.value.status_code == 503 and not calls


async def test_real_normalized_font_is_accepted_without_api_parser(monkeypatch):
    from tests.test_font_assets_api import fixture_font
    from yleum_api.services.font_validation_worker import normalize

    normalized = normalize(fixture_font(), sanitize=False)

    async def respond(request):
        return httpx.Response(200, json=normalized)

    transport(monkeypatch, respond)
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: pytest.fail("API spawned subprocess"))
    assert await font_assets._validate(b"raw") == normalized


@pytest.mark.parametrize("weight", [1, 1000, "100 900", "100.5 900.25"])
async def test_supported_static_and_variable_weights(monkeypatch, weight):
    async def respond(request):
        return httpx.Response(200, json={**face(), "weight": weight})

    transport(monkeypatch, respond)
    assert (await orchestrator_client.validate_font(b"font"))["weight"] == weight


async def test_absolute_deadline_closes_slow_response(monkeypatch):
    class SlowStream(httpx.AsyncByteStream):
        closed = False

        async def __aiter__(self):
            while True:
                await asyncio.sleep(0.001)
                yield b" "

        async def aclose(self):
            self.closed = True

    stream = SlowStream()

    async def respond(request):
        return httpx.Response(200, stream=stream, headers={"content-type": "application/json"})

    transport(monkeypatch, respond)

    def short_deadline(seconds):
        assert seconds == 30.0
        return asyncio.timeout(0.02)

    monkeypatch.setattr(orchestrator_client, "asyncio_timeout", short_deadline)
    with pytest.raises(ApiError) as caught:
        await orchestrator_client.validate_font(b"font")
    assert caught.value.status_code == 503 and stream.closed
