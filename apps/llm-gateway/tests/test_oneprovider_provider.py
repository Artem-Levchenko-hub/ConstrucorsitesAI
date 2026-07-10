"""Unit tests for the oneprovider chat provider (providers/oneprovider.py).

Covers model gating, message shaping (vision keep vs text flatten), chain-of-thought
stripping, and the two guard paths (unknown model, missing key). The live upstream
happy-path is covered by the deployed end-to-end verification.
"""

from __future__ import annotations

import pytest

from omnia_gateway.core.errors import UpstreamProviderError, ValidationFailedError
from omnia_gateway.providers import oneprovider

_MODEL = "claude-opus-4-8"


def test_is_oneprovider_model() -> None:
    assert oneprovider.is_oneprovider_model(_MODEL) is True
    # Retired / other-provider slugs are not served here.
    assert oneprovider.is_oneprovider_model("claude-opus-4-7") is False
    assert oneprovider.is_oneprovider_model("gpt-5") is False
    assert oneprovider.is_oneprovider_model("deepseek-chat") is False


def test_is_vision() -> None:
    assert oneprovider._is_vision(_MODEL) is True
    assert oneprovider._is_vision("some-text-only-model") is False


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
    vis = oneprovider._to_messages(msgs, vision=True)
    assert isinstance(vis[0]["content"], list)  # image block survives for the judge
    txt = oneprovider._to_messages(msgs, vision=False)
    assert txt[0]["content"] == "hi"  # image dropped, text kept


def test_to_messages_rejects_bad_role() -> None:
    with pytest.raises(ValidationFailedError):
        oneprovider._to_messages([{"role": "tool", "content": "x"}])


def test_strip_reasoning() -> None:
    assert oneprovider._strip_reasoning("<think>hmm</think>answer") == "answer"
    # If stripping would empty the text, keep the original.
    assert oneprovider._strip_reasoning("<think>only</think>") == "<think>only</think>"


async def test_acompletion_unknown_model_raises() -> None:
    with pytest.raises(ValidationFailedError):
        await oneprovider.acompletion(
            model="not-a-real-model", messages=[{"role": "user", "content": "hi"}]
        )


async def test_acompletion_missing_key_raises() -> None:
    # conftest clears ONEPROVIDER_API_KEY → _key_and_url raises UpstreamProviderError.
    with pytest.raises(UpstreamProviderError):
        await oneprovider.acompletion(
            model=_MODEL, messages=[{"role": "user", "content": "hi"}]
        )
