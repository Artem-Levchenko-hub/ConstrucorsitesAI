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
import re
import time
from typing import Any
from uuid import uuid4

import httpx

from omnia_gateway.core.config import get_settings
from omnia_gateway.core.errors import UpstreamProviderError, ValidationFailedError

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
    # Opus 4.8 — the art_director (design-brain) model. proxyapi.ru ran dry and
    # the OpenRouter detour needed a separate key, so Opus rides vsegpt.ru on the
    # SAME funded key already used for the DeepSeek workers (VSEGPT_API_KEY).
    # Dispatched here (before the LiteLLM Router) so the dead openrouter/ slug is
    # never used. The brief is short prose, so the non-thinking variant is enough
    # and keeps the response clean (no <think> field to strip). Requires the
    # vsegpt plan to include Anthropic models — else vsegpt 400s "not available on
    # your subscription plan" and the writer carries the page alone (R-10).
    "claude-opus-4-8": "anthropic/claude-opus-4.8",
}

# Default ceiling for a thinking model: chain-of-thought shares the token budget
# with the visible answer, so a small cap silently truncates the real output.
_DEFAULT_MAX_TOKENS = 16384
# Below the API client's 120 s read timeout (apps/api llm_client) so we fail
# first and surface a clean error the caller can fall back on, rather than the
# client tearing down the socket mid-flight.
_DEFAULT_TIMEOUT_S = 115.0

# Strip a leaked chain-of-thought. vsegpt normally returns reasoning in a
# separate field, but some upstreams inline `<think>…</think>` in the content —
# which would break the PageIR JSON parse downstream.
_THINK_BLOCK = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)


def is_vsegpt_model(model_id: str) -> bool:
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


def _to_vsegpt_messages(messages: list[dict[str, Any]]) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for m in messages:
        role = m.get("role")
        if role not in ("system", "user", "assistant"):
            raise ValidationFailedError(f"unsupported role: {role}")
        out.append({"role": role, "content": _flatten_content(m.get("content", ""))})
    return out


def _approx_tokens(text: str) -> int:
    """~4 chars/token — coarse fallback when vsegpt omits usage."""
    return max(1, len(text) // 4)


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

    url = f"{settings.vsegpt_base_url.rstrip('/')}/chat/completions"
    payload: dict[str, Any] = {
        "model": slug,
        "messages": _to_vsegpt_messages(messages),
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
        },
    }
