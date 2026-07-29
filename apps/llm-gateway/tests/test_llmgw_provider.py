"""Unit tests for the llmgw chat provider (providers/llmgw.py).

Covers model gating, slug mapping (Omnia id ↔ llmgw canonical catalog id),
message shaping (vision keep vs text flatten), chain-of-thought stripping, cache
usage extraction, and the two guard paths (unknown model, missing key). The live
upstream happy-path is covered by the deployed end-to-end verification.
"""

from __future__ import annotations

import pytest

from omnia_gateway.core.errors import UpstreamProviderError, ValidationFailedError
from omnia_gateway.providers import llmgw

_MODEL = "claude-opus-4-8"


def test_is_llmgw_model() -> None:
    assert llmgw.is_llmgw_model(_MODEL) is True
    # Retired / other-provider slugs are not served here.
    assert llmgw.is_llmgw_model("deepseek-v4-pro") is False
    assert llmgw.is_llmgw_model("claude-opus-4-7") is False
    assert llmgw.is_llmgw_model("gpt-5") is False
    assert llmgw.is_llmgw_model("deepseek-chat") is False


def test_slug_mapping_round_trip() -> None:
    # Omnia id → canonical llmgw catalog slug.
    assert llmgw.native_slug(_MODEL) == "anthropic/claude-opus-4.8"
    assert llmgw.native_slug("unknown-model") == "unknown-model"
    # Upstream response `model` → Omnia id (both surfaces' spellings).
    assert llmgw.slug_to_omnia("claude-opus-4.8") == _MODEL
    assert llmgw.slug_to_omnia("anthropic/claude-opus-4.8") == _MODEL
    assert llmgw.slug_to_omnia("gpt-5") is None


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
