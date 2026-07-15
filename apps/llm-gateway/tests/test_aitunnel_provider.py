"""Unit tests for the aitunnel chat provider (providers/aitunnel.py).

Covers model gating, slug mapping (Omnia dashed id ↔ aitunnel dotted catalog id),
message shaping (vision keep vs text flatten), chain-of-thought stripping, cache
usage extraction, and the two guard paths (unknown model, missing key). The live
upstream happy-path is covered by the deployed end-to-end verification.
"""

from __future__ import annotations

import pytest

from omnia_gateway.core.errors import UpstreamProviderError, ValidationFailedError
from omnia_gateway.providers import aitunnel

_MODEL = "claude-opus-4-8"


def test_is_aitunnel_model() -> None:
    assert aitunnel.is_aitunnel_model(_MODEL) is True
    # Retired / other-provider slugs are not served here.
    assert aitunnel.is_aitunnel_model("claude-opus-4-7") is False
    assert aitunnel.is_aitunnel_model("gpt-5") is False
    assert aitunnel.is_aitunnel_model("deepseek-chat") is False


def test_slug_mapping_round_trip() -> None:
    # Omnia id → aitunnel dotted catalog slug (the native passthrough uses this).
    assert aitunnel.native_slug(_MODEL) == "claude-opus-4.8"
    assert aitunnel.native_slug("unknown-model") == "unknown-model"
    # Upstream response `model` → Omnia id (both surfaces' spellings).
    assert aitunnel.slug_to_omnia("claude-opus-4.8") == _MODEL
    assert aitunnel.slug_to_omnia("anthropic/claude-opus-4.8") == _MODEL
    assert aitunnel.slug_to_omnia("gpt-5") is None


def test_is_vision() -> None:
    assert aitunnel._is_vision(_MODEL) is True
    assert aitunnel._is_vision("some-text-only-model") is False


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
    vis = aitunnel._to_messages(msgs, vision=True)
    assert isinstance(vis[0]["content"], list)  # image block survives for the judge
    txt = aitunnel._to_messages(msgs, vision=False)
    assert txt[0]["content"] == "hi"  # image dropped, text kept


def test_to_messages_rejects_bad_role() -> None:
    with pytest.raises(ValidationFailedError):
        aitunnel._to_messages([{"role": "tool", "content": "x"}])


def test_strip_reasoning() -> None:
    assert aitunnel._strip_reasoning("<think>hmm</think>answer") == "answer"
    # If stripping would empty the text, keep the original.
    assert aitunnel._strip_reasoning("<think>only</think>") == "<think>only</think>"


def test_cached_tokens_extraction() -> None:
    # AITunnel's OpenAI shape (live-verified 15.07).
    assert aitunnel._cached_tokens({"prompt_tokens_details": {"cached_tokens": 42}}) == 42
    # DeepSeek-style fallback field.
    assert aitunnel._cached_tokens({"prompt_cache_hit_tokens": 7}) == 7
    assert aitunnel._cached_tokens({}) == 0


async def test_acompletion_unknown_model_raises() -> None:
    with pytest.raises(ValidationFailedError):
        await aitunnel.acompletion(
            model="not-a-real-model", messages=[{"role": "user", "content": "hi"}]
        )


async def test_acompletion_missing_key_raises() -> None:
    # conftest clears AITUNNEL_API_KEY → _key_and_url raises UpstreamProviderError.
    with pytest.raises(UpstreamProviderError):
        await aitunnel.acompletion(
            model=_MODEL, messages=[{"role": "user", "content": "hi"}]
        )
