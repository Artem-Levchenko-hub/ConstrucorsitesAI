"""Exact Sonnet aliases, current quotes, and immutable receipt precedence."""

import json
from decimal import Decimal
from unittest.mock import AsyncMock

import httpx
import pytest

from yleum_gateway.core.config import reset_settings_cache
from yleum_gateway.core.errors import ModelNotFoundError, UpstreamProviderError
from yleum_gateway.providers import llmgw
from yleum_gateway.services import pricing
from yleum_gateway.services.cache_pricing import anthropic_cache_write_factor
from yleum_gateway.services.prompt_cache import apply_anthropic_cache

LATEST = "~anthropic/claude-sonnet-latest"
MODELS = [(LATEST, LATEST), ("claude-sonnet-5.5", "anthropic/claude-sonnet-5.5")]


def quote(slug):
    amounts = ("349.8078", "1749.0389", "17.4904")
    return {
        "items": [
            {
                "model_name": slug,
                "pricing": {
                    "currency": "RUB",
                    "unit": "1M_tokens",
                    **dict(
                        zip(
                            (
                                "input_rub_per_mtok",
                                "output_rub_per_mtok",
                                "cached_input_rub_per_mtok",
                            ),
                            amounts,
                            strict=True,
                        )
                    ),
                    "rates": [
                        {
                            "billable": kind,
                            "unit": "token",
                            "quantity": "1000000",
                            "variant": "",
                            "price_rub": amount,
                            "is_from_price": False,
                        }
                        for kind, amount in zip(
                            ("input_text", "output_text", "cached_input"), amounts, strict=True
                        )
                    ],
                },
            }
        ]
    }


@pytest.mark.parametrize("model,slug", MODELS)
async def test_exact_routing_and_response_identity(monkeypatch, model, slug):
    monkeypatch.setenv("LLMGW_API_KEY", "test-only-provider-key")
    reset_settings_cache()
    calls = []

    def reply(request):
        calls.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "model": slug,
                "choices": [{"message": {"content": "answer"}}],
                "usage": {
                    "prompt_tokens": 1000,
                    "completion_tokens": 100,
                    "cache_read_input_tokens": 600,
                },
            },
        )

    monkeypatch.setattr(httpx, "HTTPTransport", lambda *a, **kw: httpx.MockTransport(reply))
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "image_url", "image_url": {"url": "https://example.invalid/fixture.png"}}
            ],
        }
    ]
    result = await llmgw.acompletion(model=model, messages=messages)
    assert len(calls) == 1
    assert calls[0]["model"] == slug
    assert calls[0]["messages"] == messages
    assert "thinking" not in calls[0]
    assert result["model"] == model
    assert result["usage"]["prompt_cache_hit_tokens"] == 600
    assert llmgw.slug_to_omnia(model) == model


@pytest.mark.parametrize(
    "unknown",
    [
        "anthropic/claude-sonnet-latest",
        "claude-sonnet-latest",
        "anthropic/~anthropic/claude-sonnet-latest",
        "other/claude-sonnet-5.5",
        "claude-sonnet-5.50",
    ],
)
def test_near_aliases_fail_closed(unknown):
    assert not llmgw.is_llmgw_model(unknown)
    assert llmgw.slug_to_omnia(unknown) is None
    assert pricing._parse_catalog_prices(quote(unknown)) == {}
    with pytest.raises(UpstreamProviderError):
        llmgw.resolve_response_model(unknown, requested=LATEST, provider_request_id="fixture")
    with pytest.raises(ModelNotFoundError):
        pricing.calculate_cost_rub(unknown, 1000, 100)


@pytest.mark.parametrize("model,slug", MODELS)
async def test_exact_catalog_cache_rate_and_receipt_precedence(monkeypatch, model, slug):
    parsed = pricing._parse_catalog_prices(quote(slug))
    assert parsed == {model: (Decimal("349.8078"), Decimal("1749.0389"), Decimal("17.4904"))}
    fetch = AsyncMock(return_value=parsed)
    monkeypatch.setattr(pricing, "_fetch_catalog_prices", fetch)
    cost, evidence = await pricing.resolve_request_cost(
        model, tokens_in=1_000_000, tokens_out=0, cache_read_tokens=1_000_000
    )
    assert cost == Decimal("17.4904")
    assert evidence["basis"] == "provider_catalog_estimate"
    assert pricing.validate_cost_provenance(evidence, cost_rub=cost) == evidence
    fetch.reset_mock()
    receipt, actual = await pricing.resolve_request_cost(
        model,
        tokens_in=1_000_000,
        tokens_out=0,
        reported=pricing.ReportedCost(Decimal("0.20"), rub_source="usage:cost_rub"),
    )
    assert receipt == Decimal("0.20")
    assert actual["basis"] == "provider_reported_rub"
    fetch.assert_not_awaited()


@pytest.mark.parametrize("model,slug", MODELS)
async def test_current_fallback_cache_factor_and_metadata(model, slug):
    cost, evidence = await pricing.resolve_request_cost(
        model, tokens_in=1000, tokens_out=100, cache_read_tokens=600
    )
    assert cost == Decimal("0.3253")
    assert evidence["tariff"]["cache_read_factor"] == "0.05"
    assert evidence["basis"] == "token_tariff"
    meta = next(item for item in pricing.list_models() if item["id"] == model)
    assert meta["context_window"] == 1_000_000
    assert meta["provider"] == "anthropic"
    assert "multimodal" in meta["recommended_for"]


@pytest.mark.parametrize("model,slug", MODELS)
@pytest.mark.parametrize("ttl,factor", [("5m", Decimal("1.25")), ("1h", Decimal("2"))])
async def test_explicit_cache_controls_support_known_write_rates(
    monkeypatch, model, slug, ttl, factor
):
    messages = apply_anthropic_cache(model, [{"role": "system", "content": "fixture"}])
    messages[0]["content"][0]["cache_control"]["ttl"] = ttl
    assert anthropic_cache_write_factor(model, {"messages": messages}) == factor
    monkeypatch.setattr(
        pricing,
        "_fetch_catalog_prices",
        AsyncMock(return_value=pricing._parse_catalog_prices(quote(slug))),
    )
    cost, evidence = await pricing.resolve_request_cost(
        model, tokens_in=1000, tokens_out=0, cache_write_tokens=1000, cache_write_factor=factor
    )
    assert cost == (Decimal("0.3498078") * factor).quantize(Decimal(".0001"))
    assert evidence["basis"] == "provider_catalog_estimate"
    assert evidence["tariff"]["cache_write_factor"] == str(factor)


@pytest.mark.parametrize("model,slug", MODELS)
async def test_missing_write_policy_does_not_invent_catalog_receipt(monkeypatch, model, slug):
    fetch = AsyncMock(side_effect=AssertionError("unknown writes must not request a quote"))
    monkeypatch.setattr(pricing, "_fetch_catalog_prices", fetch)
    assert anthropic_cache_write_factor(model, {"messages": []}) is None
    _, evidence = await pricing.resolve_request_cost(
        model, tokens_in=1000, tokens_out=0, cache_write_tokens=1000
    )
    assert evidence["basis"] == "token_tariff"
    fetch.assert_not_awaited()


def test_old_sonnet_tariff_and_routing_unchanged():
    assert llmgw.native_slug("claude-sonnet-5") == "anthropic/claude-sonnet-5"
    assert pricing.calculate_cost_rub("claude-sonnet-5", 1000, 1000) == Decimal("1.9380")
    assert pricing.calculate_cost_rub("claude-sonnet-5", 1000, 0, 1000) == Decimal(".0323")
