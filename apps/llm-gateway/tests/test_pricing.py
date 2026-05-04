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
        # claude-sonnet-4-6 @ 0.30 in / 1.50 out: 1000*0.30/1000 + 2000*1.50/1000 = 0.30 + 3.00
        ("claude-sonnet-4-6", 1000, 2000, Decimal("3.3000")),
        # gpt-5-mini @ 0.06 / 0.24: 5000*0.06/1000 + 1000*0.24/1000 = 0.30 + 0.24
        ("gpt-5-mini", 5000, 1000, Decimal("0.5400")),
        # zero tokens: zero cost
        ("claude-opus-4-7", 0, 0, Decimal("0.0000")),
        # qwen-3-coder @ 0.05 / 0.20: 100*0.05/1000 + 50*0.20/1000 = 0.005 + 0.010
        ("qwen-3-coder", 100, 50, Decimal("0.0150")),
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
        calculate_cost_rub("gpt-5-mini", -1, 0)
    with pytest.raises(ValueError):
        calculate_cost_rub("gpt-5-mini", 0, -5)


def test_list_models_covers_price_table() -> None:
    catalog = list_models()
    assert {m["id"] for m in catalog} == set(PRICE_TABLE.keys())
    for m in catalog:
        assert m["price_rub_per_1k_in"] > 0
        assert m["price_rub_per_1k_out"] > 0
        assert m["context_window"] >= 16_000
        assert m["provider"] in {"anthropic", "openai", "yandex", "alibaba"}
        assert m["display_name"]
        assert isinstance(m["recommended_for"], list)
