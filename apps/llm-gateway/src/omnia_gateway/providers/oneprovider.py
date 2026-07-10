"""oneprovider.dev provider — chat completions over its OpenAI-compatible surface.

Per the oneprovider docs (https://oneprovider.dev/llms.txt) the OpenAI-shape
surface lives at ``https://api.oneprovider.dev/v1`` and answers
``POST /v1/chat/completions`` with ``Authorization: Bearer <ONEPROVIDER_API_KEY>``.
This module drives the gateway's ``/v1/chat/completions`` route (chat.py) and the
SSE stream (services/streaming.py) — both non-streaming and true token streaming.

Why a sync ``httpx.Client`` on a worker thread instead of ``AsyncClient``: the
gateway container may carry an ``HTTPS_PROXY`` (a UK egress used only to
geo-bypass Google), and an ``AsyncClient`` inside the long-lived uvicorn loop
intermittently stalls the TLS handshake. ``trust_env=False`` + an explicit no-op
``mounts`` transport ignores the proxy unconditionally, and a fresh sync client
on ``asyncio.to_thread`` connects in ~300 ms.

R-01 (deep module): callers see ``is_oneprovider_model()`` + ``acompletion()`` +
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
from typing import Any
from uuid import uuid4

import httpx

from omnia_gateway.core.config import get_settings
from omnia_gateway.core.errors import UpstreamProviderError, ValidationFailedError
from omnia_gateway.services.prompt_cache import apply_anthropic_cache

# Transient transport faults worth one retry (the TLS handshake to the reseller
# edge intermittently stalls inside a long-lived process).
_TRANSIENT = (
    httpx.ConnectError,
    httpx.ConnectTimeout,
    httpx.ReadTimeout,
    httpx.RemoteProtocolError,
)

# Omnia model ID → the exact catalog id sent as the OpenAI `model` field. The slug
# is forwarded verbatim, so it MUST match oneprovider's catalog. Add a row here to
# expose another oneprovider-served chat model.
_MODEL_SLUG: dict[str, str] = {
    "claude-opus-4-8": "claude-opus-4-8",
}

# Natively multimodal models — keep OpenAI image_url blocks instead of flattening
# them (the acceptance/vision judge + the agent `see` tool send screenshots).
_MULTIMODAL: frozenset[str] = frozenset({"claude-opus-4-8"})

_DEFAULT_MAX_TOKENS = 32768
# Long art-director / writer passes run ~150s non-streaming. 240s clears them while
# bounding a genuine hang, and stays under the api client's 300s read timeout so a
# real upstream failure surfaces as a clean error, not a client socket teardown.
_DEFAULT_TIMEOUT_S = 240.0

# Strip a leaked chain-of-thought — some upstreams inline `<think>…</think>` in the
# content, which would break a downstream PageIR JSON parse.
_THINK_BLOCK = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)


def is_oneprovider_model(model_id: str) -> bool:
    """True if this model is served by oneprovider's chat surface."""
    return model_id in _MODEL_SLUG


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


def _to_messages(
    messages: list[dict[str, Any]], *, vision: bool = False
) -> list[dict[str, Any]]:
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


def _key_and_url() -> tuple[str, str]:
    settings = get_settings()
    if not settings.oneprovider_api_key:
        raise UpstreamProviderError("ONEPROVIDER_API_KEY not configured")
    key = settings.oneprovider_api_key.get_secret_value()
    url = f"{settings.oneprovider_openai_base_url.rstrip('/')}/chat/completions"
    return key, url


async def astream(
    model: str,
    messages: list[dict[str, Any]],
    *,
    temperature: float = 0.5,
    max_tokens: int = _DEFAULT_MAX_TOKENS,
    timeout: float = _DEFAULT_TIMEOUT_S,  # noqa: ASYNC109 — handed to httpx.Client
) -> AsyncIterator[tuple[str, str]]:
    """TRUE token streaming from oneprovider — the page builds live in the preview.

    A sync ``httpx.Client`` on a worker thread reads the SSE incrementally and
    bridges each delta to the async caller through an ``asyncio.Queue``. Yields
    ``(delta, omnia_id)``. Retries once on a transient fault that hits BEFORE the
    first delta; mid-stream faults propagate.
    """
    slug = _MODEL_SLUG.get(model)
    if slug is None:
        raise ValidationFailedError(f"unsupported oneprovider model: {model}")
    key, url = _key_and_url()

    # Anthropic prompt caching: wrap the stable system prefix in `cache_control:
    # ephemeral`. _is_vision keeps the resulting block array intact for claude.
    messages = apply_anthropic_cache(model, messages)
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

    def _produce() -> None:
        emitted = False
        for attempt in range(2):
            try:
                with httpx.Client(
                    timeout=httpx.Timeout(timeout, connect=30.0),
                    trust_env=False,
                    mounts={"all://": httpx.HTTPTransport()},
                ) as client, client.stream("POST", url, json=payload, headers=headers) as r:
                    if r.status_code >= 400:
                        body = r.read().decode("utf-8", "replace")[:300]
                        loop.call_soon_threadsafe(
                            queue.put_nowait,
                            ("err", f"oneprovider HTTP {r.status_code}: {body}"),
                        )
                        loop.call_soon_threadsafe(queue.put_nowait, _DONE)
                        return
                    for raw in r.iter_lines():
                        if not raw or not raw.startswith("data:"):
                            continue
                        data = raw[5:].strip()
                        if data == "[DONE]":
                            loop.call_soon_threadsafe(queue.put_nowait, _DONE)
                            return
                        try:
                            obj = json.loads(data)
                        except ValueError:
                            continue
                        usage = obj.get("usage")
                        if usage:
                            print(
                                f"[ONEPROVIDER] stream usage model={model} "
                                f"prompt={usage.get('prompt_tokens')} "
                                f"completion={usage.get('completion_tokens')} "
                                f"cache_hit={usage.get('prompt_cache_hit_tokens')}",
                                flush=True,
                            )
                        try:
                            delta = (
                                obj["choices"][0].get("delta", {}).get("content", "")
                            )
                        except (KeyError, IndexError, TypeError):
                            delta = ""
                        if delta:
                            emitted = True
                            loop.call_soon_threadsafe(queue.put_nowait, ("delta", delta))
                loop.call_soon_threadsafe(queue.put_nowait, _DONE)
                return
            except _TRANSIENT as exc:
                if not emitted and attempt == 0:
                    time.sleep(0.5)
                    continue
                loop.call_soon_threadsafe(
                    queue.put_nowait,
                    ("err", f"oneprovider stream transport: {type(exc).__name__}: {exc}"),
                )
                loop.call_soon_threadsafe(queue.put_nowait, _DONE)
                return
            except Exception as exc:  # noqa: BLE001 — surface as a clean error event
                loop.call_soon_threadsafe(
                    queue.put_nowait,
                    ("err", f"oneprovider stream error: {type(exc).__name__}: {exc}"),
                )
                loop.call_soon_threadsafe(queue.put_nowait, _DONE)
                return

    threading.Thread(target=_produce, daemon=True).start()

    while True:
        item = await queue.get()
        if item is _DONE:
            break
        kind, val = item
        if kind == "err":
            raise UpstreamProviderError(val)
        yield val, model


async def acompletion(
    *,
    model: str,
    messages: list[dict[str, Any]],
    temperature: float = 0.5,
    max_tokens: int = _DEFAULT_MAX_TOKENS,
    timeout: float = _DEFAULT_TIMEOUT_S,  # noqa: ASYNC109 — handed to httpx.Client
) -> dict[str, Any]:
    """Call oneprovider's chat surface and return an OpenAI-shaped completion dict.

    Raises:
        ValidationFailedError on bad input (unknown model / role).
        UpstreamProviderError on transport, 4xx/5xx, or empty response.
    """
    slug = _MODEL_SLUG.get(model)
    if slug is None:
        raise ValidationFailedError(f"unsupported oneprovider model: {model}")
    key, url = _key_and_url()

    messages = apply_anthropic_cache(model, messages)
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
        # reseller endpoint is hit DIRECT. Retry a transient transport fault once.
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
                    return r.json()
            except _TRANSIENT as exc:
                last = exc
                if attempt == 0:
                    time.sleep(0.5)
                    continue
                raise
        raise last  # type: ignore[misc]  # unreachable — loop returns or raises

    try:
        data = await asyncio.to_thread(_completion_sync)
    except httpx.HTTPStatusError as exc:
        print(
            f"[ONEPROVIDER] HTTP {exc.response.status_code}: {exc.response.text[:300]!r}",
            flush=True,
        )
        raise UpstreamProviderError(
            f"oneprovider HTTP {exc.response.status_code}",
            details={"body": exc.response.text[:500]},
        ) from exc
    except httpx.HTTPError as exc:
        raise UpstreamProviderError(
            f"oneprovider transport error: {type(exc).__name__}: {exc}"
        ) from exc

    try:
        choice = (data.get("choices") or [])[0]
        content = (choice.get("message") or {}).get("content") or ""
    except (IndexError, AttributeError, KeyError) as exc:
        raise UpstreamProviderError(
            "oneprovider: malformed response", details={"body": str(data)[:500]}
        ) from exc
    content = _strip_reasoning(content)

    usage = data.get("usage") or {}
    tokens_in = int(
        usage.get("prompt_tokens")
        or _approx_tokens("".join(_flatten_content(m.get("content", "")) for m in messages))
    )
    tokens_out = int(usage.get("completion_tokens") or _approx_tokens(content))
    cache_hit_tokens = int(usage.get("prompt_cache_hit_tokens") or 0)

    # Normalize to OpenAI shape with `model` = the Omnia id so chat.py bills against
    # PRICE_TABLE (slug_to_omnia is identity for oneprovider models).
    return {
        "id": data.get("id") or f"oneprovider-{uuid4()}",
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
