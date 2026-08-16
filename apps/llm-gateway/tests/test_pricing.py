"""Pricing logic — pure, no I/O."""

from __future__ import annotations

from decimal import Decimal

import pytest

from omnia_gateway.core.errors import ModelNotFoundError
from omnia_gateway.services.pricing import (
    PRICE_TABLE,
    calculate_cost_rub,
    list_models,
)


@pytest.mark.parametrize(
    ("model_id", "tokens_in", "tokens_out", "expected"),
    [
        # Gemini Custom Tools @ configured rates.
        ("gemini-3.1-pro-preview-customtools", 1000, 2000, Decimal("16.5000")),
        # 5000*1.50/1000 + 1000*7.50/1000 = 7.50 + 7.50
        ("gemini-3.1-pro-preview-customtools", 5000, 1000, Decimal("15.0000")),
        # zero tokens: zero cost
        ("gemini-3.1-pro-preview-customtools", 0, 0, Decimal("0.0000")),
        # 100*1.50/1000 + 50*7.50/1000 = 0.15 + 0.375
        ("gemini-3.1-pro-preview-customtools", 100, 50, Decimal("0.5250")),
        # Sonnet 5 @ LLMGW catalog rates.
        ("claude-sonnet-5", 1000, 1000, Decimal("1.9380")),
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
        calculate_cost_rub("gemini-3.1-pro-preview-customtools", -1, 0)
    with pytest.raises(ValueError):
        calculate_cost_rub("gemini-3.1-pro-preview-customtools", 0, -5)
    with pytest.raises(ValueError):
        calculate_cost_rub("gemini-3.1-pro-preview-customtools", 100, 0, cached_tokens=-1)


def test_cached_tokens_bill_cheaper() -> None:
    full = calculate_cost_rub("gemini-3.1-pro-preview-customtools", 10_000, 1000)
    assert full == Decimal("22.5000")  # (10000*1.50 + 1000*7.50)/1000
    # 8000 of the 10000 prompt tokens served from cache → bill those at 10%:
    # fresh 2000*1.50 + cached 8000*1.50*0.1 + out 1000*7.50, all /1000.
    cached = calculate_cost_rub(
        "gemini-3.1-pro-preview-customtools",
        10_000,
        1000,
        cached_tokens=8_000,
    )
    assert cached == Decimal("11.7000")
    assert cached < full


def test_cached_tokens_default_zero_is_unchanged() -> None:
    assert calculate_cost_rub(
        "gemini-3.1-pro-preview-customtools", 5000, 500
    ) == calculate_cost_rub("gemini-3.1-pro-preview-customtools", 5000, 500, cached_tokens=0)


def test_cache_creation_is_billed_separately() -> None:
    # 4K fresh + 6K cache write at 1.25x + 1K output.
    cost = calculate_cost_rub(
        "gemini-3.1-pro-preview-customtools",
        10_000,
        1_000,
        cache_write_tokens=6_000,
    )
    assert cost == Decimal("24.7500")


def test_cached_tokens_capped_at_prompt() -> None:
    # A bogus upstream count (cached > prompt) must clamp, never underbill negative.
    capped = calculate_cost_rub(
        "gemini-3.1-pro-preview-customtools", 1000, 0, cached_tokens=999_999
    )
    all_cached = calculate_cost_rub(
        "gemini-3.1-pro-preview-customtools", 1000, 0, cached_tokens=1000
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
