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
    # Opus 4.8 via vsegpt.ru — the art_director model. vsegpt's Anthropic pricing
    # differs from proxyapi; this internal RUB table drives billing/markup math,
    # so mirror opus-4-7's tier until the vsegpt price page is reconciled.
    "claude-opus-4-8": ModelPrice(Decimal("1.50"), Decimal("7.50")),
    # Haiku 4.5 via proxyapi.ru (sk- key, OpenAI-compat). Anthropic list price
    # ~$1/$5 per 1M; converted at the same factor as Sonnet (0.30/1.50 ≈ $3/$15)
    # then padded ~25% for proxyapi markup → 0.15/0.75 ₽ per 1k. Recheck the
    # proxyapi price page before billing real users.
    "claude-haiku-4-5": ModelPrice(Decimal("0.15"), Decimal("0.75")),
    "gpt-4.1": ModelPrice(Decimal("0.50"), Decimal("2.00")),
    "gpt-5-mini": ModelPrice(Decimal("0.06"), Decimal("0.24")),
    # GPT-5 family routed via proxyapi.ru/openai/v1 (same balance as Haiku).
    # OpenAI list prices (May 2026, USD → RUB at ~100 + 20% markup):
    #   gpt-5      : $1.25 / $10.00  → 0.15 / 1.20 ₽ per 1k
    #   gpt-5-nano : $0.05 / $0.40   → 0.006 / 0.05 ₽ per 1k (we use 0.01/0.05
    #                                 as a slightly padded floor so accounting
    #                                 stays integer-friendly).
    "gpt-5": ModelPrice(Decimal("0.15"), Decimal("1.20")),
    "gpt-5-nano": ModelPrice(Decimal("0.01"), Decimal("0.05")),
    "yandexgpt-5": ModelPrice(Decimal("0.10"), Decimal("0.40")),
    "qwen-3-coder": ModelPrice(Decimal("0.05"), Decimal("0.20")),
    # DeepSeek via proxyapi.ru (same key/balance as Haiku/GPT-5). DeepSeek list
    # prices (May 2026, USD → RUB at ~100 + 20% markup):
    #   deepseek-chat     (V3): $0.27 / $1.10 per 1M  → 0.03 / 0.13 ₽ per 1k
    #   deepseek-reasoner (R1): $0.55 / $2.19 per 1M  → 0.07 / 0.26 ₽ per 1k
    # Approximate — recheck the proxyapi price page before billing real users.
    "deepseek-chat": ModelPrice(Decimal("0.03"), Decimal("0.13")),
    "deepseek-reasoner": ModelPrice(Decimal("0.07"), Decimal("0.26")),
    # DeepSeek V4 Flash (Thinking) via vsegpt.ru. vsegpt list price ≈ 0.036 in /
    # 0.072 out ₽ per 1k; padded to a round, profitable floor below.
    "deepseek-v4-flash-thinking": ModelPrice(Decimal("0.05"), Decimal("0.10")),
    # Sber GigaChat — RUB-native, no FX conversion. Numbers approximate Sber's
    # public price list (May 2026); adjust against the official table before bumping
    # markup. Output is priced same as input on Sber's tariffs.
    "gigachat-2": ModelPrice(Decimal("0.20"), Decimal("0.20")),
    "gigachat-2-pro": ModelPrice(Decimal("1.50"), Decimal("1.50")),
    "gigachat-2-max": ModelPrice(Decimal("1.95"), Decimal("1.95")),
    # Google Gemini via AI Studio (free tier available; same key works for paid).
    # List prices (May 2026, ≤200k context window):
    #   2.5 Pro:   $1.25 / $10.00  per 1M tokens → ~0.15 / 1.20 ₽ per 1k (x100 FX + 20% markup)
    #   2.5 Flash: $0.30 / $2.50   per 1M tokens → ~0.04 / 0.30 ₽ per 1k
    # On free tier real charge is 0; these values are correct once we move to paid.
    "gemini-2.5-pro": ModelPrice(Decimal("0.15"), Decimal("1.20")),
    "gemini-2.5-flash": ModelPrice(Decimal("0.04"), Decimal("0.30")),
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
    "claude-opus-4-8": _ModelMeta("Claude Opus 4.8", "anthropic", 200_000, ("quality",)),
    "claude-haiku-4-5": _ModelMeta("Claude Haiku 4.5", "anthropic", 200_000, ("fast", "budget")),
    "gpt-4.1": _ModelMeta("GPT-4.1", "openai", 128_000, ("quality",)),
    "gpt-5-mini": _ModelMeta("GPT-5 Mini", "openai", 128_000, ("fast", "budget")),
    "gpt-5": _ModelMeta("GPT-5", "openai", 200_000, ("quality",)),
    "gpt-5-nano": _ModelMeta("GPT-5 Nano", "openai", 128_000, ("fast", "budget")),
    "yandexgpt-5": _ModelMeta("YandexGPT 5", "yandex", 32_000, ("budget",)),
    "qwen-3-coder": _ModelMeta("Qwen 3 Coder", "alibaba", 128_000, ("budget", "fast")),
    "deepseek-chat": _ModelMeta("DeepSeek V3", "deepseek", 128_000, ("quality", "budget")),
    "deepseek-reasoner": _ModelMeta("DeepSeek R1", "deepseek", 128_000, ("quality",)),
    "deepseek-v4-flash-thinking": _ModelMeta(
        "DeepSeek V4 Flash (Thinking)", "deepseek", 1_000_000, ("budget", "fast")
    ),
    "gigachat-2": _ModelMeta("GigaChat 2", "sber", 32_000, ("fast", "budget")),
    "gigachat-2-pro": _ModelMeta("GigaChat 2 Pro", "sber", 128_000, ("quality",)),
    "gigachat-2-max": _ModelMeta("GigaChat 2 Max", "sber", 128_000, ("quality",)),
    "gemini-2.5-pro": _ModelMeta("Gemini 2.5 Pro", "google", 1_000_000, ("quality",)),
    "gemini-2.5-flash": _ModelMeta("Gemini 2.5 Flash", "google", 1_000_000, ("fast", "budget")),
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
