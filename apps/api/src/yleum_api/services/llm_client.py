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
from yleum_api.services.generation.provider_identity import GenerationProviderIdentity

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
    from yleum_api.services.generation.provider_attempts import prepare_generation_attempt

    deadline = asyncio.get_running_loop().time() + 300.0
    identity = await prepare_generation_attempt(payload, "/v1/chat/completions")
    try:
        async for data in _chat_stream_data(payload, url, identity, deadline):
            if data.get("error"):
                yield {"error": _stream_error_message(data["error"])}
                return
            choices = data.get("choices") or [{}]
            delta = choices[0].get("delta", {}).get("content")
            if delta:
                yield {"delta": delta}
            usage = data.get("usage")
            if usage:
                meta = data.get("metadata") or {}
                cost_rub = float(meta.get("cost_rub") or usage.get("cost_rub", 0))
                yield {
                    "usage": {
                        "tokens_in": usage.get("prompt_tokens", 0),
                        "tokens_out": usage.get("completion_tokens", 0),
                        "cost_rub": cost_rub,
                    }
                }
    except LLMError as exc:
        yield {"error": str(exc)}
    except (httpx.HTTPError, TimeoutError) as exc:
        # Transport logs must not include request bodies/headers or private URLs.
        log.warning("LLM stream transport failed: %s", type(exc).__name__)
        yield {
            "error": f"http: {exc}"
            if identity is None
            else "LLM stream outcome could not be confirmed"
        }


def _stream_error_message(error: object) -> str:
    raw = (error.get("message") if isinstance(error, dict) else str(error)) or "upstream error"
    low = raw.lower()
    if any(key in low for key in ("budget", "balance", "money", "out of")):
        return "Недостаточно средств на балансе LLM-провайдера — пополни баланс и попробуй снова."
    if "rate" in low or "429" in low:
        return "Провайдер перегружен (rate-limit) — попробуй ещё раз через минуту."
    if any(key in low for key in ("unavailable", "501", "unknown main", "not available")):
        return "Модель временно недоступна у провайдера — попробуй другую модель или позже."
    return f"Ошибка LLM-провайдера: {raw[:200]}"


async def _chat_stream_data(
    payload: dict[str, Any],
    url: str,
    identity: GenerationProviderIdentity | None,
    deadline: float,
) -> AsyncIterator[dict[str, Any]]:
    from yleum_api.services.generation.provider_attempts import (
        attach_identity,
        prepare_generation_attempt,
    )

    allowance = _chat_attempt_allowance(payload) if identity is not None else 1
    outward_event = False
    accepted_headers = False
    async with asyncio.timeout_at(deadline if identity is not None else None):
        for attempt in range(allowance):
            remaining = (
                deadline - asyncio.get_running_loop().time() if identity is not None else 300.0
            )
            if remaining <= 0:
                raise TimeoutError
            attach_identity(payload, identity)
            retry_parent = False
            try:
                timeout = httpx.Timeout(remaining, connect=min(5.0, remaining), read=remaining)
                async with httpx.AsyncClient(timeout=timeout) as client:
                    async with client.stream(
                        "POST",
                        url,
                        json=payload,
                        headers=(
                            {"x-yleum-provider-timeout-seconds": str(remaining)}
                            if identity is not None
                            else None
                        ),
                    ) as response:
                        accepted_headers = True
                        if response.status_code >= 400:
                            await response.aread()
                            if (
                                identity is not None
                                and attempt + 1 < allowance
                                and _response_preconnect(response)
                            ):
                                retry_parent = True
                            else:
                                if identity is None:
                                    raise LLMError(
                                        f"gateway {response.status_code}: {response.content!r}"
                                    )
                                raise _gateway_failure(response)
                        else:
                            async for line in response.aiter_lines():
                                if not line.startswith("data:"):
                                    continue
                                chunk = line[5:].strip()
                                if not chunk or chunk == "[DONE]":
                                    continue
                                try:
                                    data = json.loads(chunk)
                                except json.JSONDecodeError:
                                    outward_event = (
                                        True  # Unknown partial delivery never buys retry.
                                    )
                                    continue
                                if not isinstance(data, dict):
                                    outward_event = True
                                    continue
                                if (
                                    identity is not None
                                    and not outward_event
                                    and attempt + 1 < allowance
                                    and _committed_preconnect(data)
                                ):
                                    retry_parent = True
                                    break
                                # Role/usage events count even if the UI mapper yields no text.
                                outward_event = True
                                yield data
                                if data.get("error"):
                                    return
                            if not retry_parent:
                                return
            except (httpx.ConnectError, httpx.ConnectTimeout):
                if (
                    outward_event or (identity is not None and accepted_headers)
                    or attempt + 1 >= allowance
                ):
                    raise
            await asyncio.sleep(0.5)
            if retry_parent:
                identity = await prepare_generation_attempt(
                    payload, "/v1/chat/completions", parent=identity
                )
                accepted_headers = False  # A verified closed parent permits a fresh attempt.


def _chat_attempt_allowance(payload: dict[str, Any]) -> int:
    # Mirror only known existing llmgw adapter branches. Unknown models cannot
    # acquire a retry allowance by claiming an arbitrary metadata value.
    known = {
        "gemini-3.1-pro-preview-customtools",
        "claude-sonnet-5",
        "claude-sonnet-5.5",
        "~anthropic/claude-sonnet-latest",
    }
    model = payload.get("model")
    if model not in known:
        return 1
    if (
        not payload.get("stream")
        and model == "claude-sonnet-5.5"
        and (payload.get("max_tokens") is None)
    ):
        return 1  # collect_response never had the sync adapter's second attempt.
    return 2


def _committed_preconnect(data: object) -> bool:
    if not isinstance(data, dict):
        return False
    error = data.get("error")
    if not isinstance(error, dict) or error.get("code") != "model_unavailable":
        return False
    details = error.get("details")
    return isinstance(details, dict) and (
        details.get("provider_failure_kind") == "preconnect"
        and details.get("provider_charge_ambiguous") is False
        and details.get("retry_safe") is True
        and details.get("retry_policy") == "llmgw_preconnect_v1"
        and "upstream_http_status" not in details
    )


def _response_preconnect(response: httpx.Response) -> bool:
    if response.status_code != 502 or len(response.content) > 16_384 or response.history:
        return False
    try:
        body = response.json()
    except (ValueError, RecursionError):
        return False
    return isinstance(body, dict) and _committed_preconnect(body.get("detail"))


async def _chat_post(payload: dict[str, Any], url: str, budget: float) -> httpx.Response:
    from yleum_api.services.generation.provider_attempts import (
        attach_identity,
        prepare_generation_attempt,
    )

    loop = asyncio.get_running_loop()
    deadline = loop.time() + budget
    identity = await prepare_generation_attempt(payload, "/v1/chat/completions")
    allowance = _chat_attempt_allowance(payload) if identity is not None else 1
    accepted_headers = False
    omitted_default = payload.get("max_tokens") is None and payload["model"] in {
        "claude-sonnet-5.5",
        "anthropic/claude-sonnet-5.5",
    }
    try:
        # Preserve legacy per-I/O timing; the new two-cycle path shares one total
        # deadline, including intent SQL, the existing backoff and second intent.
        async with asyncio.timeout_at(
            deadline if identity is not None or omitted_default else None
        ):
            for attempt in range(allowance):
                remaining = deadline - loop.time() if identity is not None else budget
                if remaining <= 0:
                    raise TimeoutError
                attach_identity(payload, identity)
                timeout = httpx.Timeout(remaining, connect=min(5.0, remaining), read=remaining)
                try:
                    async with httpx.AsyncClient(timeout=timeout) as client:
                        headers = (
                            {"x-yleum-provider-timeout-seconds": str(remaining)}
                            if omitted_default or identity is not None
                            else None
                        )
                        if identity is None:
                            response = await client.post(url, json=payload, headers=headers)
                        else:
                            # Receiving headers proves this is no longer connection
                            # establishment. A body fault must stay ambiguous for the
                            # outer agent as well as this bounded transport cycle.
                            async with client.stream(
                                "POST", url, json=payload, headers=headers
                            ) as response:
                                accepted_headers = True
                                await response.aread()
                except (httpx.ConnectError, httpx.ConnectTimeout):
                    if accepted_headers or attempt + 1 >= allowance:
                        raise
                    await asyncio.sleep(0.5)
                    continue  # API-to-gateway transport: SAME committed identity.
                if response.status_code < 400:
                    return response
                if (
                    identity is not None
                    and attempt + 1 < allowance
                    and _response_preconnect(response)
                ):
                    await asyncio.sleep(0.5)
                    identity = await prepare_generation_attempt(
                        payload,
                        "/v1/chat/completions",
                        parent=identity,
                    )  # SQL independently verifies the linked committed receipt.
                    accepted_headers = False
                    continue
                raise _gateway_failure(response)
    except (httpx.HTTPError, TimeoutError) as exc:
        preconnect = isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout)) and (
            identity is None or not accepted_headers
        )
        raise LLMError(
            "Не удалось установить соединение с LLM-сервисом."
            if preconnect
            else "Не получен подтверждённый ответ LLM-сервиса; исход запроса требует сверки.",
            code="preconnect" if preconnect else "transport",
            provider_charge_ambiguous=not preconnect,
            retry_safe=preconnect,
        ) from None
    raise AssertionError("chat attempt cycle exhausted without a result")


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
    resp = await _chat_post(payload, url, request_timeout)
    try:
        data = resp.json()
    except ValueError:
        raise LLMError(
            "Не удалось прочитать подтверждённый результат LLM-запроса.",
            http_status=resp.status_code,
            provider_charge_ambiguous=True,
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
