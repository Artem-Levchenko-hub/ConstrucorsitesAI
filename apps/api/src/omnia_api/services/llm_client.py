"""Async клиент к LLM Gateway (OpenAI-compatible SSE) с MOCK-режимом."""

from __future__ import annotations

import asyncio
import contextvars
import json
import logging
from collections.abc import AsyncIterator
from typing import Any

import httpx

from omnia_api.core.config import get_settings

log = logging.getLogger(__name__)


# Free-generation flag for the current generation, set by routers/messages.py
# before the pipeline runs. Threading a `free=` param through every generator
# (director_polish, multipass, _run_pass) would be noisy; a contextvar rides the
# same async task tree (asyncio.gather children inherit it). The gateway reads
# metadata.free to skip the wallet debit while still logging Usage.
_free_generation: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "omnia_free_generation", default=False
)


def set_free_generation(value: bool) -> None:
    """Mark the current async context as a free (non-billed) generation."""
    _free_generation.set(value)


class LLMError(Exception):
    def __init__(self, message: str, *, code: str = "llm_error") -> None:
        super().__init__(message)
        self.code = code


class PaidCallAmbiguousError(LLMError):
    def __init__(self, message: str = "paid call result is ambiguous") -> None:
        super().__init__(message, code="paid_call_ambiguous")


def _gateway_error_code(payload: object) -> str:
    if not isinstance(payload, dict):
        return "gateway_error"
    detail = payload.get("detail")
    if isinstance(detail, dict):
        payload = detail
    error = payload.get("error") if isinstance(payload, dict) else None
    if not isinstance(error, dict):
        return "gateway_error"
    return str(error.get("code") or error.get("type") or "gateway_error")


async def stream_chat_completion(
    messages: list[dict[str, str]],
    model: str,
    user_id: str,
    project_id: str,
    message_id: str,
) -> AsyncIterator[dict[str, Any]]:
    """Yields events:
    - {"delta": "..."} — текстовый чанк
    - {"usage": {"tokens_in", "tokens_out", "cost_rub"}} — финальная статистика
    - {"error": "...", "error_code": "..."} — ошибка (после неё — конец)
    """
    settings = get_settings()
    if settings.mock_llm:
        async for event in _mock_stream(messages):
            yield event
        return

    url = f"{settings.llm_gateway_url.rstrip('/')}/v1/chat/completions"
    payload = {
        "model": model,
        "messages": messages,
        "stream": True,
        "user": user_id,
        "metadata": {
            "project_id": project_id,
            "message_id": message_id,
            "free": _free_generation.get(),
        },
    }
    # read=300s: the art_director Opus pass (vsegpt, non-streaming) can take
    # ~150s+; the old 120s cut it off so the brief was dropped and the writer ran
    # alone. Headroom over the gateway/vsegpt 240s provider timeout so a real
    # upstream failure surfaces as an error, not a client-side socket teardown.
    timeout = httpx.Timeout(300.0, connect=5.0, read=300.0)
    print(f"[LLM] start url={url} model={model} msgs={len(messages)}", flush=True)
    line_count = 0
    delta_count = 0
    usage_seen = False
    done_seen = False
    last_line_sample = ""
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            async with client.stream("POST", url, json=payload) as resp:
                print(f"[LLM] connected status={resp.status_code}", flush=True)
                if resp.status_code >= 400:
                    body = await resp.aread()
                    try:
                        error_code = _gateway_error_code(json.loads(body))
                    except (json.JSONDecodeError, UnicodeDecodeError):
                        error_code = "gateway_error"
                    if error_code == "gateway_error" and (
                        resp.status_code in {408, 425, 429} or resp.status_code >= 500
                    ):
                        error_code = "paid_call_ambiguous"
                    print(
                        f"[LLM] http_error status={resp.status_code} code={error_code}",
                        flush=True,
                    )
                    yield {
                        "error": f"LLM gateway rejected the request (HTTP {resp.status_code})",
                        "error_code": error_code,
                    }
                    return
                async for line in resp.aiter_lines():
                    line_count += 1
                    if line_count <= 3 or line_count % 10 == 0:
                        last_line_sample = line[:120]
                        print(f"[LLM] line#{line_count}: {last_line_sample!r}", flush=True)
                    if not line.startswith("data:"):
                        continue
                    chunk = line[5:].strip()
                    if not chunk:
                        continue
                    if chunk == "[DONE]":
                        done_seen = True
                        break
                    try:
                        data = json.loads(chunk)
                    except json.JSONDecodeError as exc:
                        print(f"[LLM] json_err chunk={chunk[:100]!r} err={exc}", flush=True)
                        yield {
                            "error": (
                                "Ответ платного вызова повреждён; автоматический повтор отключён."
                            ),
                            "error_code": "paid_call_ambiguous",
                        }
                        return
                    if not isinstance(data, dict):
                        yield {
                            "error": (
                                "Ответ платного вызова имеет неверный формат; "
                                "автоматический повтор отключён."
                            ),
                            "error_code": "paid_call_ambiguous",
                        }
                        return
                    # Upstream errors arrive INSIDE the SSE stream as
                    # `data: {"error": {...}}` (vsegpt: out-of-budget / rate-limit
                    # / model-unavailable). Without this they looked like an empty
                    # response → the user saw a generic "timeout 90с". Surface a
                    # clean, actionable message instead and end the stream.
                    if isinstance(data, dict) and data.get("error"):
                        _e = data["error"]
                        _code = (
                            str(_e.get("code") or _e.get("type") or "upstream_error")
                            if isinstance(_e, dict)
                            else "upstream_error"
                        )
                        _raw = (
                            _e.get("message") if isinstance(_e, dict) else str(_e)
                        ) or "upstream error"
                        _low = _raw.lower()
                        if any(k in _low for k in ("budget", "balance", "money", "out of")):
                            _msg = (
                                "Недостаточно средств на балансе LLM-провайдера — "
                                "пополни баланс и попробуй снова."
                            )
                        elif "rate" in _low or "429" in _low:
                            _msg = (
                                "Провайдер перегружен (rate-limit) — попробуй ещё раз через минуту."
                            )
                        elif any(
                            k in _low
                            for k in ("unavailable", "501", "unknown main", "not available")
                        ):
                            _msg = (
                                "Модель временно недоступна у провайдера — попробуй "
                                "другую модель или позже."
                            )
                        else:
                            _msg = f"Ошибка LLM-провайдера: {_raw[:200]}"
                        print(f"[LLM] upstream_error code={_code}", flush=True)
                        yield {"error": _msg, "error_code": _code}
                        return
                    delta = data.get("choices", [{}])[0].get("delta", {}).get("content")
                    if delta:
                        delta_count += 1
                        yield {"delta": delta}
                    usage = data.get("usage")
                    if usage:
                        usage_seen = True
                        meta = data.get("metadata") or {}
                        cost_rub = (
                            float(meta.get("cost_rub", 0))
                            if meta.get("cost_rub")
                            else float(usage.get("cost_rub", 0))
                        )
                        yield {
                            "usage": {
                                "tokens_in": usage.get("prompt_tokens", 0),
                                "tokens_out": usage.get("completion_tokens", 0),
                                "cost_rub": cost_rub,
                            }
                        }
                if not done_seen:
                    yield {
                        "error": (
                            "Ответ платного вызова оборвался; автоматический повтор отключён."
                        ),
                        "error_code": "paid_call_ambiguous",
                    }
                    return
    except (
        httpx.ReadTimeout,
        httpx.WriteTimeout,
        httpx.ReadError,
        httpx.WriteError,
        httpx.RemoteProtocolError,
    ) as e:
        import traceback as _tb

        print(f"[LLM] transport_error err={e!r}\n{_tb.format_exc()}", flush=True)
        yield {
            "error": "Ответ платного вызова потерян; автоматический повтор отключён.",
            "error_code": "paid_call_ambiguous",
        }
    except httpx.HTTPError as e:
        print(f"[LLM] connection_error type={type(e).__name__}", flush=True)
        yield {"error": "LLM gateway is unavailable", "error_code": "gateway_transport"}
    print(
        f"[LLM] done lines={line_count} deltas={delta_count} "
        f"usage_seen={usage_seen} last={last_line_sample!r}",
        flush=True,
    )


async def complete_chat(
    messages: list[dict[str, Any]],
    model: str,
    *,
    user_id: str | None = None,
    project_id: str | None = None,
    stage: str | None = None,
    max_tokens: int = 1024,
    temperature: float | None = 0.0,
) -> str:
    """Non-streaming completion → assistant text (``""`` on mock/empty).

    The acceptance gate's vision audit needs a single structured verdict, not
    a token stream — so it calls this instead of `stream_chat_completion`.
    `messages` may carry multimodal content (a `content` that is a list of
    text/image_url blocks); the gateway forwards it to a vision model.

    Mock mode returns ``""`` so callers skip cleanly (no fabricated verdicts).
    Raises `LLMError` on a gateway 4xx/5xx so the caller can fail-soft.
    """
    settings = get_settings()
    if settings.mock_llm:
        return ""

    url = f"{settings.llm_gateway_url.rstrip('/')}/v1/chat/completions"
    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "stream": False,
        "user": user_id,
        "metadata": {
            "project_id": project_id,
            "free": _free_generation.get(),
            **({"stage": stage} if stage else {}),
        },
        "max_tokens": max_tokens,
    }
    if temperature is not None:
        payload["temperature"] = temperature
    timeout = httpx.Timeout(90.0, connect=5.0, read=90.0)
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(url, json=payload)
            if resp.status_code >= 400:
                body = await resp.aread()
                try:
                    error_code = _gateway_error_code(json.loads(body))
                except (json.JSONDecodeError, UnicodeDecodeError):
                    error_code = "gateway_error"
                if error_code == "gateway_error" and (
                    resp.status_code in {408, 425, 429} or resp.status_code >= 500
                ):
                    error_code = "paid_call_ambiguous"
                if error_code == "paid_call_ambiguous":
                    raise PaidCallAmbiguousError()
                raise LLMError(
                    f"gateway rejected the request (HTTP {resp.status_code})",
                    code=error_code,
                )
            try:
                data = resp.json()
            except ValueError as exc:
                raise PaidCallAmbiguousError(
                    "gateway returned malformed JSON after a paid call"
                ) from exc
            if not isinstance(data, dict):
                raise PaidCallAmbiguousError(
                    "gateway returned a non-object response after a paid call"
                )
    except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout) as exc:
        raise LLMError("gateway connection failed", code="gateway_transport") from exc
    except httpx.HTTPError as exc:
        raise PaidCallAmbiguousError(
            f"gateway response status is unknown after {type(exc).__name__}"
        ) from exc
    try:
        choices = data["choices"]
        content = choices[0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise PaidCallAmbiguousError(
            "gateway returned an incomplete response after a paid call"
        ) from exc
    if not isinstance(content, str):
        raise PaidCallAmbiguousError("gateway returned invalid content after a paid call")
    return content


async def _mock_stream(messages: list[dict[str, str]]) -> AsyncIterator[dict[str, Any]]:
    user_text = next(
        (m["content"] for m in reversed(messages) if m.get("role") == "user"),
        "пустой запрос",
    )[:200]
    chunks = [
        f"Готово. Сгенерировал сайт по запросу: «{user_text}»\n\n",
        '<file path="index.html">',
        '\n<!DOCTYPE html><html lang="ru"><head>',
        '<meta charset="utf-8"><title>Omnia mock</title>',
        '\n<script src="https://cdn.tailwindcss.com"></script>',
        '\n</head><body class="bg-slate-50 p-12 font-sans">',
        '\n  <h1 class="text-4xl font-bold mb-4">Сайт по запросу</h1>',
        f'\n  <p class="text-slate-700 max-w-2xl">{user_text}</p>',
        "\n</body></html>\n",
        "</file>\n",
    ]
    for c in chunks:
        await asyncio.sleep(0.04)
        yield {"delta": c}
    yield {"usage": {"tokens_in": 200, "tokens_out": 350, "cost_rub": 0.5}}
