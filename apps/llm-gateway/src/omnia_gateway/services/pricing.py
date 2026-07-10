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
    # Opus 4.8 — the ONLY chat model (every role). This internal RUB table drives
    # billing/markup math (oneprovider bills us in its own units; we mark up here).
    # Image generation (routers/images.py) and whisper
    # transcription (routers/audio.py) bill via their own paths, not this table.
    "claude-opus-4-8": ModelPrice(Decimal("1.50"), Decimal("7.50")),
}

_PER_1K = Decimal("1000")
_QUANT = Decimal("0.0001")  # 4 decimals — matches NUMERIC(12,4) in Postgres

# Cached-prefix input tokens bill at a fraction of the fresh-input rate. When a
# provider serves a prompt prefix from its context cache (DeepSeek automatic
# context caching, Anthropic cache_read, Gemini implicit caching), those tokens
# cost far less upstream — DeepSeek/Anthropic charge ~10% of the normal input
# rate for a cache hit. We mirror that so our billing reflects the real cost of
# the big stable system prompt once it is cached. `cached_tokens` defaults to 0,
# so every existing caller is byte-for-byte unchanged.
_CACHE_HIT_RATE = Decimal("0.1")


def calculate_cost_rub(
    model_id: str,
    tokens_in: int,
    tokens_out: int,
    cached_tokens: int = 0,
) -> Decimal:
    """RUB cost for a request, quantized to 4 decimal places.

    ``cached_tokens`` (≤ ``tokens_in``) are the prompt tokens the provider served
    from its context cache; they bill at ``_CACHE_HIT_RATE`` of the input rate.
    Default 0 → identical to the pre-cache behaviour.
    """
    if tokens_in < 0 or tokens_out < 0 or cached_tokens < 0:
        raise ValueError("token counts must be non-negative")
    try:
        price = PRICE_TABLE[model_id]
    except KeyError as exc:
        raise ModelNotFoundError(f"Unknown model_id: {model_id}") from exc

    # A cache hit is a subset of the prompt; never let a bad upstream count make
    # cached exceed the total in (which would underbill into negatives).
    cached = min(cached_tokens, tokens_in)
    fresh_in = tokens_in - cached
    cost = (
        Decimal(fresh_in) * price.rub_per_1k_in
        + Decimal(cached) * price.rub_per_1k_in * _CACHE_HIT_RATE
        + Decimal(tokens_out) * price.rub_per_1k_out
    ) / _PER_1K
    return cost.quantize(_QUANT)


@dataclass(frozen=True, slots=True)
class _ModelMeta:
    display_name: str
    provider: str  # 'anthropic' | 'openai' | 'yandex' | 'alibaba' | 'sber'
    context_window: int
    recommended_for: tuple[str, ...]


_MODEL_META: Mapping[str, _ModelMeta] = {
    "claude-opus-4-8": _ModelMeta("Claude Opus 4.8", "anthropic", 200_000, ("quality",)),
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
