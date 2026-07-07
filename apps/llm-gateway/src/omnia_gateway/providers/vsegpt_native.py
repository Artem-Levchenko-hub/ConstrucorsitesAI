"""Native Anthropic Messages → vsegpt chat/completions adapter (Opus 4.8).

vsegpt.ru exposes NO native ``/v1/messages`` endpoint (probed 2026-07-01: 404,
«Available endpoints: 'v1/chat/completions', …»), but its OpenAI surface DOES
support tool-calling for ``anthropic/claude-opus-4.8`` (probed live: ``tool_calls``
returned in ~3s). This module lets the native tool-use agent (apps/api
``agent_native.py``) keep speaking raw Anthropic Messages while the upstream is
vsegpt: request blocks are converted Anthropic→OpenAI, the response is converted
back OpenAI→Anthropic, byte-compatible with what the agent loop consumes
(``content`` blocks, ``stop_reason``, ``usage``).

Why this is SAFE without thinking-signature plumbing: the whole reason the native
passthrough existed was oneprovider's forced extended thinking — signatures had to
survive round-trips or Anthropic 400s. vsegpt sends no ``thinking`` param → Opus
runs with thinking OFF → responses carry no thinking blocks → nothing to echo.
The agent's verbatim-echo of assistant turns keeps working (there is simply
nothing to preserve), and calls drop from ~71s to ~3s (measured).

Transport mirrors ``providers/vsegpt.py``: sync ``httpx.Client`` on a worker
thread, ``trust_env=False`` + no-op mounts (the UK Gemini egress proxy must never
tunnel a RU endpoint; AsyncClient TLS-stalls in the long-lived uvicorn loop).

Upstream HTTP errors are returned as ``(status, anthropic-error-body)`` — the
agent's 429 retry loop keys off the status code, so a vsegpt rate-limit (~1 req/s)
propagates verbatim instead of being masked as a 502.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any
from uuid import uuid4

import httpx
import structlog

from omnia_gateway.core.config import get_settings
from omnia_gateway.providers.vsegpt import (
    _TRANSIENT,
    _VSEGPT_MODEL_SLUG,
    _strip_reasoning,
)

log = structlog.get_logger(__name__)

_TIMEOUT_S = 240.0


# ── Request: Anthropic Messages → OpenAI chat.completions ──────────────────


def _system_text(system: Any) -> str:
    """Anthropic ``system`` is a string or a list of text blocks (e.g. with
    cache_control). Flatten to plain text for the OpenAI system message."""
    if isinstance(system, str):
        return system
    if isinstance(system, list):
        return "\n".join(
            str(b.get("text", ""))
            for b in system
            if isinstance(b, dict) and b.get("type") == "text"
        )
    return str(system or "")


def _tool_result_text(content: Any) -> str:
    """tool_result ``content`` is a string or a list of text blocks."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            str(b.get("text", ""))
            for b in content
            if isinstance(b, dict) and b.get("type") == "text"
        )
    return str(content or "")


def _assistant_msg(blocks: list[dict[str, Any]]) -> dict[str, Any]:
    """Assistant turn: text blocks → content, tool_use → tool_calls, thinking
    blocks DROPPED (vsegpt runs Opus with thinking off — none come back, and any
    historical ones must not leak into an OpenAI payload)."""
    texts: list[str] = []
    tool_calls: list[dict[str, Any]] = []
    for b in blocks:
        kind = b.get("type")
        if kind == "text":
            texts.append(str(b.get("text", "")))
        elif kind == "tool_use":
            tool_calls.append(
                {
                    "id": str(b.get("id", "")) or f"call_{uuid4().hex[:12]}",
                    "type": "function",
                    "function": {
                        "name": str(b.get("name", "")),
                        "arguments": json.dumps(
                            b.get("input") or {}, ensure_ascii=False
                        ),
                    },
                }
            )
    msg: dict[str, Any] = {
        "role": "assistant",
        # "" (not None): some OpenAI-compat aggregators reject null content.
        "content": "\n".join(t for t in texts if t),
    }
    if tool_calls:
        msg["tool_calls"] = tool_calls
    return msg


def _user_msgs(blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """User turn: tool_result blocks → role:"tool" messages (must directly follow
    the assistant tool_calls turn), remaining text/image blocks → one user message.
    Images ride the OpenAI ``image_url`` shape (Opus via vsegpt is multimodal)."""
    out: list[dict[str, Any]] = []
    parts: list[dict[str, Any]] = []
    for b in blocks:
        kind = b.get("type")
        if kind == "tool_result":
            text = _tool_result_text(b.get("content"))
            if b.get("is_error"):
                text = f"[TOOL ERROR] {text}"
            out.append(
                {
                    "role": "tool",
                    "tool_call_id": str(b.get("tool_use_id", "")),
                    "content": text,
                }
            )
        elif kind == "text":
            parts.append({"type": "text", "text": str(b.get("text", ""))})
        elif kind == "image":
            src = b.get("source") or {}
            if src.get("type") == "base64":
                media = src.get("media_type", "image/png")
                parts.append(
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:{media};base64,{src.get('data', '')}"},
                    }
                )
    if parts:
        if all(p["type"] == "text" for p in parts):
            out.append(
                {"role": "user", "content": "\n".join(p["text"] for p in parts)}
            )
        else:
            out.append({"role": "user", "content": parts})
    return out


def to_openai_payload(body: dict[str, Any]) -> dict[str, Any]:
    """Convert a raw Anthropic Messages request body to a vsegpt chat/completions
    payload. ``thinking`` is dropped by design (that's the latency win)."""
    slug = _VSEGPT_MODEL_SLUG[str(body.get("model", ""))]
    msgs: list[dict[str, Any]] = []
    system = body.get("system")
    if system:
        msgs.append({"role": "system", "content": _system_text(system)})
    for m in body.get("messages") or []:
        role = m.get("role")
        content = m.get("content")
        if isinstance(content, str):
            msgs.append({"role": role, "content": content})
        elif isinstance(content, list):
            if role == "assistant":
                msgs.append(_assistant_msg(content))
            else:
                msgs.extend(_user_msgs(content))

    payload: dict[str, Any] = {
        "model": slug,
        "messages": msgs,
        "max_tokens": int(body.get("max_tokens") or 8192),
        "stream": False,
    }
    if "temperature" in body:
        payload["temperature"] = body["temperature"]

    tools = body.get("tools")
    if tools:
        payload["tools"] = [
            {
                "type": "function",
                "function": {
                    "name": t.get("name", ""),
                    "description": t.get("description", ""),
                    "parameters": t.get("input_schema")
                    or {"type": "object", "properties": {}},
                },
            }
            for t in tools
        ]
        tc = body.get("tool_choice") or {}
        tc_type = tc.get("type")
        if tc_type == "any":
            payload["tool_choice"] = "required"
        elif tc_type == "tool":
            payload["tool_choice"] = {
                "type": "function",
                "function": {"name": tc.get("name", "")},
            }
        else:  # "auto" / absent
            payload["tool_choice"] = "auto"
    return payload


# ── Response: OpenAI chat.completions → Anthropic Messages ─────────────────

_STOP_REASON = {"tool_calls": "tool_use", "stop": "end_turn", "length": "max_tokens"}


def _parse_tool_arguments(raw: Any) -> dict[str, Any]:
    """Parse an OpenAI ``tool_call.function.arguments`` value into an input dict.

    Normally ``arguments`` is a single JSON-object string. But oneprovider's
    Anthropic→OpenAI translation concatenates the streamed ``input_json_delta``
    fragments verbatim, prepending the empty ``content_block_start`` input — so a
    real call arrives as ``{}{"path": "src/app/page.tsx"}`` (two objects
    back-to-back; verified live 2026-07-07 across single-arg, multi-arg AND
    no-arg tools — the leading ``{}`` is ALWAYS present). A plain ``json.loads``
    rejects that ("Extra data") and the old code degraded the WHOLE call to
    ``{}`` → the native agent looped forever calling read-file with an empty path
    (``403 unsafe path: ''``).

    Robust strategy: ``raw_decode`` each top-level object in sequence and merge
    them left-to-right (later keys win). ``{}{"path": …}`` collapses to
    ``{"path": …}``; a compliant single object is unchanged; a lone ``{}`` stays
    ``{}``. Also accepts an already-parsed dict (some relays pre-decode).
    """
    if isinstance(raw, dict):
        return raw
    s = str(raw or "").strip()
    if not s:
        return {}
    # Fast path: one clean object (the compliant-provider case).
    try:
        obj = json.loads(s)
        return obj if isinstance(obj, dict) else {}
    except ValueError:
        pass
    # Concatenated objects (oneprovider): decode each, merge left-to-right.
    decoder = json.JSONDecoder()
    merged: dict[str, Any] = {}
    idx, n, found = 0, len(s), False
    while idx < n:
        while idx < n and s[idx].isspace():
            idx += 1
        if idx >= n:
            break
        try:
            obj, idx = decoder.raw_decode(s, idx)
        except ValueError:
            break
        found = True
        if isinstance(obj, dict):
            merged.update(obj)
    return merged if found else {}


def to_anthropic_response(data: dict[str, Any], model: str) -> dict[str, Any]:
    """Convert a vsegpt (OpenAI-shape) completion to the Anthropic Messages shape
    the native agent consumes: ``content`` blocks + ``stop_reason`` + ``usage``."""
    choice = (data.get("choices") or [{}])[0]
    msg = choice.get("message") or {}

    content: list[dict[str, Any]] = []
    text = msg.get("content") or ""
    if text:
        text = _strip_reasoning(str(text))
        if text.strip():
            content.append({"type": "text", "text": text})
    for tc in msg.get("tool_calls") or []:
        fn = tc.get("function") or {}
        raw_args = fn.get("arguments")
        args = _parse_tool_arguments(raw_args)
        if not args and str(raw_args or "").strip() not in ("", "{}"):
            # Truly unparseable (not just the empty-object cases): surface it —
            # the agent's tool executor reports the miss and the loop self-corrects.
            log.warning("vsegpt_native.bad_tool_args", raw=str(raw_args)[:200])
        content.append(
            {
                "type": "tool_use",
                "id": str(tc.get("id", "")) or f"toolu_{uuid4().hex[:12]}",
                "name": str(fn.get("name", "")),
                "input": args,
            }
        )

    finish = choice.get("finish_reason") or "stop"
    usage = data.get("usage") or {}
    return {
        "id": str(data.get("id", "")) or f"msg_vsegpt_{uuid4().hex[:12]}",
        "type": "message",
        "role": "assistant",
        "model": model,
        "content": content,
        "stop_reason": _STOP_REASON.get(finish, "end_turn"),
        "stop_sequence": None,
        "usage": {
            "input_tokens": int(usage.get("prompt_tokens") or 0),
            "output_tokens": int(usage.get("completion_tokens") or 0),
        },
    }


# ── Transport ───────────────────────────────────────────────────────────────


async def anative_messages(body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    """One native-agent turn via vsegpt. Returns ``(status, anthropic_body)``.

    Upstream 4xx/5xx come back with the ORIGINAL status (the agent retries 429
    itself); transport faults raise ``httpx.HTTPError`` after one retry — the
    router maps that to a 502.
    """
    settings = get_settings()
    payload = to_openai_payload(body)
    url = f"{settings.vsegpt_base_url.rstrip('/')}/chat/completions"
    headers = {
        "Authorization": f"Bearer {settings.vsegpt_api_key.get_secret_value()}",  # type: ignore[union-attr]
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    def _post() -> httpx.Response:
        last: Exception | None = None
        for attempt in range(2):
            try:
                with httpx.Client(
                    timeout=httpx.Timeout(_TIMEOUT_S, connect=30.0),
                    trust_env=False,
                    mounts={"all://": httpx.HTTPTransport()},
                ) as client:
                    return client.post(url, json=payload, headers=headers)
            except _TRANSIENT as exc:
                last = exc
                if attempt == 0:
                    time.sleep(0.5)
                    continue
                raise
        raise last  # type: ignore[misc]  # unreachable — loop returns or raises

    upstream = await asyncio.to_thread(_post)
    if upstream.status_code >= 400:
        snippet = upstream.text[:300]
        log.warning(
            "vsegpt_native.upstream_error",
            status=upstream.status_code,
            body=snippet,
        )
        return upstream.status_code, {
            "type": "error",
            "error": {
                "type": "rate_limit_error" if upstream.status_code == 429 else "api_error",
                "message": f"vsegpt HTTP {upstream.status_code}: {snippet}",
            },
        }

    try:
        data = upstream.json()
    except ValueError:
        return 502, {
            "type": "error",
            "error": {"type": "api_error", "message": "vsegpt: non-JSON response"},
        }
    return 200, to_anthropic_response(data, str(body.get("model", "")))
