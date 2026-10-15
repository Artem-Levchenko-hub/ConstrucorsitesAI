"""Async клиент к LLM Gateway (OpenAI-compatible SSE) с MOCK-режимом."""

from __future__ import annotations

import asyncio
import contextvars
import json
import logging
from collections.abc import AsyncIterator
from typing import Any

import httpx

from yleum_api.core.config import get_settings

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


_generation_billing_context: contextvars.ContextVar[tuple[str, str, str] | None] = (
    contextvars.ContextVar("generation_billing_context", default=None)
)


def set_generation_billing_context(run_id: Any, user_id: Any, project_id: Any) -> None:
    _generation_billing_context.set((str(run_id), str(user_id), str(project_id)))


def _billing_metadata(user_id: str | None, project_id: str | None) -> dict[str, str]:
    context = _generation_billing_context.get()
    if context is None or project_id != context[2] or (user_id and user_id != context[1]):
        return {}
    return {"run_id": context[0], "user_id": context[1]}


def aggregate_pass_usage(*usages: dict[str, Any] | None) -> dict[str, Any]:
    """Sum tokens / cost across pass usages. None entries treated as zeros."""
    return {
        "tokens_in": sum(int((u or {}).get("tokens_in", 0)) for u in usages),
        "tokens_out": sum(int((u or {}).get("tokens_out", 0)) for u in usages),
        "cost_rub": sum(float((u or {}).get("cost_rub", 0.0)) for u in usages),
        "passes": len([u for u in usages if u is not None]),
    }


class LLMError(Exception):
    """Safe nonstream failure metadata; unknown POST outcomes are never replay-safe."""

    def __init__(
        self, message: str, *, code: str | None = None, http_status: int | None = None,
        upstream_http_status: int | None = None,
        provider_charge_ambiguous: bool | None = None, retry_safe: bool = False,
        provider_refusal_category: str | None = None,
        retry_after_seconds: int | None = None,
        finish_reason: object = None, requested_max_tokens: object = None,
        completion_tokens: object = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.http_status = http_status
        self.upstream_http_status = upstream_http_status
        self.provider_charge_ambiguous = provider_charge_ambiguous
        self.retry_safe = retry_safe
        self.provider_refusal_category = provider_refusal_category
        self.retry_after_seconds = retry_after_seconds
        self.finish_reason = (
            finish_reason if isinstance(finish_reason, str) and finish_reason in {
                "stop", "tool_calls", "length", "content_filter", "refusal", "pause_turn",
            } else "unknown" if finish_reason is not None else None
        )
        self.requested_max_tokens = (
            requested_max_tokens if type(requested_max_tokens) is int
            and 0 < requested_max_tokens < 2**63 else None
        )
        self.completion_tokens = (
            completion_tokens if type(completion_tokens) is int
            and 0 <= completion_tokens < 2**63 else None
        )


class LLMOutputTruncated(LLMError):
    """A confirmed response ended at its ceiling; its partial content is discarded."""

    def __init__(self, *, requested_max_tokens: object, completion_tokens: object) -> None:
        super().__init__(
            "Ответ модели достиг предела вывода; неприменённое действие нужно разбить.",
            code="provider_output_truncated", http_status=200, retry_safe=False,
            finish_reason="length", requested_max_tokens=requested_max_tokens,
            completion_tokens=completion_tokens,
        )


_GATEWAY_ERROR_CODES = {
    "model_unavailable", "model_not_found", "validation_failed", "wallet_empty",
    "billing_reconciliation_required",
    "provider_output_invalid", "provider_output_filtered",
}


def rate_limit_retry_delay(details: object) -> int | None:
    """Accept only the Gateway's post-journal, closed refusal policy contract."""
    if not isinstance(details, dict) or not (
        type(details.get("upstream_http_status")) is int
        and details["upstream_http_status"] == 429
        and details.get("provider_refusal_category") == "rate_limit"
        and details.get("provider_charge_ambiguous") is False
        and details.get("retry_safe") is True
        and details.get("retry_policy") == "llmgw_key_rate_limit_v1"
    ):
        return None
    delay = details.get("retry_after_seconds", 0)
    return delay if type(delay) is int and 0 <= delay <= 86_400 else None


def _gateway_failure(response: httpx.Response) -> LLMError:
    # Never forward provider messages, raw bodies, headers or URLs to the agent.
    error: dict[str, Any] = {}
    try:
        body = response.json()
    except (ValueError, RecursionError):
        body = None
    if isinstance(body, dict) and isinstance(body.get("detail"), dict):
        candidate = body["detail"].get("error")
        if isinstance(candidate, dict):
            error = candidate
    raw_code = error.get("code")
    code = raw_code if isinstance(raw_code, str) and raw_code in _GATEWAY_ERROR_CODES else None
    details = error.get("details")
    details = details if isinstance(details, dict) else {}
    raw_status = details.get("upstream_http_status")
    upstream = raw_status if type(raw_status) is int and 400 <= raw_status <= 599 else None
    raw_ambiguity = details.get("provider_charge_ambiguous")
    ambiguous = raw_ambiguity if type(raw_ambiguity) is bool else None
    category = details.get("provider_refusal_category")
    category = (
        category if isinstance(category, str) and category in {"access", "funds", "rate_limit"}
        and upstream is not None and 400 <= upstream < 500 else None
    )
    retry_safe = (
        response.status_code == 502 and code == "model_unavailable"
        and ambiguous is False and details.get("provider_failure_kind") == "preconnect"
        and "upstream_http_status" not in details
    )
    rate_delay = (
        rate_limit_retry_delay(details)
        if response.status_code == 502 and code == "model_unavailable"
        and len(response.content) <= 16_384
        and error.get("type", "model_unavailable") == "model_unavailable" else None
    )
    retry_safe = retry_safe or rate_delay is not None
    if category == "funds":
        message = "Недостаточно средств у LLM-провайдера."
    elif category == "access":
        message = "LLM-провайдер отклонил доступ к запросу."
    elif category == "rate_limit":
        message = "LLM-провайдер ограничил частоту запросов."
    elif upstream == 401:
        message = "Требуется проверка доступа и средств у LLM-провайдера (HTTP 401)."
    elif upstream == 403:
        message = "LLM-провайдер запретил доступ к запросу (HTTP 403)."
    elif upstream == 402:
        message = "LLM-провайдер отклонил запрос по оплате (HTTP 402)."
    elif upstream == 429 or (upstream is None and response.status_code == 429):
        message = "LLM-провайдер вернул HTTP 429; точная причина не установлена, требуется сверка."
    elif upstream is not None:
        message = f"LLM-провайдер завершил запрос ошибкой (HTTP {upstream})."
    elif code == "wallet_empty":
        message = "Недостаточно средств на балансе Yleum для этого запроса."
    elif code == "billing_reconciliation_required":
        message = "Результат запроса к LLM-провайдеру требует сверки."
    elif retry_safe:
        message = "Не удалось установить соединение с LLM-провайдером."
    else:
        message = (
            "Не получен подтверждённый результат LLM-запроса "
            f"(gateway HTTP {response.status_code})."
        )
    return LLMError(
        message, code=code, http_status=response.status_code, upstream_http_status=upstream,
        provider_charge_ambiguous=ambiguous, retry_safe=retry_safe,
        provider_refusal_category=category,
        retry_after_seconds=rate_delay,
    )


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
    - {"error": "..."} — ошибка (после неё — конец)
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
            **_billing_metadata(user_id, project_id),
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
    last_line_sample = ""
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            async with client.stream("POST", url, json=payload) as resp:
                print(f"[LLM] connected status={resp.status_code}", flush=True)
                if resp.status_code >= 400:
                    body = await resp.aread()
                    print(f"[LLM] http_error {resp.status_code} {body[:300]!r}", flush=True)
                    yield {"error": f"gateway {resp.status_code}: {body!r}"}
                    return
                async for line in resp.aiter_lines():
                    line_count += 1
                    if line_count <= 3 or line_count % 10 == 0:
                        last_line_sample = line[:120]
                        print(f"[LLM] line#{line_count}: {last_line_sample!r}", flush=True)
                    if not line.startswith("data:"):
                        continue
                    chunk = line[5:].strip()
                    if not chunk or chunk == "[DONE]":
                        continue
                    try:
                        data = json.loads(chunk)
                    except json.JSONDecodeError as exc:
                        print(f"[LLM] json_err chunk={chunk[:100]!r} err={exc}", flush=True)
                        continue
                    # Upstream errors arrive INSIDE the SSE stream as
                    # `data: {"error": {...}}` (vsegpt: out-of-budget / rate-limit
                    # / model-unavailable). Without this they looked like an empty
                    # response → the user saw a generic "timeout 90с". Surface a
                    # clean, actionable message instead and end the stream.
                    if isinstance(data, dict) and data.get("error"):
                        _e = data["error"]
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
                        print(f"[LLM] upstream_error {_raw[:200]!r}", flush=True)
                        yield {"error": _msg}
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
    except httpx.HTTPError as e:
        import traceback as _tb

        print(f"[LLM] transport_error err={e!r}\n{_tb.format_exc()}", flush=True)
        yield {"error": f"http: {e}"}
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
    max_tokens: int | None = 1024,
    temperature: float | None = 0.0,
    free: bool | None = None,
    timeout_seconds: float | None = None,
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
            **_billing_metadata(user_id, project_id),
            "free": _free_generation.get() if free is None else free,
            **({"stage": stage} if stage else {}),
        },
        **({"max_tokens": max_tokens} if max_tokens is not None else {}),
    }
    if temperature is not None:
        payload["temperature"] = temperature
    request_timeout = 90.0 if timeout_seconds is None else max(1.0, float(timeout_seconds))
    timeout = httpx.Timeout(
        request_timeout,
        connect=min(5.0, request_timeout),
        read=request_timeout,
    )
    try:
        request_headers = None
        if max_tokens is None and model in {"claude-sonnet-5.5", "anthropic/claude-sonnet-5.5"}:
            request_headers = {"x-yleum-provider-timeout-seconds": str(request_timeout)}
        response_budget = request_timeout if request_headers else None
        async with asyncio.timeout(response_budget), httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(url, json=payload, headers=request_headers)
            if resp.status_code >= 400:
                raise _gateway_failure(resp)
            try:
                data = resp.json()
            except ValueError:
                raise LLMError(
                    "Не удалось прочитать подтверждённый результат LLM-запроса.",
                    http_status=resp.status_code, provider_charge_ambiguous=True,
                ) from None
    except (httpx.HTTPError, TimeoutError) as exc:
        preconnect = isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout))
        raise LLMError(
            "Не удалось установить соединение с LLM-сервисом." if preconnect else
            "Не получен подтверждённый ответ LLM-сервиса; исход запроса требует сверки.",
            code="preconnect" if preconnect else "transport",
            provider_charge_ambiguous=not preconnect, retry_safe=preconnect,
        ) from None
    choices = data.get("choices") if isinstance(data, dict) else None
    choice = choices[0] if isinstance(choices, list) and choices else None
    reason = choice.get("finish_reason") if isinstance(choice, dict) else None
    usage = data.get("usage") if isinstance(data, dict) else None
    completion_tokens = usage.get("completion_tokens") if isinstance(usage, dict) else None
    if (
        resp.status_code == 200 and reason == "length" and isinstance(choice, dict)
        and isinstance(choice.get("message"), dict)
        and not choice["message"].get("refusal") and not data.get("error")
    ):
        raise LLMOutputTruncated(
            requested_max_tokens=max_tokens, completion_tokens=completion_tokens,
        )
    if not isinstance(reason, str) or reason not in {"stop", "tool_calls"}:
        raise LLMError(
            "LLM-провайдер не подтвердил завершение ответа; автоматический повтор остановлен.",
            code=(
                "provider_output_filtered"
                if reason == "content_filter" else "provider_output_invalid"
            ),
            retry_safe=False,
            finish_reason=reason, requested_max_tokens=max_tokens,
            completion_tokens=completion_tokens,
        )
    assert isinstance(choice, dict)
    message = choice.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    if not isinstance(message, dict) or (content is not None and not isinstance(content, str)):
        raise LLMError(
            "LLM-провайдер вернул неподтверждённый формат ответа.",
            code="provider_output_invalid", retry_safe=False,
        )
    return content or ""


async def _mock_stream(messages: list[dict[str, str]]) -> AsyncIterator[dict[str, Any]]:
    user_text = next(
        (m["content"] for m in reversed(messages) if m.get("role") == "user"),
        "пустой запрос",
    )[:200]
    chunks = [
        f"Готово. Сгенерировал сайт по запросу: «{user_text}»\n\n",
        '<file path="index.html">',
        '\n<!DOCTYPE html><html lang="ru"><head>',
        '<meta charset="utf-8"><title>Yleum mock</title>',
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
