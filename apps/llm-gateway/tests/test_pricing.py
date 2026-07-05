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
        # claude-opus-4-8 @ 1.50 in / 7.50 out: 1000*1.50/1000 + 2000*7.50/1000 = 1.50 + 15.00
        ("claude-opus-4-8", 1000, 2000, Decimal("16.5000")),
        # 5000*1.50/1000 + 1000*7.50/1000 = 7.50 + 7.50
        ("claude-opus-4-8", 5000, 1000, Decimal("15.0000")),
        # zero tokens: zero cost
        ("claude-opus-4-8", 0, 0, Decimal("0.0000")),
        # 100*1.50/1000 + 50*7.50/1000 = 0.15 + 0.375
        ("claude-opus-4-8", 100, 50, Decimal("0.5250")),
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
        calculate_cost_rub("claude-opus-4-8", -1, 0)
    with pytest.raises(ValueError):
        calculate_cost_rub("claude-opus-4-8", 0, -5)
    with pytest.raises(ValueError):
        calculate_cost_rub("claude-opus-4-8", 100, 0, cached_tokens=-1)


def test_cached_tokens_bill_cheaper() -> None:
    # claude-opus-4-8 @ 1.50 in / 7.50 out.
    full = calculate_cost_rub("claude-opus-4-8", 10_000, 1000)
    assert full == Decimal("22.5000")  # (10000*1.50 + 1000*7.50)/1000
    # 8000 of the 10000 prompt tokens served from cache → bill those at 10%:
    # fresh 2000*1.50 + cached 8000*1.50*0.1 + out 1000*7.50, all /1000.
    cached = calculate_cost_rub("claude-opus-4-8", 10_000, 1000, cached_tokens=8_000)
    assert cached == Decimal("11.7000")
    assert cached < full


def test_cached_tokens_default_zero_is_unchanged() -> None:
    assert calculate_cost_rub("claude-opus-4-8", 5000, 500) == calculate_cost_rub(
        "claude-opus-4-8", 5000, 500, cached_tokens=0
    )


def test_cached_tokens_capped_at_prompt() -> None:
    # A bogus upstream count (cached > prompt) must clamp, never underbill negative.
    capped = calculate_cost_rub("claude-opus-4-8", 1000, 0, cached_tokens=999_999)
    all_cached = calculate_cost_rub("claude-opus-4-8", 1000, 0, cached_tokens=1000)
    assert capped == all_cached >= Decimal("0")


def test_list_models_covers_price_table() -> None:
    catalog = list_models()
    assert {m["id"] for m in catalog} == set(PRICE_TABLE.keys())
    for m in catalog:
        assert m["price_rub_per_1k_in"] > 0
        assert m["price_rub_per_1k_out"] > 0
        assert m["context_window"] >= 16_000
        assert m["provider"] in {"anthropic", "openai", "alibaba", "google", "deepseek", "minimax", "moonshot"}
        assert m["display_name"]
        assert isinstance(m["recommended_for"], list)
