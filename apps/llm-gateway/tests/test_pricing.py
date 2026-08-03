"""Pricing logic — pure, no I/O."""

from __future__ import annotations

from decimal import Decimal

import pytest

from omnia_gateway.core.errors import ModelNotFoundError
from omnia_gateway.services.pricing import (
    PRICE_TABLE,
    calculate_cost_rub,
    calculate_provider_cost_usd_upper_bound,
    list_models,
)


@pytest.mark.parametrize(
    ("model_id", "tokens_in", "tokens_out", "expected"),
    [
        ("gemini-3.1-pro-preview-customtools", 1000, 2000, Decimal("16.5000")),
        # Sonnet 5 @ configured rates.
        ("claude-sonnet-5", 1000, 2000, Decimal("3.5530")),
        # 5000*0.323/1000 + 1000*1.615/1000 = 1.615 + 1.615
        ("claude-sonnet-5", 5000, 1000, Decimal("3.2300")),
        # zero tokens: zero cost
        ("claude-sonnet-5", 0, 0, Decimal("0.0000")),
        # 100*0.323/1000 + 50*1.615/1000
        ("claude-sonnet-5", 100, 50, Decimal("0.1130")),
    ],
)
def test_calculate_cost_rub_known_models(
    model_id: str, tokens_in: int, tokens_out: int, expected: Decimal
) -> None:
    assert calculate_cost_rub(model_id, tokens_in, tokens_out) == expected


def test_calculate_cost_rub_unknown_model_raises() -> None:
    with pytest.raises(ModelNotFoundError):
        calculate_cost_rub("totally-fake", 100, 100)


def test_calculate_cost_rub_negative_tokens_rejected() -> None:
    with pytest.raises(ValueError):
        calculate_cost_rub("claude-sonnet-5", -1, 0)
    with pytest.raises(ValueError):
        calculate_cost_rub("claude-sonnet-5", 0, -5)
    with pytest.raises(ValueError):
        calculate_cost_rub("claude-sonnet-5", 100, 0, cached_tokens=-1)


def test_provider_cost_reservation_uses_full_output_and_headroom() -> None:
    # (100K bytes + 32K framing) * $3/M + 20K output * $15/M, then 2x headroom.
    assert calculate_provider_cost_usd_upper_bound(
        "claude-sonnet-5", 100_000, 20_000
    ) == Decimal("1.39200000")


def test_provider_cost_reservation_keeps_sonnet_long_context_rate() -> None:
    # Sonnet 5 keeps one conservative $3/$15 rate through its 1M context.
    assert calculate_provider_cost_usd_upper_bound(
        "claude-sonnet-5", 180_000, 20_000
    ) == Decimal("1.87200000")


def test_cached_tokens_bill_cheaper() -> None:
    full = calculate_cost_rub("claude-sonnet-5", 10_000, 1000)
    assert full == Decimal("4.8450")  # (10000*0.323 + 1000*1.615)/1000
    # 8000 of the 10000 prompt tokens served from cache → bill those at 10%:
    # fresh 2000*0.323 + cached 8000*0.323*0.1 + out 1000*1.615, all /1000.
    cached = calculate_cost_rub(
        "claude-sonnet-5",
        10_000,
        1000,
        cached_tokens=8_000,
    )
    assert cached == Decimal("2.5194")
    assert cached < full


def test_cached_tokens_default_zero_is_unchanged() -> None:
    assert calculate_cost_rub(
        "claude-sonnet-5", 5000, 500
    ) == calculate_cost_rub("claude-sonnet-5", 5000, 500, cached_tokens=0)


def test_cache_creation_is_billed_separately() -> None:
    # 4K fresh + 6K cache write at 1.25x + 1K output.
    cost = calculate_cost_rub(
        "claude-sonnet-5",
        10_000,
        1_000,
        cache_write_tokens=6_000,
    )
    assert cost == Decimal("5.3295")


def test_cached_tokens_capped_at_prompt() -> None:
    # A bogus upstream count (cached > prompt) must clamp, never underbill negative.
    capped = calculate_cost_rub(
        "claude-sonnet-5", 1000, 0, cached_tokens=999_999
    )
    all_cached = calculate_cost_rub(
        "claude-sonnet-5", 1000, 0, cached_tokens=1000
    )
    assert capped == all_cached >= Decimal("0")


def test_list_models_covers_price_table() -> None:
    catalog = list_models()
    assert set(PRICE_TABLE) == {
        "gemini-3.1-pro-preview-customtools",
        "claude-sonnet-5",
    }
    assert {m["id"] for m in catalog} == set(PRICE_TABLE.keys())
    for m in catalog:
        assert m["price_rub_per_1k_in"] > 0
        assert m["price_rub_per_1k_out"] > 0
        assert m["context_window"] >= 16_000
        assert m["provider"] in {
            "anthropic",
            "openai",
            "alibaba",
            "google",
            "deepseek",
            "minimax",
            "moonshot",
        }
        assert m["display_name"]
        assert isinstance(m["recommended_for"], list)
