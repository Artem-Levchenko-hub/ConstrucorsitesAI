"""RUB pricing for supported models.

Single source of truth — `/v1/models`, billing math, and tests all read from
`PRICE_TABLE` here. To revise prices: edit this map (or, in a later iteration,
load it from env / a config file).

Numbers: AGENT-C-LLM-GATEWAY.md, May 2026 (CBR rate × 1.20 markup).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal

from omnia_gateway.core.errors import ModelNotFoundError


@dataclass(frozen=True, slots=True)
class ModelPrice:
    rub_per_1k_in: Decimal
    rub_per_1k_out: Decimal


PRICE_TABLE: Mapping[str, ModelPrice] = {
    "claude-sonnet-4-6": ModelPrice(Decimal("0.30"), Decimal("1.50")),
    "claude-opus-4-7": ModelPrice(Decimal("1.50"), Decimal("7.50")),
    "gpt-4.1": ModelPrice(Decimal("0.50"), Decimal("2.00")),
    "gpt-5-mini": ModelPrice(Decimal("0.06"), Decimal("0.24")),
    "yandexgpt-5": ModelPrice(Decimal("0.10"), Decimal("0.40")),
    "qwen-3-coder": ModelPrice(Decimal("0.05"), Decimal("0.20")),
    # Sber GigaChat — RUB-native, no FX conversion. Numbers approximate Sber's
    # public price list (May 2026); adjust against the official table before bumping
    # markup. Output is priced same as input on Sber's tariffs.
    "gigachat-2": ModelPrice(Decimal("0.20"), Decimal("0.20")),
    "gigachat-2-pro": ModelPrice(Decimal("1.50"), Decimal("1.50")),
    "gigachat-2-max": ModelPrice(Decimal("1.95"), Decimal("1.95")),
}

_PER_1K = Decimal("1000")
_QUANT = Decimal("0.0001")  # 4 decimals — matches NUMERIC(12,4) in Postgres


def calculate_cost_rub(model_id: str, tokens_in: int, tokens_out: int) -> Decimal:
    """RUB cost for a request, quantized to 4 decimal places."""
    if tokens_in < 0 or tokens_out < 0:
        raise ValueError("token counts must be non-negative")
    try:
        price = PRICE_TABLE[model_id]
    except KeyError as exc:
        raise ModelNotFoundError(f"Unknown model_id: {model_id}") from exc

    cost = (
        Decimal(tokens_in) * price.rub_per_1k_in + Decimal(tokens_out) * price.rub_per_1k_out
    ) / _PER_1K
    return cost.quantize(_QUANT)


@dataclass(frozen=True, slots=True)
class _ModelMeta:
    display_name: str
    provider: str  # 'anthropic' | 'openai' | 'yandex' | 'alibaba' | 'sber'
    context_window: int
    recommended_for: tuple[str, ...]


_MODEL_META: Mapping[str, _ModelMeta] = {
    "claude-sonnet-4-6": _ModelMeta("Claude Sonnet 4.6", "anthropic", 200_000, ("quality",)),
    "claude-opus-4-7": _ModelMeta("Claude Opus 4.7", "anthropic", 200_000, ("quality",)),
    "gpt-4.1": _ModelMeta("GPT-4.1", "openai", 128_000, ("quality",)),
    "gpt-5-mini": _ModelMeta("GPT-5 Mini", "openai", 128_000, ("fast", "budget")),
    "yandexgpt-5": _ModelMeta("YandexGPT 5", "yandex", 32_000, ("budget",)),
    "qwen-3-coder": _ModelMeta("Qwen 3 Coder", "alibaba", 128_000, ("budget", "fast")),
    "gigachat-2": _ModelMeta("GigaChat 2", "sber", 32_000, ("fast", "budget")),
    "gigachat-2-pro": _ModelMeta("GigaChat 2 Pro", "sber", 128_000, ("quality",)),
    "gigachat-2-max": _ModelMeta("GigaChat 2 Max", "sber", 128_000, ("quality",)),
}


def list_models() -> list[dict]:
    """Return public model catalog matching the contract Model type."""
    out: list[dict] = []
    for model_id, price in PRICE_TABLE.items():
        meta = _MODEL_META[model_id]
        out.append(
            {
                "id": model_id,
                "display_name": meta.display_name,
                "provider": meta.provider,
                "price_rub_per_1k_in": float(price.rub_per_1k_in),
                "price_rub_per_1k_out": float(price.rub_per_1k_out),
                "context_window": meta.context_window,
                "recommended_for": list(meta.recommended_for),
            }
        )
    return out
