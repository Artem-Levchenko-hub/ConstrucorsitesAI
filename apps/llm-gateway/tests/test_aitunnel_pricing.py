"""Provider identity controls receipt currency and the immutable tariff source."""

from decimal import Decimal
from unittest.mock import AsyncMock

import pytest

from yleum_gateway.core.config import Settings
from yleum_gateway.core.errors import ModelNotFoundError
from yleum_gateway.services import pricing


def test_text_credentials_are_separate_and_disabled_by_default(monkeypatch):
    monkeypatch.setenv("AITUNNEL_API_KEY", "synthetic-media")
    monkeypatch.delenv("AITUNNEL_TEXT_API_KEY", raising=False)
    monkeypatch.delenv("AITUNNEL_TEXT_FALLBACK_ENABLED", raising=False)
    settings = Settings(_env_file=None)
    assert settings.aitunnel_text_api_key is None
    assert settings.aitunnel_text_fallback_enabled is False
    assert settings.aitunnel_api_key.get_secret_value() == "synthetic-media"
    configured = Settings(_env_file=None, aitunnel_text_api_key="synthetic-text",
                          aitunnel_text_organization_id="")
    assert configured.aitunnel_text_organization_id is None
    assert "synthetic-text" not in repr(configured)


def test_ai_receipt_rub_precedence_and_cost_is_not_usd():
    receipt = pricing.read_reported_cost(
        {"usage": {"cost_rub": "0.3", "cost": "0.3"}},
        {"x-llmgw-cost-rub": "99", "x-cost-rub": "88"}, provider_scope="aitunnel",
    )
    assert receipt.cost_rub == Decimal("0.3") and receipt.rub_source == "usage:cost_rub"
    assert receipt.cost_usd is None and receipt.usd_source is None


async def test_ai_static_verified_tariff_never_fetches_llmgw_catalog(monkeypatch):
    catalog = AsyncMock(side_effect=AssertionError("foreign catalog"))
    monkeypatch.setattr(pricing, "_catalog_prices", catalog)
    cost, proof = await pricing.resolve_request_cost(
        "claude-sonnet-5.5", tokens_in=1000, tokens_out=1000,
        cache_read_tokens=500, provider_scope="aitunnel",
    )
    assert cost == Decimal("2.2200")
    assert proof["schema_version"] == 2 and proof["provider_scope"] == "aitunnel"
    assert proof["tariff_source"] == "aitunnel-public-catalog-2026-10-23"
    assert proof["basis"] == "provider_catalog_estimate"
    assert proof["tariff"]["cache_write_factor"] is None
    assert pricing.validate_cost_provenance(proof, cost_rub=cost) == proof
    catalog.assert_not_awaited()


async def test_ai_actual_rub_wins_without_inventing_cache_write_tariff():
    reported = pricing.read_reported_cost({"usage": {"cost_rub": "0.3"}}, {},
                                         provider_scope="aitunnel")
    cost, proof = await pricing.resolve_request_cost(
        "claude-sonnet-5.5", tokens_in=407, tokens_out=66, cache_write_tokens=100,
        cache_write_factor=Decimal("1.25"), reported=reported, provider_scope="aitunnel",
    )
    assert cost == Decimal("0.3") and proof["basis"] == "provider_reported_rub"
    assert proof["calculated_cost_rub"] is None
    assert proof["tariff"]["cache_write_factor"] is None


async def test_ai_unknown_write_price_without_receipt_is_not_a_quote():
    with pytest.raises(ValueError, match="cache write tariff"):
        await pricing.resolve_request_cost("claude-sonnet-5.5", tokens_in=1000,
            tokens_out=1, cache_write_tokens=1, provider_scope="aitunnel")


@pytest.mark.parametrize("scope", ["wrong", "", None])
async def test_unknown_scope_cannot_receive_llmgw_estimate(scope):
    with pytest.raises(ValueError, match="provider scope"):
        await pricing.resolve_request_cost("claude-sonnet-5.5", tokens_in=1,
                                          tokens_out=1, provider_scope=scope)


@pytest.mark.parametrize("model", ["claude-sonnet-5", "~anthropic/claude-sonnet-latest"])
async def test_ai_only_exact_verified_sonnet_55(model):
    with pytest.raises(ModelNotFoundError):
        await pricing.resolve_request_cost(model, tokens_in=1, tokens_out=1,
                                          provider_scope="aitunnel")


@pytest.mark.parametrize("usage", [
    {"cost": "2.79"},
    {"cost_usd": "2.79"},
    {"cost_rub": 0},
    {"cost_rub": "0.000001"},
    {"cost_rub": "-2.79"},
    {"cost_rub": True},
    {"cost_rub": "NaN"},
    {"cost_rub": "1e99"},
])
async def test_ai_kimi_unusable_rub_receipt_cannot_fall_back_to_a_tariff(usage):
    reported = pricing.read_reported_cost(
        {"usage": usage, "metadata": {"cost_rub": "2.79"}},
        {"x-cost-rub": "2.79", "x-llmgw-cost-rub": "2.79"}, provider_scope="aitunnel",
    )
    with pytest.raises(ValueError, match="receipt"):
        await pricing.resolve_request_cost(
            "kimi-k3.1-pro", tokens_in=1000, tokens_out=1, reported=reported,
            provider_scope="aitunnel",
        )


async def test_ai_kimi_exact_receipt_has_no_invented_tariff_and_rejects_forged_proof():
    reported = pricing.read_reported_cost(
        {"usage": {"cost_rub": "2.7912345"}}, {}, provider_scope="aitunnel",
    )
    cost, proof = await pricing.resolve_request_cost(
        "kimi-k3.1-pro", tokens_in=1000, tokens_out=1, cache_write_tokens=100,
        reported=reported, provider_scope="aitunnel",
    )
    assert cost == Decimal("2.7912345")
    assert proof["schema_version"] == 2 and proof["provider_scope"] == "aitunnel"
    assert proof["tariff_source"] == "aitunnel-kimi-k3.1-pro-usage-cost-rub"
    assert proof["tariff"] is proof["tariff_revision"] is proof["calculated_cost_rub"] is None
    assert pricing.validate_cost_provenance(proof, cost_rub=cost) == proof
    for overrides in (
        {"rub_source": "header:x-cost-rub"},
        {"tariff_revision": "0" * 64},
        {"tariff": {}},
        {"calculated_cost_rub": "2.7912345"},
        {"reported_cost_rub": "0", "effective_cost_rub": "0"},
    ):
        with pytest.raises(ValueError):
            pricing.validate_cost_provenance({**proof, **overrides})
