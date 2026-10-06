"""Unit tests for the llmgw chat provider (providers/llmgw.py).

Covers model gating, slug mapping (Omnia id ↔ llmgw canonical catalog id),
message shaping (vision keep vs text flatten), chain-of-thought stripping, cache
usage extraction, and the two guard paths (unknown model, missing key). The live
upstream happy-path is covered by the deployed end-to-end verification.
"""

from __future__ import annotations

import httpx
import pytest

from yleum_gateway.core.errors import UpstreamProviderError, ValidationFailedError
from yleum_gateway.providers import llmgw

_MODEL = "gemini-3.1-pro-preview-customtools"


def test_is_llmgw_model() -> None:
    assert llmgw.is_llmgw_model(_MODEL) is True
    assert llmgw.is_llmgw_model("claude-sonnet-5") is True
    # Retired / other-provider slugs are not served here.
    assert llmgw.is_llmgw_model("deepseek-v4-pro") is False
    assert llmgw.is_llmgw_model("claude-opus-4-7") is False
    assert llmgw.is_llmgw_model("gpt-5") is False
    assert llmgw.is_llmgw_model("deepseek-chat") is False


def test_slug_mapping_round_trip() -> None:
    # Omnia id → canonical llmgw catalog slug.
    assert llmgw.native_slug(_MODEL) == "google/gemini-3.1-pro-preview-customtools"
    assert llmgw.native_slug("unknown-model") == "unknown-model"
    # Upstream response `model` → Omnia id (both surfaces' spellings).
    assert llmgw.slug_to_omnia("gemini-3.1-pro-preview-customtools") == _MODEL
    assert llmgw.slug_to_omnia("google/gemini-3.1-pro-preview-customtools") == _MODEL
    assert llmgw.native_slug("claude-sonnet-5") == "anthropic/claude-sonnet-5"
    assert llmgw.slug_to_omnia("claude-sonnet-5") == "claude-sonnet-5"
    assert llmgw.slug_to_omnia("anthropic/claude-sonnet-5") == "claude-sonnet-5"
    assert llmgw.slug_to_omnia("gpt-5") is None


def test_is_vision() -> None:
    assert llmgw._is_vision(_MODEL) is True
    assert llmgw._is_vision("claude-sonnet-5") is True
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


@pytest.mark.parametrize("reported", [
    {"prompt_tokens_details": {"cached_tokens": 600, "cache_creation_tokens": 200}},
    {"cache_read_input_tokens": 600, "cache_creation_input_tokens": 200},
])
async def test_acompletion_preserves_cache_read_and_write_usage(monkeypatch, reported):
    from yleum_gateway.core.config import reset_settings_cache

    monkeypatch.setenv("LLMGW_API_KEY", "test-only-provider-key")
    reset_settings_cache()

    def reply(request):
        return httpx.Response(200, json={
            "choices": [{"message": {"content": "answer"}}],
            "usage": {"prompt_tokens": 1000, "completion_tokens": 100, **reported},
        })

    monkeypatch.setattr(httpx, "HTTPTransport", lambda *a, **kw: httpx.MockTransport(reply))
    result = await llmgw.acompletion(model=_MODEL, messages=[{"role": "user", "content": "hi"}])
    assert result["usage"]["prompt_cache_hit_tokens"] == 600
    assert result["usage"]["cache_creation_input_tokens"] == 200


async def test_acompletion_missing_key_raises() -> None:
    # conftest clears LLMGW_API_KEY → _key_and_url raises UpstreamProviderError.
    with pytest.raises(UpstreamProviderError):
        await llmgw.acompletion(model=_MODEL, messages=[{"role": "user", "content": "hi"}])


@pytest.mark.parametrize("money", ["valid", "invalid"])
@pytest.mark.parametrize("failure", ["unknown_model", "malformed_choices"])
async def test_failed_adapter_preserves_safe_reported_receipt(monkeypatch, money, failure):
    from yleum_gateway.core.config import reset_settings_cache

    monkeypatch.setenv("LLMGW_API_KEY", "test-only-provider-key")
    reset_settings_cache()
    data = {
        "id": "completion-id", "model": "unknown/provider-model",
        "choices": [{"message": {"content": "private answer"}}],
        "usage": {"prompt_tokens": 100, "completion_tokens": 10,
                  "cost_rub": "2.50" if money == "valid" else "NaN",
                  "cost_usd": ".025" if money == "valid" else {"private": "data"}},
    }
    if failure == "malformed_choices":
        data["choices"] = []
    monkeypatch.setattr(httpx, "HTTPTransport", lambda *a, **kw: httpx.MockTransport(
        lambda request: httpx.Response(200, json=data, headers={"x-llmgw-request-id": "canonical-id"}),
    ))
    with pytest.raises(UpstreamProviderError) as raised:
        await llmgw.acompletion(model=_MODEL, messages=[{"role": "user", "content": "hi"}])
    details = raised.value.details
    assert details["provider_request_id"] == "canonical-id"
    assert details["provider_cost_rub"] == ("2.50" if money == "valid" else None)
    assert details["provider_cost_usd"] == ("0.025" if money == "valid" else None)
    assert "private" not in repr(details)


@pytest.mark.parametrize("failure", ["invalid_json", "list_json", "null_json", "dict_content",
                                     "list_content", "bad_created", "bad_metadata", "bad_usage",
                                     "bad_model", "http_400", "http_500"])
async def test_any_received_malformed_response_keeps_header_receipt_without_retry(monkeypatch, failure):
    from yleum_gateway.core.config import reset_settings_cache

    monkeypatch.setenv("LLMGW_API_KEY", "test-only-provider-key")
    reset_settings_cache()
    data = {"id": "completion-id", "model": _MODEL,
            "choices": [{"message": {"content": "private answer"}}],
            "usage": {"prompt_tokens": 3, "completion_tokens": 2}}
    if failure == "dict_content":
        data["choices"][0]["message"]["content"] = {}
    elif failure == "list_content":
        data["choices"][0]["message"]["content"] = ["private content"]
    elif failure == "bad_created":
        data["created"] = {"private": "value"}
    elif failure == "bad_metadata":
        data["metadata"] = ["private metadata"]
    elif failure == "bad_usage":
        data["usage"] = ["private usage"]
    elif failure == "bad_model":
        data["model"] = {"private": "model"}
    headers = {"x-llmgw-request-id": "canonical-header-id", "x-llmgw-cost-rub": "2.50",
               "x-cost-usd": ".025"}
    calls = []

    def reply(request):
        calls.append(request)
        if failure == "invalid_json":
            return httpx.Response(200, content=b"private-invalid-json", headers=headers)
        if failure == "list_json":
            return httpx.Response(200, json=["private response"], headers=headers)
        if failure == "null_json":
            return httpx.Response(200, content=b"null", headers=headers)
        status = int(failure[5:]) if failure.startswith("http_") else 200
        return httpx.Response(status, json=data, headers=headers)

    monkeypatch.setattr(httpx, "HTTPTransport", lambda *a, **kw: httpx.MockTransport(reply))
    with pytest.raises(UpstreamProviderError) as raised:
        await llmgw.acompletion(model=_MODEL, messages=[{"role": "user", "content": "hi"}])
    assert len(calls) == 1
    details = raised.value.details
    assert details["provider_request_id"] == "canonical-header-id"
    assert details["provider_cost_rub"] == "2.50"
    assert details["provider_cost_usd"] == "0.025"
    assert "private" not in repr(details)
