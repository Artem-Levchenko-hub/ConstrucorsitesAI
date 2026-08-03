"""Unit tests for the llmgw chat provider (providers/llmgw.py).

Covers model gating, slug mapping (Omnia id ↔ llmgw canonical catalog id),
message shaping (vision keep vs text flatten), chain-of-thought stripping, cache
usage extraction, and the two guard paths (unknown model, missing key). The live
upstream happy-path is covered by the deployed end-to-end verification.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from omnia_gateway.core.config import reset_settings_cache
from omnia_gateway.core.errors import (
    PaidCallAmbiguousError,
    UpstreamProviderError,
    ValidationFailedError,
)
from omnia_gateway.providers import llmgw

_MODEL = "claude-sonnet-5"


def test_is_llmgw_model() -> None:
    assert llmgw.is_llmgw_model(_MODEL) is True
    assert llmgw.is_llmgw_model("gemini-3.1-pro-preview-customtools") is True
    # Retired / other-provider slugs are not served here.
    assert llmgw.is_llmgw_model("deepseek-v4-pro") is False
    assert llmgw.is_llmgw_model("claude-opus-4-7") is False
    assert llmgw.is_llmgw_model("gpt-5") is False
    assert llmgw.is_llmgw_model("deepseek-chat") is False


def test_slug_mapping_round_trip() -> None:
    # Omnia id → canonical llmgw catalog slug.
    assert llmgw.native_slug(_MODEL) == "claude-sonnet-5"
    assert llmgw.native_slug("unknown-model") == "unknown-model"
    # Upstream response `model` → Omnia id (both surfaces' spellings).
    assert llmgw.slug_to_omnia("claude-sonnet-5") == _MODEL
    assert llmgw.slug_to_omnia("anthropic/claude-sonnet-5") == _MODEL
    assert llmgw.native_slug("gemini-3.1-pro-preview-customtools") == (
        "google/gemini-3.1-pro-preview-customtools"
    )
    assert llmgw.slug_to_omnia("google/gemini-3.1-pro-preview-customtools") == (
        "gemini-3.1-pro-preview-customtools"
    )
    assert llmgw.slug_to_omnia("gpt-5") is None


def test_sonnet_and_gemini_use_independent_provider_credentials(monkeypatch) -> None:
    monkeypatch.setenv("AITUNNEL_API_KEY", "aitunnel-key")
    monkeypatch.setenv("AITUNNEL_BASE_URL", "https://aitunnel.test/v1")
    monkeypatch.setenv("LLMGW_API_KEY", "llmgw-key")
    monkeypatch.setenv("LLMGW_BASE_URL", "https://llmgw.test/v1")
    reset_settings_cache()

    assert llmgw._key_and_url("claude-sonnet-5") == (
        "aitunnel-key",
        "https://aitunnel.test/v1/chat/completions",
    )
    assert llmgw._key_and_url("gemini-3.1-pro-preview-customtools") == (
        "llmgw-key",
        "https://llmgw.test/v1/chat/completions",
    )


def test_is_vision() -> None:
    assert llmgw._is_vision(_MODEL) is True
    assert llmgw._is_vision("some-text-only-model") is False


def test_to_messages_vision_keeps_blocks_text_flattens() -> None:
    msgs = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "hi"},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}},
            ],
        }
    ]
    vis = llmgw._to_messages(msgs, vision=True)
    assert isinstance(vis[0]["content"], list)  # image block survives for the judge
    txt = llmgw._to_messages(msgs, vision=False)
    assert txt[0]["content"] == "hi"  # image dropped, text kept


def test_to_messages_rejects_bad_role() -> None:
    with pytest.raises(ValidationFailedError):
        llmgw._to_messages([{"role": "tool", "content": "x"}])


def test_strip_reasoning() -> None:
    assert llmgw._strip_reasoning("<think>hmm</think>answer") == "answer"
    # If stripping would empty the text, keep the original.
    assert llmgw._strip_reasoning("<think>only</think>") == "<think>only</think>"


def test_cached_tokens_extraction() -> None:
    assert llmgw._cached_tokens({"prompt_tokens_details": {"cached_tokens": 42}}) == 42
    # DeepSeek-style fallback field.
    assert llmgw._cached_tokens({"prompt_cache_hit_tokens": 7}) == 7
    assert llmgw._cached_tokens({}) == 0


async def test_acompletion_unknown_model_raises() -> None:
    with pytest.raises(ValidationFailedError):
        await llmgw.acompletion(
            model="not-a-real-model", messages=[{"role": "user", "content": "hi"}]
        )


async def test_acompletion_missing_key_raises() -> None:
    # conftest clears LLMGW_API_KEY → _key_and_url raises UpstreamProviderError.
    with pytest.raises(UpstreamProviderError):
        await llmgw.acompletion(model=_MODEL, messages=[{"role": "user", "content": "hi"}])


class _ClientContext:
    def __init__(self, post) -> None:
        self.post = post

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        return None


async def test_acompletion_never_retries_ambiguous_read_failure(monkeypatch) -> None:
    calls = 0

    def post(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        raise httpx.ReadTimeout("lost after send")

    monkeypatch.setattr(llmgw, "_key_and_url", lambda _model: ("key", "https://provider.invalid"))
    monkeypatch.setattr(llmgw.httpx, "Client", lambda **_kwargs: _ClientContext(post))

    with pytest.raises(PaidCallAmbiguousError):
        await llmgw.acompletion(model=_MODEL, messages=[{"role": "user", "content": "hi"}])

    assert calls == 1


async def test_astream_never_retries_ambiguous_protocol_failure(monkeypatch) -> None:
    calls = 0

    class StreamContext:
        def __enter__(self):
            nonlocal calls
            calls += 1
            raise httpx.RemoteProtocolError("response lost")

        def __exit__(self, *_args) -> None:
            return None

    class Client(_ClientContext):
        def __init__(self, **_kwargs: Any) -> None:
            super().__init__(lambda: None)

        def stream(self, *_args, **_kwargs):
            return StreamContext()

    monkeypatch.setattr(llmgw, "_key_and_url", lambda _model: ("key", "https://provider.invalid"))
    monkeypatch.setattr(llmgw.httpx, "Client", Client)

    stream = llmgw.astream(model=_MODEL, messages=[{"role": "user", "content": "hi"}])
    with pytest.raises(PaidCallAmbiguousError):
        await anext(stream)

    assert calls == 1


class _StreamResponse:
    status_code = 200

    def __init__(self, lines: list[str], *, status_code: int = 200) -> None:
        self._lines = lines
        self.status_code = status_code

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        return None

    def iter_lines(self):
        yield from self._lines

    def read(self) -> bytes:
        return b"provider diagnostic"


async def test_astream_eof_without_terminal_marker_is_ambiguous(monkeypatch) -> None:
    class Client(_ClientContext):
        def __init__(self, **_kwargs: Any) -> None:
            super().__init__(lambda: None)

        def stream(self, *_args, **_kwargs):
            return _StreamResponse(
                ['data: {"choices":[{"delta":{"content":"partial"}}]}']
            )

    monkeypatch.setattr(llmgw, "_key_and_url", lambda _model: ("key", "https://provider.invalid"))
    monkeypatch.setattr(llmgw.httpx, "Client", Client)

    stream = llmgw.astream(model=_MODEL, messages=[{"role": "user", "content": "hi"}])
    assert await anext(stream) == ("partial", _MODEL)
    with pytest.raises(PaidCallAmbiguousError):
        await anext(stream)


async def test_astream_503_is_ambiguous(monkeypatch) -> None:
    class Client(_ClientContext):
        def __init__(self, **_kwargs: Any) -> None:
            super().__init__(lambda: None)

        def stream(self, *_args, **_kwargs):
            return _StreamResponse([], status_code=503)

    monkeypatch.setattr(llmgw, "_key_and_url", lambda _model: ("key", "https://provider.invalid"))
    monkeypatch.setattr(llmgw.httpx, "Client", Client)

    stream = llmgw.astream(model=_MODEL, messages=[{"role": "user", "content": "hi"}])
    with pytest.raises(PaidCallAmbiguousError):
        await anext(stream)


async def test_astream_empty_terminal_completion_is_ambiguous(monkeypatch) -> None:
    class Client(_ClientContext):
        def __init__(self, **_kwargs: Any) -> None:
            super().__init__(lambda: None)

        def stream(self, *_args, **_kwargs):
            return _StreamResponse(["data: [DONE]"])

    monkeypatch.setattr(llmgw, "_key_and_url", lambda _model: ("key", "https://provider.invalid"))
    monkeypatch.setattr(llmgw.httpx, "Client", Client)

    stream = llmgw.astream(model=_MODEL, messages=[{"role": "user", "content": "hi"}])
    with pytest.raises(PaidCallAmbiguousError):
        await anext(stream)


async def test_acompletion_malformed_2xx_is_ambiguous(monkeypatch) -> None:
    class Response:
        def raise_for_status(self) -> None:
            return None

        def json(self):
            raise ValueError("truncated json")

    monkeypatch.setattr(llmgw, "_key_and_url", lambda _model: ("key", "https://provider.invalid"))
    monkeypatch.setattr(
        llmgw.httpx,
        "Client",
        lambda **_kwargs: _ClientContext(lambda *_args, **_kwargs: Response()),
    )

    with pytest.raises(PaidCallAmbiguousError):
        await llmgw.acompletion(model=_MODEL, messages=[{"role": "user", "content": "hi"}])
