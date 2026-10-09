"""llmgw.ru provider — chat completions over its OpenAI-compatible surface.

The public API lives under ``https://api.llmgw.ru/v1`` and uses
``Authorization: Bearer <LLMGW_API_KEY>``. The provider requires canonical
vendor-prefixed model ids (``google/gemini-3.1-pro-preview-customtools``), while
Omnia keeps a stable public id without the vendor prefix.

Why a sync ``httpx.Client`` on a worker thread instead of ``AsyncClient``: the
gateway container may carry an ``HTTPS_PROXY`` (a UK egress used only to
geo-bypass Google), and an ``AsyncClient`` inside the long-lived uvicorn loop
intermittently stalls the TLS handshake. ``trust_env=False`` + an explicit no-op
``mounts`` transport ignores the proxy unconditionally, and a fresh sync client
on ``asyncio.to_thread`` connects fast.

R-01 (deep module): callers see ``is_llmgw_model()`` + ``acompletion()`` +
``astream()``. Transport quirks, chain-of-thought stripping, and error
translation live entirely inside.
"""

from __future__ import annotations

import asyncio
import json
import re
import threading
import time
from collections.abc import AsyncGenerator, Mapping
from typing import Any
from uuid import uuid4

import httpx

from yleum_gateway.core.config import get_settings
from yleum_gateway.core.errors import UpstreamProviderError, ValidationFailedError
from yleum_gateway.services.receipt_evidence import receipt_evidence

# Retry only failures before the POST can be accepted. A missing response can
# still mean a paid generation; replaying it may create a second provider bill.
_SAFE_TO_RETRY = (httpx.ConnectError, httpx.ConnectTimeout)

# Omnia model ID → the exact llmgw catalog id sent as the OpenAI `model` field.
_MODEL_SLUG: dict[str, str] = {
    "gemini-3.1-pro-preview-customtools": "google/gemini-3.1-pro-preview-customtools",
    "claude-sonnet-5": "anthropic/claude-sonnet-5",
}

# The native Messages response may add the provider prefix; accept both forms.
_SLUG_TO_OMNIA: dict[str, str] = {
    "gemini-3.1-pro-preview-customtools": "gemini-3.1-pro-preview-customtools",
    "google/gemini-3.1-pro-preview-customtools": "gemini-3.1-pro-preview-customtools",
    "claude-sonnet-5": "claude-sonnet-5",
    "anthropic/claude-sonnet-5": "claude-sonnet-5",
}

# Natively multimodal models — keep OpenAI image_url blocks instead of flattening
# them (the acceptance/vision judge + the agent `see` tool send screenshots).
_MULTIMODAL: frozenset[str] = frozenset(
    {"gemini-3.1-pro-preview-customtools", "claude-sonnet-5"}
)

_DEFAULT_MAX_TOKENS = 32768
# Long art-director / writer passes run ~150s non-streaming. 240s clears them while
# bounding a genuine hang, and stays under the api client's 300s read timeout so a
# real upstream failure surfaces as a clean error, not a client socket teardown.
_DEFAULT_TIMEOUT_S = 240.0

# Strip a leaked chain-of-thought — some upstreams inline `<think>…</think>` in the
# content, which would break a downstream PageIR JSON parse.
_THINK_BLOCK = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)


def is_llmgw_model(model_id: str) -> bool:
    """True if this model is served by llmgw's chat surface."""
    return model_id in _MODEL_SLUG


def native_slug(model_id: str) -> str:
    """Catalog slug for the native `/v1/messages` surface (identity if unknown)."""
    return _MODEL_SLUG.get(model_id, model_id)


def slug_to_omnia(slug: str) -> str | None:
    """Map an upstream response `model` back to the Omnia id; None if unknown."""
    return _SLUG_TO_OMNIA.get(slug)


def resolve_response_model(reported: str, requested: str, provider_request_id: str | None) -> str:
    """An unknown actual model must not inherit the requested model's tariff."""
    if not reported:
        return requested
    mapped = slug_to_omnia(reported)
    if mapped is None:
        raise UpstreamProviderError(
            "llmgw returned an unsupported model",
            details={"provider_charge_ambiguous": True, "provider_request_id": provider_request_id},
        )
    return mapped


def _is_vision(model_id: str) -> bool:
    """Multimodal model — keeps image_url blocks instead of flattening them."""
    return model_id in _MULTIMODAL


def _strip_reasoning(text: str) -> str:
    """Remove inline `<think>` blocks; keep the original if that empties it."""
    cleaned = _THINK_BLOCK.sub("", text).strip()
    return cleaned or text.strip()


def _flatten_content(content: Any) -> str:
    """For text-only requests: keep the text parts of a multimodal block list and
    drop images so the request still goes through instead of 400-ing."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [
            block.get("text", "")
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        ]
        return "\n".join(p for p in parts if p)
    return str(content)


def _to_messages(messages: list[dict[str, Any]], *, vision: bool = False) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for m in messages:
        role = m.get("role")
        if role not in ("system", "user", "assistant"):
            raise ValidationFailedError(f"unsupported role: {role}")
        raw = m.get("content", "")
        # Vision models keep the OpenAI multimodal array (text + image_url blocks);
        # text-only models flatten images away.
        content = raw if vision else _flatten_content(raw)
        out.append({"role": role, "content": content})
    return out


def _approx_tokens(text: str) -> int:
    """~4 chars/token — coarse fallback when the upstream omits usage."""
    return max(1, len(text) // 4)


def _cache_token_count(
    usage: dict[str, Any], detail_key: str | tuple[str, ...], *aliases: str,
) -> int:
    details = usage.get("prompt_tokens_details")
    details = details if isinstance(details, dict) else {}
    keys = (detail_key,) if isinstance(detail_key, str) else detail_key
    candidates = [details.get(key) for key in keys] + [usage.get(key) for key in aliases]
    # An explicit zero is an observation, not permission to use a legacy alias.
    value = next((value for value in candidates if value is not None), None)
    if isinstance(value, bool):
        return 0
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError, OverflowError):
        return 0


def _cached_tokens(usage: dict[str, Any]) -> int:
    """Prompt tokens served from the provider's context cache.

    llmgw reports them as `prompt_tokens_details.cached_tokens` (OpenAI shape);
    DeepSeek and Anthropic-compatible counters remain accepted aliases.
    """
    return _cache_token_count(
        usage, "cached_tokens", "prompt_cache_hit_tokens", "cache_read_input_tokens",
    )


def _key_and_url() -> tuple[str, str]:
    settings = get_settings()
    if not settings.llmgw_api_key:
        raise UpstreamProviderError("LLMGW_API_KEY not configured")
    key = settings.llmgw_api_key.get_secret_value()
    url = f"{settings.llmgw_base_url.rstrip('/')}/chat/completions"
    return key, url


def _receipt_headers(headers: Mapping[str, str]) -> dict[str, str]:
    return {key: headers[key] for key in (
        "x-llmgw-cost-rub", "x-cost-rub", "x-cost-usd", "x-llmgw-request-id",
    ) if key in headers}


def _failure_details(data: object, headers: object = None) -> dict[str, Any]:
    # Error details cross the HTTP boundary. Keep only validated identity and
    # decimal text; the journal revalidates these scalars before persistence.
    evidence = receipt_evidence(data, headers)
    return {"provider_charge_ambiguous": True, **{
        key: str(value) if key.startswith("provider_cost_") and value is not None else value
        for key, value in evidence.items()
    }}


def normalize_usage(
    usage: dict[str, Any], fallback_input: int, fallback_output: int,
) -> tuple[dict[str, Any], bool]:
    """Keep provider usage/details, replacing only absent or invalid counters."""
    estimated = False

    def count(key: str, fallback: int) -> int:
        nonlocal estimated
        value = usage.get(key)
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            return value
        estimated = True
        return fallback

    tokens_in = count("prompt_tokens", fallback_input)
    tokens_out = count("completion_tokens", fallback_output)
    cache_read = min(tokens_in, _cached_tokens(usage))
    cache_write = min(tokens_in - cache_read, _cache_token_count(
        usage, ("cache_write_tokens", "cache_creation_tokens"), "cache_creation_input_tokens",
    ))
    return {
        **usage,
        "prompt_tokens": tokens_in, "completion_tokens": tokens_out,
        "total_tokens": tokens_in + tokens_out,
        "prompt_cache_hit_tokens": cache_read,
        "cache_creation_input_tokens": cache_write,
    }, estimated


async def astream(
    model: str,
    messages: list[dict[str, Any]],
    *,
    temperature: float = 0.5,
    max_tokens: int = _DEFAULT_MAX_TOKENS,
    timeout: float = _DEFAULT_TIMEOUT_S,  # noqa: ASYNC109 — handed to httpx.Client
    receipt: dict[str, Any] | None = None,
) -> AsyncGenerator[tuple[str, str], None]:
    """TRUE token streaming from llmgw — the page builds live in the preview.

    A sync ``httpx.Client`` on a worker thread reads the SSE incrementally and
    bridges each delta to the async caller through an ``asyncio.Queue``. Yields
    ``(delta, omnia_id)``. The optional receipt collects provider usage, cost
    headers and request identity. Only connection establishment is retried.
    """
    slug = _MODEL_SLUG.get(model)
    if slug is None:
        raise ValidationFailedError(f"unsupported llmgw model: {model}")
    key, url = _key_and_url()

    payload: dict[str, Any] = {
        "model": slug,
        "messages": _to_messages(messages, vision=_is_vision(model)),
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": True,
        # The final chunk is the provider's usage receipt, including cache/reasoning.
        "stream_options": {"include_usage": True},
    }
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
    }

    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[Any] = asyncio.Queue()
    _DONE = object()
    stopped = threading.Event()
    receipt = receipt if receipt is not None else {}

    def _produce() -> None:
        for attempt in range(2):
            try:
                with (
                    httpx.Client(
                        timeout=httpx.Timeout(timeout, connect=30.0),
                        trust_env=False,
                        mounts={"all://": httpx.HTTPTransport()},
                    ) as client,
                    client.stream("POST", url, json=payload, headers=headers) as r,
                ):
                    if r.status_code >= 400:
                        loop.call_soon_threadsafe(
                            queue.put_nowait,
                            ("err", UpstreamProviderError(f"llmgw HTTP {r.status_code}")),
                        )
                        loop.call_soon_threadsafe(queue.put_nowait, _DONE)
                        return
                    loop.call_soon_threadsafe(queue.put_nowait, ("receipt", {
                        "headers": _receipt_headers(r.headers),
                        "provider_request_id": r.headers.get("x-llmgw-request-id"),
                    }))
                    for raw in r.iter_lines():
                        if stopped.is_set():
                            return
                        if not raw or not raw.startswith("data:"):
                            continue
                        data = raw[5:].strip()
                        if data == "[DONE]":
                            loop.call_soon_threadsafe(queue.put_nowait, ("receipt", {"complete": True}))
                            loop.call_soon_threadsafe(queue.put_nowait, _DONE)
                            return
                        try:
                            obj = json.loads(data)
                        except ValueError:
                            continue
                        if not isinstance(obj, dict):
                            continue
                        if obj.get("error"):
                            raise UpstreamProviderError("llmgw stream returned an error")
                        loop.call_soon_threadsafe(queue.put_nowait, ("receipt", {
                            key: obj[key] for key in ("usage", "model", "id", "metadata")
                            if obj.get(key) is not None
                        }))
                        try:
                            delta = obj["choices"][0].get("delta", {}).get("content", "")
                        except (KeyError, IndexError, TypeError):
                            delta = ""
                        if delta:
                            loop.call_soon_threadsafe(queue.put_nowait, ("delta", delta))
                raise UpstreamProviderError(
                    "llmgw stream ended without completion",
                    details={"provider_charge_ambiguous": True},
                )
            except httpx.HTTPError as exc:
                if isinstance(exc, _SAFE_TO_RETRY) and attempt == 0:
                    time.sleep(0.5)
                    continue
                loop.call_soon_threadsafe(
                    queue.put_nowait,
                    ("err", UpstreamProviderError(
                        f"llmgw stream transport: {type(exc).__name__}",
                        details={"provider_charge_ambiguous": not isinstance(exc, _SAFE_TO_RETRY)},
                    )),
                )
                loop.call_soon_threadsafe(queue.put_nowait, _DONE)
                return
            except Exception as exc:  # noqa: BLE001 — surface as a clean error event
                loop.call_soon_threadsafe(
                    queue.put_nowait,
                    ("err", exc if isinstance(exc, UpstreamProviderError) else
                     UpstreamProviderError(f"llmgw stream error: {type(exc).__name__}")),
                )
                loop.call_soon_threadsafe(queue.put_nowait, _DONE)
                return

    threading.Thread(target=_produce, daemon=True).start()

    try:
        while True:
            item = await queue.get()
            if item is _DONE:
                break
            kind, val = item
            if kind == "err":
                if receipt.get("provider_request_id") or receipt.get("id"):
                    val.details["provider_request_id"] = receipt.get("provider_request_id") or receipt["id"]
                raise val
            if kind == "receipt":
                receipt.update(val)
                resolve_response_model(
                    receipt.get("model", ""), model,
                    receipt.get("provider_request_id") or receipt.get("id"),
                )
                continue
            yield val, slug_to_omnia(receipt.get("model", "")) or model
    finally:
        stopped.set()


async def acompletion(
    *,
    model: str,
    messages: list[dict[str, Any]],
    temperature: float = 0.5,
    max_tokens: int = _DEFAULT_MAX_TOKENS,
    timeout: float = _DEFAULT_TIMEOUT_S,  # noqa: ASYNC109 — handed to httpx.Client
) -> dict[str, Any]:
    """Call llmgw's chat surface and return an OpenAI-shaped completion dict.

    Raises:
        ValidationFailedError on bad input (unknown model / role).
        UpstreamProviderError on transport, 4xx/5xx, or empty response.
    """
    slug = _MODEL_SLUG.get(model)
    if slug is None:
        raise ValidationFailedError(f"unsupported llmgw model: {model}")
    key, url = _key_and_url()

    payload: dict[str, Any] = {
        "model": slug,
        "messages": _to_messages(messages, vision=_is_vision(model)),
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": False,
    }
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    def _completion_sync() -> dict[str, Any]:
        # trust_env=False + no-op mounts: ignore the container's HTTPS_PROXY so the
        # provider endpoint is hit DIRECT. Retry connection establishment only.
        last: Exception | None = None
        for attempt in range(2):
            try:
                with httpx.Client(
                    timeout=httpx.Timeout(timeout, connect=30.0),
                    trust_env=False,
                    mounts={"all://": httpx.HTTPTransport()},
                ) as client:
                    r = client.post(url, json=payload, headers=headers)
                    provider_headers = _receipt_headers(r.headers)
                    r.raise_for_status()
                    try:
                        result = r.json()
                        if not isinstance(result, dict):
                            raise TypeError("Response must be an object")
                    except (TypeError, ValueError):
                        raise UpstreamProviderError(
                            "llmgw: malformed response",
                            details=_failure_details({}, provider_headers),
                        ) from None
                    result["_provider_headers"] = provider_headers
                    return result
            except _SAFE_TO_RETRY as exc:
                last = exc
                if attempt == 0:
                    time.sleep(0.5)
                    continue
                raise
        raise last  # type: ignore[misc]  # unreachable — loop returns or raises

    try:
        data = await asyncio.to_thread(_completion_sync)
    except httpx.HTTPStatusError as exc:
        details = _failure_details({}, _receipt_headers(exc.response.headers))
        status = exc.response.status_code
        if type(status) is int and 400 <= status <= 599:
            details["upstream_http_status"] = status
        raise UpstreamProviderError(
            f"llmgw HTTP {exc.response.status_code}",
            details=details,
        ) from exc
    except httpx.HTTPError as exc:
        raise UpstreamProviderError(
            f"llmgw transport error: {type(exc).__name__}",
            details={
                "provider_charge_ambiguous": not isinstance(exc, _SAFE_TO_RETRY),
                "provider_failure_kind": "preconnect" if isinstance(exc, _SAFE_TO_RETRY)
                else "transport",
            },
        ) from exc

    safe_receipt = receipt_evidence(data)
    error_receipt = _failure_details(data)
    try:
        choice = (data.get("choices") or [])[0]
        content = (choice.get("message") or {}).get("content")
        if content is None:
            content = ""
        if not isinstance(content, str):
            raise TypeError("Content must be text")
        content = _strip_reasoning(content)
        raw_usage = data.get("usage")
        metadata = data.get("metadata")
        if ((raw_usage is not None and not isinstance(raw_usage, dict))
                or (metadata is not None and not isinstance(metadata, dict))):
            raise TypeError("Usage and metadata must be objects")
        usage, estimated = normalize_usage(
            raw_usage or {},
            _approx_tokens("".join(_flatten_content(m.get("content", "")) for m in messages)),
            _approx_tokens(content),
        )

        actual_model = resolve_response_model(data.get("model", ""), model, safe_receipt["provider_request_id"])
        # Keep normalization inside the same receipt-preserving guard: invalid
        # timestamps, metadata, models or content cannot discard money evidence.
        return {
            "id": data.get("id") or f"llmgw-{uuid4()}",
            "object": "chat.completion",
            "created": int(data.get("created") or time.time()),
            "model": actual_model,
            "_provider_headers": data["_provider_headers"],
            "metadata": {
                **(metadata or {}),
                "provider_request_id": safe_receipt["provider_request_id"],
                "estimated_tokens": estimated,
            },
            "choices": [{
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": (choice.get("finish_reason") or "stop"),
            }],
            "usage": usage,
        }
    except UpstreamProviderError as exc:
        exc.details.update(error_receipt)
        raise
    except (TypeError, ValueError, AttributeError, KeyError, IndexError, OverflowError):
        raise UpstreamProviderError("llmgw: malformed response", details=error_receipt) from None
