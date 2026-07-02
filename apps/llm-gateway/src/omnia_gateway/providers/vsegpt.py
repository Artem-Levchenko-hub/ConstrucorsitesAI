"""VseGPT provider — OpenAI-compatible chat completion via a sync httpx call on
a worker thread.

Why a custom wrapper instead of the LiteLLM Router: two independent failure
modes made the Router path unusable for api.vsegpt.ru on the prod VPS.

  1. Outbound proxy leak. The gateway container has HTTPS_PROXY set (a UK egress
     used only to geo-bypass Google for Gemini). `NO_PROXY` whitelists
     proxyapi.ru / Sber / Postgres so they stay direct — but vsegpt.ru was never
     added. LiteLLM/httpx therefore tunnelled vsegpt traffic through the UK proxy
     and the call died with APIConnectionError ("litellm Connection error").
  2. AsyncClient TLS stall. Even with the proxy bypassed, httpx.AsyncClient
     inside the long-running uvicorn loop intermittently hangs the TLS handshake
     to api.vsegpt.ru — the exact symptom Sber hit (see providers/sber.py). A
     sync httpx.Client on a fresh thread connects in ~300 ms.

The fix mirrors providers/sber.py: a sync httpx.Client with `trust_env=False`
AND `mounts={"all://": HTTPTransport()}` so proxy env is ignored unconditionally,
run via `asyncio.to_thread`. Direct calls "in a vacuum" always worked; the bug
was purely the live-server proxy + event-loop interaction.

R-01 (deep module): callers see `is_vsegpt_model()` + `acompletion()`. Proxy
quirks, chain-of-thought stripping, and error translation live entirely inside.
R-10 (Release It!): explicit timeouts, fail fast on transport/4xx/5xx, and strip
`<think>` blocks so a verbose thinking model can't corrupt a downstream JSON
parse (director/polish emit PageIR JSON).
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

# Transient transport faults worth one retry (vsegpt's TLS handshake to
# api.vsegpt.ru intermittently stalls inside a long-lived process).
_TRANSIENT = (
    httpx.ConnectError,
    httpx.ConnectTimeout,
    httpx.ReadTimeout,
    httpx.RemoteProtocolError,
)

# Omnia model ID → the model name vsegpt.ru proxies through to DeepSeek.
# vsegpt is an OpenAI-compatible aggregator; the slug is sent verbatim as the
# `model` field. Add a row here to expose another vsegpt-fronted model.
_VSEGPT_MODEL_SLUG: dict[str, str] = {
    # DeepSeek V3 (≈128K context) — the worker model for generation. Its big
    # context fits the ~32K polish prompt PLUS the output; the V4-flash-thinking
    # endpoint caps at 16384 TOTAL tokens, so a real page prompt 400s with
    # context_length_exceeded (that's why DeepSeek "didn't run" in orchestration
    # and the IR fell back to the director / Haiku).
    "deepseek-chat": "deepseek/deepseek-chat",
    "deepseek-v4-flash-thinking": "deepseek/deepseek-v4-flash-thinking",
    # Opus 4.8 via vsegpt (owner 2026-07-01, key sk-or-vv-…). vsegpt sends NO
    # `thinking` param → Opus runs with extended thinking OFF and answers in ~3s
    # (MEASURED), versus ~71s on oneprovider.dev which forces thinking regardless
    # of {type:disabled} → the 30s-POST-timeout root cause. is_vsegpt_model() now
    # returns True, so Opus is dispatched by THIS direct provider (streaming.py +
    # litellm_router.acompletion) BEFORE the Router; the oneprovider entry in
    # litellm_router._PROXY_ROUTES stays as a fallback target. Note: no continuation
    # wrapper on this native-stream path (the Router's 32k-cap auto-resume), and
    # vsegpt doesn't honour Anthropic prompt caching — accepted for the latency win.
    "claude-opus-4-8": "anthropic/claude-opus-4.8",
    # Orchestrator (art_director / design-brain) — owner pick 2026-06-02.
    "gemini-3.5-flash-high": "google/gemini-3.5-flash-high",
    # Developer (freeform_writer — writes the HTML) — owner pick 2026-06-02.
    "minimax-m2.7": "minimax/minimax-m2.7",
    # Owner pick 2026-06-02: ONE strong thinking model for BOTH orchestrator and
    # coder. 1M context (no 16K-cap orchestration break like v4-flash-thinking);
    # reasoning lands in a separate field, so `content` stays clean HTML/brief.
    "deepseek-v4-pro-thinking": "deepseek/deepseek-v4-pro-thinking",
    # Owner pick 2026-06-02: deepseek-v4-pro (NON-thinking) as the coder — same
    # 1M-context family, no reasoning overhead → faster, clean HTML. "Deepseek
    # everywhere" for reliability.
    "deepseek-v4-pro": "deepseek/deepseek-v4-pro",
    # Vision judge for the acceptance gate (owner pick 2026-06-02: Gemini 3 Flash
    # Preview). vsegpt `vis-` prefix = multimodal — the provider PASSES image_url
    # blocks for these (see `_is_vision` + _to_vsegpt_messages) instead of
    # flattening, so the screenshot actually reaches the model. DeepSeek has no
    # vision model, so the judge is Gemini via vsegpt.
    "gemini-3-flash-vision": "vis-google/gemini-3-flash-pre",
    # Kimi K2.6 (thinking) — Moonshot 1T/32B MoE, NATIVE multimodal + strong
    # design taste. The design-brain DeepSeek can't be: no vision, weaker
    # aesthetics → generic/monotone output. art_director (brief author) model.
    # Confirmed served by vsegpt on the SAME VSEGPT_API_KEY (2026-06-03 live
    # test → real completion + separate `reasoning` field). Thinking ⇒ ~150-200s
    # for the brief, inside _DEFAULT_TIMEOUT_S=240s. Reasoning is a separate
    # field, so `content` stays clean (no inline <think> to strip).
    "kimi-k2.6-thinking": "moonshotai/kimi-k2.6-thinking",
    # Kimi K2.6 NON-thinking — same taste/vision, no long reasoning. The -thinking
    # variant 502s as art_director (deep reasoning on the large brief prompt exceeds
    # the 240s upstream timeout → empty brief → generic build). Non-thinking returns
    # the brief fast; art_director default (2026-06-07).
    "kimi-k2.6": "moonshotai/kimi-k2.6",
}


# Natively multimodal models that accept OpenAI image_url blocks even though their
# vsegpt slug carries no `vis-` prefix. Anthropic Claude is multimodal, so vsegpt
# forwards image_url to anthropic/claude-opus-4.8. Owner 2026-06-29: Opus 4.8 is the
# screenshot/vision judge (acceptance gate + agent `see` tool), so it MUST keep the
# image blocks instead of having them flattened away.
_NATIVE_MULTIMODAL: frozenset[str] = frozenset({"claude-opus-4-8"})


def _is_vision(model_id: str) -> bool:
    """A vsegpt multimodal model — keeps image_url blocks instead of flattening them.
    True for `vis-`-prefixed slugs AND for natively-multimodal models (Claude Opus).
    Text-only models get image blocks dropped (`_flatten_content`)."""
    if model_id in _NATIVE_MULTIMODAL:
        return True
    return _VSEGPT_MODEL_SLUG.get(model_id, "").startswith("vis-")

# Default ceiling for a thinking model: chain-of-thought shares the token budget
# with the visible answer, so a small cap silently truncates the real output.
# 32768 leaves room for a thinking model's reasoning PLUS a full landing page —
# the writer pass can emit a large HTML doc and the deepseek-v4-pro-thinking
# coder spends extra tokens reasoning first (1M context, so this is cheap headroom).
_DEFAULT_MAX_TOKENS = 32768
# The art_director (Opus 4.8) writes a long, detailed brief — ~150s of
# non-streaming inference is normal. The old 115s read timeout cut it off, the
# gateway raised ReadTimeout, and the brief was DROPPED (writer ran alone, Opus
# spend wasted). 240s clears Opus while still bounding a genuine hang, and stays
# under the API client's 300 s read timeout (apps/api llm_client) so a real
# upstream failure surfaces as a clean error, not a client socket teardown.
_DEFAULT_TIMEOUT_S = 240.0

# Strip a leaked chain-of-thought. vsegpt normally returns reasoning in a
# separate field, but some upstreams inline `<think>…</think>` in the content —
# which would break the PageIR JSON parse downstream.
_THINK_BLOCK = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)


def is_vsegpt_model(model_id: str) -> bool:
    # Opus 4.8 can be pinned back to oneprovider via OPUS_VIA_VSEGPT=false — the
    # reversible failover for when the vsegpt balance runs dry (every call → HTTP
    # 400 "out of budget", which aborts a build as «Сборка прервана»). Returning
    # False routes Opus through the LiteLLM Router (oneprovider) on ALL three
    # dispatch points that gate on this fn: litellm_router.acompletion,
    # services/streaming.py, and routers/messages_native.py (native /v1/messages).
    if model_id == "claude-opus-4-8" and not get_settings().opus_via_vsegpt:
        return False
    return model_id in _VSEGPT_MODEL_SLUG


def _strip_reasoning(text: str) -> str:
    """Remove inline `<think>` blocks; keep the original if that empties it."""
    cleaned = _THINK_BLOCK.sub("", text).strip()
    return cleaned or text.strip()


def _flatten_content(content: Any) -> str:
    """vsegpt DeepSeek is text-only. If a caller passes multimodal blocks (a
    list of {type,text|image_url}), keep the text parts and drop images so the
    request still goes through instead of 400-ing."""
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


def _to_vsegpt_messages(
    messages: list[dict[str, Any]], *, vision: bool = False
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for m in messages:
        role = m.get("role")
        if role not in ("system", "user", "assistant"):
            raise ValidationFailedError(f"unsupported role: {role}")
        raw = m.get("content", "")
        # Vision models keep the OpenAI multimodal array (text + image_url blocks)
        # so the screenshot reaches the judge; text-only models flatten images away.
        content = raw if vision else _flatten_content(raw)
        out.append({"role": role, "content": content})
    return out


def _approx_tokens(text: str) -> int:
    """~4 chars/token — coarse fallback when vsegpt omits usage."""
    return max(1, len(text) // 4)


async def astream(
    model: str,
    messages: list[dict[str, Any]],
    *,
    temperature: float = 0.5,
    max_tokens: int = _DEFAULT_MAX_TOKENS,
    timeout: float = _DEFAULT_TIMEOUT_S,
) -> AsyncIterator[tuple[str, str]]:
    """TRUE token streaming from vsegpt.ru — the page builds live in the preview.

    Same constraints as acompletion (AsyncClient TLS-stalls in the uvicorn loop →
    sync httpx.Client on a worker thread, trust_env=False + no-op mounts), but the
    thread reads the SSE INCREMENTALLY and bridges each delta to the async caller
    through an asyncio.Queue. Yields ``(delta, omnia_id)``. Retries once on a
    transient fault that hits BEFORE the first delta; mid-stream faults propagate.
    """
    slug = _VSEGPT_MODEL_SLUG.get(model)
    if slug is None:
        raise ValidationFailedError(f"unsupported vsegpt model: {model}")
    settings = get_settings()
    if not settings.vsegpt_api_key:
        raise UpstreamProviderError("VSEGPT_API_KEY not configured")

    # Anthropic prompt caching: wrap the stable system prefix in `cache_control:
    # ephemeral`. For claude-* this turns the system content into a block array;
    # _is_vision treats claude-opus-4-8 as multimodal so _to_vsegpt_messages keeps
    # the array (incl. cache_control) instead of flattening it. NOTE: vsegpt does
    # not currently honour Anthropic caching (it bills the full prompt each call) —
    # this is a no-op there today, but it's correct plumbing that activates real
    # cache savings the moment opus routes through a caching upstream (proxyapi).
    messages = apply_anthropic_cache(model, messages)
    url = f"{settings.vsegpt_base_url.rstrip('/')}/chat/completions"
    payload: dict[str, Any] = {
        "model": slug,
        "messages": _to_vsegpt_messages(messages, vision=_is_vision(model)),
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": True,
        # Ask the upstream to emit a final usage chunk (OpenAI-compatible). For
        # DeepSeek/Gemini this carries `prompt_cache_hit_tokens` — the proof that
        # the big stable system prefix is being served from the provider context
        # cache (the whole point of keeping the prefix first + byte-stable). We
        # only LOG it here (the stream's billing stays token-counted in the
        # gateway); ignored by aggregators that don't support it.
        "stream_options": {"include_usage": True},
    }
    headers = {
        "Authorization": f"Bearer {settings.vsegpt_api_key.get_secret_value()}",
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
                ) as client:
                    with client.stream("POST", url, json=payload, headers=headers) as r:
                        if r.status_code >= 400:
                            body = r.read().decode("utf-8", "replace")[:300]
                            loop.call_soon_threadsafe(
                                queue.put_nowait, ("err", f"vsegpt HTTP {r.status_code}: {body}")
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
                            # Final usage chunk (stream_options.include_usage): its
                            # `choices` is usually empty and it carries upstream
                            # token counts incl. the cache-hit proof. Log it so the
                            # owner can SEE whether the stable system prefix is
                            # being served from the DeepSeek/Gemini context cache.
                            usage = obj.get("usage")
                            if usage:
                                print(
                                    f"[VSEGPT] stream usage model={model} "
                                    f"prompt={usage.get('prompt_tokens')} "
                                    f"completion={usage.get('completion_tokens')} "
                                    f"cache_hit={usage.get('prompt_cache_hit_tokens')} "
                                    f"cache_miss={usage.get('prompt_cache_miss_tokens')}",
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
                    ("err", f"vsegpt stream transport: {type(exc).__name__}: {exc}"),
                )
                loop.call_soon_threadsafe(queue.put_nowait, _DONE)
                return
            except Exception as exc:  # noqa: BLE001 — surface as a clean error event
                loop.call_soon_threadsafe(
                    queue.put_nowait, ("err", f"vsegpt stream error: {type(exc).__name__}: {exc}")
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
    """Call vsegpt.ru and return an OpenAI-shaped completion dict.

    Raises:
        ValidationFailedError on bad input (unknown model / role).
        UpstreamProviderError on transport, 4xx/5xx, or empty response.
    """
    slug = _VSEGPT_MODEL_SLUG.get(model)
    if slug is None:
        raise ValidationFailedError(f"unsupported vsegpt model: {model}")

    settings = get_settings()
    if not settings.vsegpt_api_key:
        raise UpstreamProviderError("VSEGPT_API_KEY not configured")

    # Anthropic prompt caching on the stable system prefix (see astream for the
    # full note). No-op on vsegpt today (it doesn't honour Anthropic caching);
    # correct plumbing that activates once opus routes through a caching upstream.
    messages = apply_anthropic_cache(model, messages)
    url = f"{settings.vsegpt_base_url.rstrip('/')}/chat/completions"
    payload: dict[str, Any] = {
        "model": slug,
        "messages": _to_vsegpt_messages(messages, vision=_is_vision(model)),
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": False,
    }
    headers = {
        "Authorization": f"Bearer {settings.vsegpt_api_key.get_secret_value()}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    # vsegpt.ru's TLS handshake is usually ~0.1–2.5 s from the VPS but
    # occasionally one-off times out; retry the transient transport faults once
    # with a fresh client. A 4xx/5xx is surfaced immediately so the caller can
    # fall back (R-10: handle transient faults, fail fast on real errors).
    _TRANSIENT = (
        httpx.ConnectError,
        httpx.ConnectTimeout,
        httpx.ReadTimeout,
        httpx.RemoteProtocolError,
    )

    def _completion_sync() -> dict[str, Any]:
        # trust_env=False + an explicit no-op mounts transport: ignore the
        # container's HTTPS_PROXY env entirely so vsegpt.ru is hit DIRECT (the
        # UK egress proxy used for Gemini must never tunnel a RU endpoint).
        # See module docstring + providers/sber.py for the full rationale.
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
            f"[VSEGPT] HTTP {exc.response.status_code}: {exc.response.text[:300]!r}",
            flush=True,
        )
        raise UpstreamProviderError(
            f"vsegpt HTTP {exc.response.status_code}",
            details={"body": exc.response.text[:500]},
        ) from exc
    except httpx.HTTPError as exc:
        import traceback as _tb

        print(
            f"[VSEGPT] transport error {type(exc).__name__} {exc!r}\n{_tb.format_exc()}",
            flush=True,
        )
        raise UpstreamProviderError(
            f"vsegpt transport error: {type(exc).__name__}: {exc}"
        ) from exc

    try:
        choice = (data.get("choices") or [])[0]
        content = (choice.get("message") or {}).get("content") or ""
    except (IndexError, AttributeError, KeyError) as exc:
        raise UpstreamProviderError(
            "vsegpt: malformed response", details={"body": str(data)[:500]}
        ) from exc
    content = _strip_reasoning(content)

    usage = data.get("usage") or {}
    tokens_in = int(
        usage.get("prompt_tokens")
        or _approx_tokens("".join(_flatten_content(m.get("content", "")) for m in messages))
    )
    tokens_out = int(usage.get("completion_tokens") or _approx_tokens(content))
    # Cache-hit prompt tokens (DeepSeek automatic context caching / OpenAI-compat).
    # Surfaced so a caller can bill them at the cheaper cache-read rate
    # (pricing.calculate_cost_rub(cached_tokens=...)) and so the cache win on the
    # big stable system prefix is visible in logs.
    cache_hit_tokens = int(usage.get("prompt_cache_hit_tokens") or 0)
    if cache_hit_tokens:
        print(
            f"[VSEGPT] usage model={model} prompt={tokens_in} "
            f"cache_hit={cache_hit_tokens}",
            flush=True,
        )

    # Normalize to OpenAI shape with `model` = the Omnia id (not the vsegpt slug)
    # so chat.py bills against PRICE_TABLE. slug_to_omnia returns None for this
    # value and the caller falls back to req.model — same contract as yandex/sber.
    return {
        "id": data.get("id") or f"vsegpt-{uuid4()}",
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
