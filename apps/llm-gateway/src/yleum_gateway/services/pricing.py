"""RUB pricing for supported models.

Single source of truth — `/v1/models`, billing math, and tests all read from
`PRICE_TABLE` here. To revise prices: edit this map (or, in a later iteration,
load it from env / a config file).

Numbers: AGENT-C-LLM-GATEWAY.md, May 2026 (CBR rate × 1.20 markup).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, cast

from yleum_gateway.core.errors import ModelNotFoundError


@dataclass(frozen=True, slots=True)
class ModelPrice:
    rub_per_1k_in: Decimal
    rub_per_1k_out: Decimal


PRICE_TABLE: Mapping[str, ModelPrice] = {
    # Gemini 3.1 Pro Preview Custom Tools drives every orchestration role. Image generation
    # (routers/images.py), video, and whisper
    # transcription (routers/audio.py) bill via their own paths, not this table.
    "gemini-3.1-pro-preview-customtools": ModelPrice(Decimal("1.50"), Decimal("7.50")),
    "claude-sonnet-5": ModelPrice(Decimal("0.323"), Decimal("1.615")),
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


@dataclass(frozen=True, slots=True)
class ReportedCost:
    cost_rub: Decimal | None = None
    cost_usd: Decimal | None = None
    rub_source: str | None = None
    usd_source: str | None = None


def _finite_cost(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return number if number.is_finite() and number >= 0 else None


_RUB_SOURCES = ("header:x-llmgw-cost-rub", "header:x-cost-rub", "usage:cost_rub", "metadata:cost_rub")
_USD_SOURCES = ("usage:cost_usd", "usage:cost", "metadata:cost_usd", "header:x-cost-usd")


def read_reported_cost(data: Mapping[str, Any], headers: Mapping[str, Any]) -> ReportedCost:
    """Only known finite monetary scalars, with the existing native precedence."""
    usage = data.get("usage")
    usage = usage if isinstance(usage, dict) else {}
    metadata = data.get("metadata")
    metadata = metadata if isinstance(metadata, dict) else {}
    values = {
        "header:x-llmgw-cost-rub": headers.get("x-llmgw-cost-rub"),
        "header:x-cost-rub": headers.get("x-cost-rub"),
        "usage:cost_rub": usage.get("cost_rub"),
        "metadata:cost_rub": metadata.get("cost_rub"),
        "usage:cost_usd": usage.get("cost_usd"),
        "usage:cost": usage.get("cost"),
        "metadata:cost_usd": metadata.get("cost_usd"),
        "header:x-cost-usd": headers.get("x-cost-usd"),
    }

    def first(sources: tuple[str, ...]) -> tuple[Decimal | None, str | None]:
        for source in sources:
            cost = _finite_cost(values[source])
            if cost is not None:
                return cost, source
        return None, None

    rub, rub_source = first(_RUB_SOURCES)
    usd, usd_source = first(_USD_SOURCES)
    return ReportedCost(rub, usd, rub_source, usd_source)


def cost_provenance(
    model_id: str,
    calculated_rub: Decimal,
    reported: ReportedCost | None = None,
    *,
    tokens_in: int,
    tokens_out: int,
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
    estimated_tokens: bool = False,
) -> dict[str, Any]:
    """Record the selected calculation; a USD observation never implies FX."""
    reported = reported or ReportedCost()
    price = PRICE_TABLE[model_id]
    tariff = {
        "rub_per_1k_in": str(price.rub_per_1k_in),
        "rub_per_1k_out": str(price.rub_per_1k_out),
        "cache_read_factor": str(_CACHE_HIT_RATE),
        "cache_write_factor": str(_CACHE_WRITE_RATE),
        "quantum_rub": str(_QUANT),
    }
    revision = {
        "formula": "token-cache-rub-v1",
        "prices": {k: [str(v.rub_per_1k_in), str(v.rub_per_1k_out)] for k, v in PRICE_TABLE.items()},
        "cache_read_factor": str(_CACHE_HIT_RATE),
        "cache_write_factor": str(_CACHE_WRITE_RATE),
        "quantum_rub": str(_QUANT),
    }
    evidence = {
        "schema_version": 1,
        "basis": "provider_reported_rub" if reported.cost_rub is not None else
        "local_token_estimate" if estimated_tokens else "token_tariff",
        "effective_cost_rub": str(reported.cost_rub if reported.cost_rub is not None else calculated_rub),
        "calculated_cost_rub": str(calculated_rub),
        "reported_cost_rub": str(reported.cost_rub) if reported.cost_rub is not None else None,
        "provider_cost_usd": str(reported.cost_usd) if reported.cost_usd is not None else None,
        "rub_source": reported.rub_source,
        "usd_source": reported.usd_source,
        "tariff_revision": hashlib.sha256(json.dumps(revision, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
        "tariff": tariff,
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "cache_read_tokens": cache_read_tokens,
        "cache_write_tokens": cache_write_tokens,
    }
    return validate_cost_provenance(evidence)


def validate_cost_provenance(
    evidence: dict[str, Any], *, cost_rub: Decimal | None = None,
) -> dict[str, Any]:
    """A closed receipt shape, without arbitrary headers, bodies or identifiers."""
    keys = {"schema_version", "basis", "effective_cost_rub", "calculated_cost_rub",
            "reported_cost_rub", "provider_cost_usd", "rub_source", "usd_source",
            "tariff_revision", "tariff", "tokens_in", "tokens_out", "cache_read_tokens", "cache_write_tokens"}
    tariff_keys = {"rub_per_1k_in", "rub_per_1k_out", "cache_read_factor", "cache_write_factor", "quantum_rub"}
    if set(evidence) != keys or type(evidence["schema_version"]) is not int or evidence["schema_version"] != 1:
        raise ValueError("Invalid cost provenance shape")
    if evidence["basis"] not in {"provider_reported_rub", "token_tariff", "local_token_estimate"}:
        raise ValueError("Invalid cost provenance basis")
    if evidence["rub_source"] not in (*_RUB_SOURCES, None) or evidence["usd_source"] not in (*_USD_SOURCES, None):
        raise ValueError("Invalid cost provenance source")
    for key in ("effective_cost_rub", "calculated_cost_rub", "reported_cost_rub", "provider_cost_usd"):
        value = evidence[key]
        if value is None and key in {"reported_cost_rub", "provider_cost_usd"}:
            continue
        if not isinstance(value, str) or _finite_cost(value) is None or len(value) > 80:
            raise ValueError("Invalid cost provenance amount")
    if cost_rub is not None and _finite_cost(evidence["effective_cost_rub"]) != cost_rub:
        raise ValueError("Cost provenance does not match receipt")
    if (evidence["reported_cost_rub"] is None) != (evidence["rub_source"] is None) or (evidence["provider_cost_usd"] is None) != (evidence["usd_source"] is None):
        raise ValueError("Cost provenance source missing")
    if (evidence["basis"] == "provider_reported_rub") != (evidence["reported_cost_rub"] is not None):
        raise ValueError("Cost provenance basis mismatch")
    effective = evidence["reported_cost_rub"] if evidence["basis"] == "provider_reported_rub" else evidence["calculated_cost_rub"]
    if _finite_cost(evidence["effective_cost_rub"]) != _finite_cost(effective):
        raise ValueError("Cost provenance selected amount mismatch")
    for key in ("tokens_in", "tokens_out", "cache_read_tokens", "cache_write_tokens"):
        if type(evidence[key]) is not int or evidence[key] < 0:
            raise ValueError("Invalid cost provenance tokens")
    tariff = evidence["tariff"]
    if not isinstance(tariff, dict) or set(tariff) != tariff_keys or any(
        not isinstance(v, str) or len(v) > 80 or _finite_cost(v) is None for v in tariff.values()
    ):
        raise ValueError("Invalid cost provenance tariff")
    revision = evidence["tariff_revision"]
    if not isinstance(revision, str) or len(revision) != 64 or any(c not in "0123456789abcdef" for c in revision):
        raise ValueError("Invalid cost provenance revision")
    # JSON roundtrip detaches mutable callers and bounds the persisted payload.
    serialized = json.dumps(evidence, sort_keys=True, separators=(",", ":"))
    if len(serialized) > 4096:
        raise ValueError("Cost provenance too large")
    return cast(dict[str, Any], json.loads(serialized))


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
    "claude-sonnet-5": _ModelMeta(
        "Claude Sonnet 5",
        "anthropic",
        1_000_000,
        ("agentic", "coding", "tool-use", "multimodal"),
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
