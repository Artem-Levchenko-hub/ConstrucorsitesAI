"""Provider receipts first; public RUB quotes and legacy tariffs are estimates.

Historical receipts are immutable. USD observations never imply an FX conversion.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, cast

import httpx

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
    cache_write_factor: Decimal = _CACHE_WRITE_RATE,
) -> dict[str, Any]:
    """Record the selected calculation; a USD observation never implies FX."""
    reported = reported or ReportedCost()
    price = PRICE_TABLE[model_id]
    tariff = {
        "rub_per_1k_in": str(price.rub_per_1k_in),
        "rub_per_1k_out": str(price.rub_per_1k_out),
        "cache_read_factor": str(_CACHE_HIT_RATE),
        "cache_write_factor": str(cache_write_factor),
        "quantum_rub": str(_QUANT),
    }
    revision = {
        "formula": "token-cache-rub-v1",
        "prices": {k: [str(v.rub_per_1k_in), str(v.rub_per_1k_out)] for k, v in PRICE_TABLE.items()},
        "cache_read_factor": str(_CACHE_HIT_RATE),
        "cache_write_factor": str(cache_write_factor),
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
    if evidence["basis"] not in {"provider_reported_rub", "token_tariff", "local_token_estimate", "provider_catalog_estimate"}:
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


_CATALOG_URL = "https://app.llmgw.ru/api/v1/models/models-with-pricing"
_CATALOG_TTL = 15.0
_catalog_cache: tuple[float, dict[str, tuple[Decimal, Decimal, Decimal]]] | None = None
_catalog_lock: asyncio.Lock | None = None


def _parse_catalog_prices(data: Any) -> dict[str, tuple[Decimal, Decimal, Decimal]]:
    """Accept only exact, non-tiered text quotes in RUB per million tokens."""
    if not isinstance(data, dict) or not isinstance(data.get("items"), list):
        return {}
    quotes: dict[str, tuple[Decimal, Decimal, Decimal]] = {}
    for item in data["items"]:
        if not isinstance(item, dict) or not isinstance(item.get("model_name"), str):
            continue
        model = item["model_name"].split("/", 1)[-1]
        slugs = {"claude-sonnet-5": "anthropic/claude-sonnet-5",
                 "gemini-3.1-pro-preview-customtools": "google/gemini-3.1-pro-preview-customtools"}
        if model not in PRICE_TABLE or item["model_name"] != slugs[model]:
            continue
        price = item.get("pricing")
        if not isinstance(price, dict) or price.get("currency") != "RUB" or price.get("unit") != "1M_tokens":
            continue
        fields = ("input_rub_per_mtok", "output_rub_per_mtok", "cached_input_rub_per_mtok")
        amounts = [_finite_cost(price.get(key)) for key in fields]
        if any(v is None or v > Decimal("1000000000") or len(str(v)) > 60 or
               cast(int, v.as_tuple().exponent) < -12
               for v in amounts):
            continue
        input_rate, output_rate, cached_rate = cast(tuple[Decimal, Decimal, Decimal], tuple(amounts))
        if cached_rate > input_rate or not isinstance(price.get("rates"), list):
            continue
        expected = dict(zip(("input_text", "output_text", "cached_input"), (input_rate, output_rate, cached_rate), strict=True))
        seen: set[str] = set()
        valid = True
        for rate in price["rates"]:
            if not isinstance(rate, dict):
                valid = False
                break
            kind = rate.get("billable")
            if not isinstance(kind, str):
                valid = False
                break
            if kind not in expected:
                continue  # Non-token modalities do not enter text billing.
            if (kind in seen or rate.get("unit") != "token" or
                _finite_cost(rate.get("quantity")) != Decimal("1000000") or
                rate.get("variant") != "" or rate.get("is_from_price") is not False or
                _finite_cost(rate.get("price_rub")) != expected[kind]):
                valid = False
                break
            seen.add(kind)
        if valid and seen == set(expected):
            quotes[model] = (input_rate, output_rate, cached_rate)
    return quotes


async def _fetch_catalog_prices() -> dict[str, tuple[Decimal, Decimal, Decimal]]:
    """Public endpoint only: no API key, redirects, unbounded body or wait."""
    try:
        async with asyncio.timeout(3):
            async with httpx.AsyncClient(timeout=3, follow_redirects=False) as client:
                async with client.stream("GET", _CATALOG_URL, params={"limit": 200}) as response:
                    response.raise_for_status()
                    body = bytearray()
                    async for chunk in response.aiter_bytes():
                        body.extend(chunk)
                        if len(body) > 1024 * 1024:
                            return {}
        return _parse_catalog_prices(json.loads(body))
    except (httpx.HTTPError, TimeoutError, ValueError):
        return {}


async def _catalog_prices() -> dict[str, tuple[Decimal, Decimal, Decimal]]:
    global _catalog_cache, _catalog_lock
    if _catalog_lock is None:
        _catalog_lock = asyncio.Lock()
    async with _catalog_lock:
        if _catalog_cache is not None and time.monotonic() - _catalog_cache[0] < _CATALOG_TTL:
            return _catalog_cache[1]
        quotes = await _fetch_catalog_prices()
        _catalog_cache = (time.monotonic(), quotes)
        return quotes


async def resolve_request_cost(
    model_id: str, *, tokens_in: int, tokens_out: int,
    cache_read_tokens: int = 0, cache_write_tokens: int = 0,
    reported: ReportedCost | None = None, estimated_tokens: bool = False,
    cache_write_factor: Decimal | None = None,
) -> tuple[Decimal, dict[str, Any]]:
    """Select an actual receipt or a transparently identified immutable estimate."""
    # Anthropic documents 5m writes at 1.25x and 1h writes at 2x. The caller
    # selects a factor only from the cache controls actually sent upstream;
    # mixed/unknown policies do not acquire a fabricated current quote.
    known_write_factor = (
        cache_write_factor if model_id == "claude-sonnet-5"
        and isinstance(cache_write_factor, Decimal) and cache_write_factor.is_finite()
        and cache_write_factor in {Decimal("1.25"), Decimal("2")} else None
    )
    write_factor = known_write_factor or _CACHE_WRITE_RATE
    local = calculate_cost_rub(model_id, tokens_in, tokens_out, cache_read_tokens, cache_write_tokens,
                               cache_write_factor=write_factor)
    evidence = cost_provenance(model_id, local, reported, tokens_in=tokens_in,
        tokens_out=tokens_out, cache_read_tokens=cache_read_tokens,
        cache_write_tokens=cache_write_tokens, estimated_tokens=estimated_tokens,
        cache_write_factor=write_factor)
    if reported is not None and reported.cost_rub is not None:
        return reported.cost_rub, evidence
    if estimated_tokens or (cache_write_tokens and known_write_factor is None):
        return local, evidence
    quote = (await _catalog_prices()).get(model_id)
    if quote is None:
        return local, evidence
    input_rate, output_rate, cached_rate = quote
    cached = min(cache_read_tokens, tokens_in)
    writes = min(cache_write_tokens, tokens_in - cached)
    cost = ((Decimal(tokens_in - cached - writes) * input_rate + Decimal(cached) * cached_rate +
             Decimal(writes) * input_rate * write_factor +
             Decimal(tokens_out) * output_rate) / Decimal("1000000")).quantize(_QUANT)
    tariff = {
        "rub_per_1k_in": str(input_rate / _PER_1K),
        "rub_per_1k_out": str(output_rate / _PER_1K),
        "cache_read_factor": str(cached_rate / input_rate if input_rate else Decimal(0)),
        "cache_write_factor": str(write_factor) if writes else "0",
        "quantum_rub": str(_QUANT),
    }
    evidence.update(basis="provider_catalog_estimate", effective_cost_rub=str(cost),
                    calculated_cost_rub=str(cost), tariff=tariff,
                    tariff_revision=hashlib.sha256(json.dumps({"source": _CATALOG_URL,
                        "formula": "public-rub-mtok-v2-cache-ttl", "tariff": tariff},
                        sort_keys=True, separators=(",", ":")).encode()).hexdigest())
    return cost, validate_cost_provenance(evidence, cost_rub=cost)


def calculate_cost_rub(
    model_id: str,
    tokens_in: int,
    tokens_out: int,
    cached_tokens: int = 0,
    cache_write_tokens: int = 0,
    *, cache_write_factor: Decimal = _CACHE_WRITE_RATE,
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
        + Decimal(cache_write) * price.rub_per_1k_in * cache_write_factor
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
