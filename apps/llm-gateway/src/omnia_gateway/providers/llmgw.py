"""OpenAI-compatible text provider for llmgw.ru and the Sonnet fallback.

The public API lives under ``https://api.llmgw.ru/v1`` and uses
``Authorization: Bearer <LLMGW_API_KEY>``. The provider requires canonical
Gemini uses llmgw's vendor-prefixed id. Sonnet 5 uses AITunnel's canonical
``claude-sonnet-5`` id because the production LLMGW credential can be rotated
independently without taking MAX Studio offline.

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
from collections.abc import AsyncIterator
from typing import Any, cast
from uuid import uuid4

import httpx

from omnia_gateway.core.config import get_settings
from omnia_gateway.core.errors import (
    PaidCallAmbiguousError,
    UpstreamProviderError,
    ValidationFailedError,
)

# Only failures proven to happen before a request reaches the paid provider may
# be retried. Read/write/protocol failures are ambiguous even before the first
# streamed delta: the provider may already have completed and billed the call.
_SAFE_CONNECT_RETRY = (
    httpx.ConnectError,
    httpx.ConnectTimeout,
    httpx.PoolTimeout,
)

_AMBIGUOUS_HTTP_STATUSES = frozenset({408, 425, 429})


def _http_status_is_ambiguous(status_code: int) -> bool:
    return status_code >= 500 or status_code in _AMBIGUOUS_HTTP_STATUSES


# Omnia model ID → the exact llmgw catalog id sent as the OpenAI `model` field.
_MODEL_SLUG: dict[str, str] = {
    "gemini-3.1-pro-preview-customtools": "google/gemini-3.1-pro-preview-customtools",
    "claude-sonnet-5": "claude-sonnet-5",
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


def _cached_tokens(usage: dict[str, Any]) -> int:
    """Prompt tokens served from the provider's context cache.

    llmgw reports them as `prompt_tokens_details.cached_tokens` (OpenAI shape);
    `prompt_cache_hit_tokens` is kept as a fallback for DeepSeek-style upstreams.
    """
    details = usage.get("prompt_tokens_details") or {}
    return int(details.get("cached_tokens") or usage.get("prompt_cache_hit_tokens") or 0)


def _key_and_url(model: str) -> tuple[str, str]:
    settings = get_settings()
    if model == "claude-sonnet-5":
        if not settings.aitunnel_api_key:
            raise UpstreamProviderError("AITUNNEL_API_KEY not configured for Sonnet 5")
        key = settings.aitunnel_api_key.get_secret_value()
        url = f"{settings.aitunnel_base_url.rstrip('/')}/chat/completions"
        return key, url
    if not settings.llmgw_api_key:
        raise UpstreamProviderError("LLMGW_API_KEY not configured")
    key = settings.llmgw_api_key.get_secret_value()
    url = f"{settings.llmgw_base_url.rstrip('/')}/chat/completions"
    return key, url


async def astream(
    model: str,
    messages: list[dict[str, Any]],
    *,
    temperature: float = 0.5,
    max_tokens: int = _DEFAULT_MAX_TOKENS,
    timeout: float = _DEFAULT_TIMEOUT_S,  # noqa: ASYNC109 — handed to httpx.Client
) -> AsyncIterator[tuple[str, str]]:
    """TRUE token streaming from llmgw — the page builds live in the preview.

    A sync ``httpx.Client`` on a worker thread reads the SSE incrementally and
    bridges each delta to the async caller through an ``asyncio.Queue``. Yields
    ``(delta, omnia_id)``. Retries once on a transient fault that hits BEFORE the
    first delta; mid-stream faults propagate.
    """
    slug = _MODEL_SLUG.get(model)
    if slug is None:
        raise ValidationFailedError(f"unsupported llmgw model: {model}")
    key, url = _key_and_url(model)

    payload: dict[str, Any] = {
        "model": slug,
        "messages": _to_messages(messages, vision=_is_vision(model)),
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": True,
        # Ask for a final usage chunk so we can LOG cache-hit tokens on the big
        # stable system prefix (billing stays gateway-token-counted).
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

    def _put(item: object) -> bool:
        """Best-effort bridge that cannot outlive the consuming event loop."""

        if stopped.is_set() or loop.is_closed():
            return False
        try:
            loop.call_soon_threadsafe(queue.put_nowait, item)
        except RuntimeError:
            # The async generator may have raised on an ambiguity marker and
            # its test/request loop can close before the producer posts DONE.
            return False
        return True

    def _produce() -> None:
        emitted = False
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
                        # Consume the body so the connection closes cleanly, but
                        # never surface provider diagnostics (they may contain
                        # account data). 408/425/429/5xx are ambiguous after an
                        # accepted paid request and must stop every later pass.
                        r.read()
                        kind = "ambiguous" if _http_status_is_ambiguous(r.status_code) else "err"
                        _put((kind, f"llmgw HTTP {r.status_code}"))
                        _put(_DONE)
                        return
                    saw_terminal = False
                    malformed_payload = False
                    for raw in r.iter_lines():
                        if not raw or not raw.startswith("data:"):
                            continue
                        data = raw[5:].strip()
                        if data == "[DONE]":
                            invalid_completion = malformed_payload or not emitted
                            _put(
                                (
                                    "ambiguous",
                                    "llmgw stream ended without usable completion content",
                                )
                                if invalid_completion
                                else _DONE
                            )
                            if invalid_completion:
                                _put(_DONE)
                            return
                        try:
                            obj = json.loads(data)
                        except ValueError:
                            malformed_payload = True
                            continue
                        if not isinstance(obj, dict):
                            malformed_payload = True
                            continue
                        if obj.get("error"):
                            _put(("ambiguous", "llmgw stream returned an error event"))
                            _put(_DONE)
                            return
                        usage = obj.get("usage")
                        if usage:
                            print(
                                f"[LLMGW] stream usage model={model} "
                                f"prompt={usage.get('prompt_tokens')} "
                                f"completion={usage.get('completion_tokens')} "
                                f"cache_hit={_cached_tokens(usage)}",
                                flush=True,
                            )
                        try:
                            choice = obj["choices"][0]
                            if choice.get("finish_reason") is not None:
                                saw_terminal = True
                            delta = choice.get("delta", {}).get("content", "")
                        except (KeyError, IndexError, TypeError):
                            delta = ""
                        if delta:
                            emitted = True
                            _put(("delta", delta))
                if saw_terminal and emitted and not malformed_payload:
                    _put(_DONE)
                else:
                    _put(("ambiguous", "llmgw stream ended without a terminal marker"))
                    _put(_DONE)
                return
            except _SAFE_CONNECT_RETRY as exc:
                if not emitted and attempt == 0:
                    time.sleep(0.5)
                    continue
                _put(
                    (
                        "ambiguous" if emitted else "err",
                        f"llmgw stream transport: {type(exc).__name__}",
                    ),
                )
                _put(_DONE)
                return
            except httpx.HTTPError as exc:
                _put(
                    ("ambiguous", f"llmgw stream response lost: {type(exc).__name__}"),
                )
                _put(_DONE)
                return
            except Exception as exc:  # noqa: BLE001 — response processing is ambiguous
                _put(
                    ("ambiguous", f"llmgw stream error: {type(exc).__name__}"),
                )
                _put(_DONE)
                return

    threading.Thread(target=_produce, daemon=True).start()

    try:
        while True:
            item = await queue.get()
            if item is _DONE:
                break
            kind, val = item
            if kind == "ambiguous":
                raise PaidCallAmbiguousError(val)
            if kind == "err":
                raise UpstreamProviderError(val)
            yield val, model
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
    key, url = _key_and_url(model)

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
        # provider endpoint is hit DIRECT. Retry only a proven pre-send connect fault.
        last: Exception | None = None
        for attempt in range(2):
            try:
                with httpx.Client(
                    timeout=httpx.Timeout(timeout, connect=30.0),
                    trust_env=False,
                    mounts={"all://": httpx.HTTPTransport()},
                ) as client:
                    r = client.post(url, json=payload, headers=headers)
                    r.raise_for_status()
                    try:
                        data = r.json()
                    except ValueError as exc:
                        raise PaidCallAmbiguousError(
                            "llmgw returned malformed JSON after accepting the request"
                        ) from exc
                    if not isinstance(data, dict):
                        raise PaidCallAmbiguousError(
                            "llmgw returned a non-object response after accepting the request"
                        )
                    return cast(dict[str, Any], data)
            except _SAFE_CONNECT_RETRY as exc:
                last = exc
                if attempt == 0:
                    time.sleep(0.5)
                    continue
                raise
        raise last  # type: ignore[misc]  # unreachable — loop returns or raises

    try:
        data = await asyncio.to_thread(_completion_sync)
    except httpx.HTTPStatusError as exc:
        print(f"[LLMGW] HTTP {exc.response.status_code}", flush=True)
        if _http_status_is_ambiguous(exc.response.status_code):
            raise PaidCallAmbiguousError(
                f"llmgw HTTP {exc.response.status_code} after a paid request"
            ) from exc
        raise UpstreamProviderError(
            f"llmgw HTTP {exc.response.status_code}",
        ) from exc
    except _SAFE_CONNECT_RETRY as exc:
        raise UpstreamProviderError(f"llmgw connection failed: {type(exc).__name__}") from exc
    except httpx.HTTPError as exc:
        raise PaidCallAmbiguousError(
            f"llmgw response status is unknown after {type(exc).__name__}"
        ) from exc

    try:
        choice = (data.get("choices") or [])[0]
        message = choice.get("message") or {}
        content = message.get("content")
        if not isinstance(content, str) or not content.strip():
            raise ValueError("missing completion content")
    except (IndexError, AttributeError, KeyError, TypeError, ValueError) as exc:
        raise PaidCallAmbiguousError(
            "llmgw returned a structurally malformed response after a paid request"
        ) from exc
    content = _strip_reasoning(content)

    usage = data.get("usage") or {}
    tokens_in = int(
        usage.get("prompt_tokens")
        or _approx_tokens("".join(_flatten_content(m.get("content", "")) for m in messages))
    )
    tokens_out = int(usage.get("completion_tokens") or _approx_tokens(content))
    cache_hit_tokens = _cached_tokens(usage)

    # Normalize to OpenAI shape with `model` = the Omnia id so chat.py bills against
    # PRICE_TABLE (the upstream slug maps back via _SLUG_TO_OMNIA).
    return {
        "id": data.get("id") or f"llmgw-{uuid4()}",
        "object": "chat.completion",
        "created": int(data.get("created") or time.time()),
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": (choice.get("finish_reason") or "stop"),
            }
        ],
        "usage": {
            "prompt_tokens": tokens_in,
            "completion_tokens": tokens_out,
            "total_tokens": tokens_in + tokens_out,
            "prompt_cache_hit_tokens": cache_hit_tokens,
        },
    }
