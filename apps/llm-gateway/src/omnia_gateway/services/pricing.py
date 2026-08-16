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
    # Gemini 3.1 Pro Preview Custom Tools drives every orchestration role. Image generation
    # (routers/images.py), video, and whisper
    # transcription (routers/audio.py) bill via their own paths, not this table.
    "gemini-3.1-pro-preview-customtools": ModelPrice(Decimal("1.50"), Decimal("7.50")),
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
_CACHE_WRITE_RATE = Decimal("1.25")


def calculate_cost_rub(
    model_id: str,
    tokens_in: int,
    tokens_out: int,
    cached_tokens: int = 0,
    cache_write_tokens: int = 0,
) -> Decimal:
    """RUB cost for a request, quantized to 4 decimal places.

    ``cached_tokens`` (≤ ``tokens_in``) are the prompt tokens the provider served
    from its context cache; they bill at ``_CACHE_HIT_RATE`` of the input rate.
    Default 0 → identical to the pre-cache behaviour.
    """
    if tokens_in < 0 or tokens_out < 0 or cached_tokens < 0 or cache_write_tokens < 0:
        raise ValueError("token counts must be non-negative")
    try:
        price = PRICE_TABLE[model_id]
    except KeyError as exc:
        raise ModelNotFoundError(f"Unknown model_id: {model_id}") from exc

    # A cache hit is a subset of the prompt; never let a bad upstream count make
    # cached exceed the total in (which would underbill into negatives).
    cached = min(cached_tokens, tokens_in)
    cache_write = min(cache_write_tokens, tokens_in - cached)
    fresh_in = tokens_in - cached - cache_write
    cost = (
        Decimal(fresh_in) * price.rub_per_1k_in
        + Decimal(cached) * price.rub_per_1k_in * _CACHE_HIT_RATE
        + Decimal(cache_write) * price.rub_per_1k_in * _CACHE_WRITE_RATE
        + Decimal(tokens_out) * price.rub_per_1k_out
    ) / _PER_1K
    return cost.quantize(_QUANT)


@dataclass(frozen=True, slots=True)
class _ModelMeta:
    display_name: str
    provider: str
    context_window: int
    recommended_for: tuple[str, ...]


_MODEL_META: Mapping[str, _ModelMeta] = {
    "gemini-3.1-pro-preview-customtools": _ModelMeta(
        "Gemini 3.1 Pro Preview Custom Tools",
        "google",
        1_048_576,
        ("agentic", "coding", "multimodal"),
    ),
}


def list_models() -> list[dict[str, object]]:
    """Return public model catalog matching the contract Model type."""
    out: list[dict[str, object]] = []
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
