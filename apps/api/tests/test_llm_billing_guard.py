from __future__ import annotations

import json
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest

from omnia_api.routers import messages
from omnia_api.services import llm_client


class _AsyncClient:
    def __init__(self, post) -> None:
        self.post = post

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args) -> None:
        return None


class _StreamResponse:
    status_code = 200

    def __init__(self, lines: list[str]) -> None:
        self._lines = lines

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args) -> None:
        return None

    async def aiter_lines(self):
        for line in self._lines:
            yield line


class _StreamClient:
    def __init__(self, lines: list[str]) -> None:
        self._lines = lines

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args) -> None:
        return None

    def stream(self, *_args, **_kwargs):
        return _StreamResponse(self._lines)


async def test_complete_chat_preserves_gateway_ambiguity(monkeypatch) -> None:
    request = httpx.Request("POST", "https://gateway.invalid/v1/chat/completions")
    response = httpx.Response(
        503,
        request=request,
        content=json.dumps({"detail": {"error": {"code": "paid_call_ambiguous"}}}).encode(),
    )

    async def post(*_args, **_kwargs):
        return response

    monkeypatch.setattr(
        llm_client,
        "get_settings",
        lambda: SimpleNamespace(mock_llm=False, llm_gateway_url="https://gateway.invalid"),
    )
    monkeypatch.setattr(llm_client.httpx, "AsyncClient", lambda **_kwargs: _AsyncClient(post))

    with pytest.raises(llm_client.PaidCallAmbiguousError):
        await llm_client.complete_chat([{"role": "user", "content": "hi"}], "model")


async def test_complete_chat_never_retries_lost_response(monkeypatch) -> None:
    calls = 0

    async def post(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        raise httpx.ReadTimeout("lost after send")

    monkeypatch.setattr(
        llm_client,
        "get_settings",
        lambda: SimpleNamespace(mock_llm=False, llm_gateway_url="https://gateway.invalid"),
    )
    monkeypatch.setattr(llm_client.httpx, "AsyncClient", lambda **_kwargs: _AsyncClient(post))

    with pytest.raises(llm_client.PaidCallAmbiguousError):
        await llm_client.complete_chat([{"role": "user", "content": "hi"}], "model")

    assert calls == 1


@pytest.mark.parametrize(
    "content",
    [b"not-json", b"[]"],
)
async def test_complete_chat_treats_malformed_success_as_ambiguous(
    monkeypatch,
    content: bytes,
) -> None:
    request = httpx.Request("POST", "https://gateway.invalid/v1/chat/completions")
    response = httpx.Response(200, request=request, content=content)

    async def post(*_args, **_kwargs):
        return response

    monkeypatch.setattr(
        llm_client,
        "get_settings",
        lambda: SimpleNamespace(mock_llm=False, llm_gateway_url="https://gateway.invalid"),
    )
    monkeypatch.setattr(llm_client.httpx, "AsyncClient", lambda **_kwargs: _AsyncClient(post))

    with pytest.raises(llm_client.PaidCallAmbiguousError):
        await llm_client.complete_chat([{"role": "user", "content": "hi"}], "model")


async def test_complete_chat_treats_unstructured_proxy_503_as_ambiguous(monkeypatch) -> None:
    request = httpx.Request("POST", "https://gateway.invalid/v1/chat/completions")
    response = httpx.Response(503, request=request, content=b"upstream unavailable")

    async def post(*_args, **_kwargs):
        return response

    monkeypatch.setattr(
        llm_client,
        "get_settings",
        lambda: SimpleNamespace(mock_llm=False, llm_gateway_url="https://gateway.invalid"),
    )
    monkeypatch.setattr(llm_client.httpx, "AsyncClient", lambda **_kwargs: _AsyncClient(post))

    with pytest.raises(llm_client.PaidCallAmbiguousError):
        await llm_client.complete_chat([{"role": "user", "content": "hi"}], "model")


@pytest.mark.parametrize(
    "lines",
    [
        ['data: {"choices":[{"delta":{"content":"partial"}}]}'],
        ["data: malformed-json"],
    ],
)
async def test_stream_requires_clean_done_sentinel(monkeypatch, lines: list[str]) -> None:
    monkeypatch.setattr(
        llm_client,
        "get_settings",
        lambda: SimpleNamespace(mock_llm=False, llm_gateway_url="https://gateway.invalid"),
    )
    monkeypatch.setattr(
        llm_client.httpx,
        "AsyncClient",
        lambda **_kwargs: _StreamClient(lines),
    )

    events = [
        event
        async for event in llm_client.stream_chat_completion(
            [{"role": "user", "content": "hi"}],
            "model",
            "user",
            "project",
            "message",
        )
    ]

    assert events[-1]["error_code"] == "paid_call_ambiguous"


async def test_internal_paid_fallback_helpers_abort_on_ambiguity(monkeypatch) -> None:
    async def ambiguous(*_args, **_kwargs):
        yield {
            "error": "response lost",
            "error_code": "paid_call_ambiguous",
        }

    monkeypatch.setattr(messages, "stream_chat_completion", ambiguous)
    report = SimpleNamespace(score=6, max=10, failures=[])

    with pytest.raises(llm_client.PaidCallAmbiguousError):
        await messages._audit_judge_wants_retry(
            html="<main />",
            report=report,
            model="model",
            user_id=uuid4(),
            project_id=uuid4(),
            message_id=uuid4(),
        )

    with pytest.raises(llm_client.PaidCallAmbiguousError):
        await messages._craft_image_prompt(
            "new photo",
            None,
            '<img alt="old">',
            None,
            uuid4(),
            uuid4(),
            uuid4(),
        )
