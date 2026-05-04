"""SSE event-stream generator for chat completions.

Bills only for tokens actually delivered to the wire. If the client disconnects
mid-stream, the loop short-circuits and the bill reflects the partial output —
never the un-streamed tail (per AGENT-C-LLM-GATEWAY.md, M1 cancellation rule).
"""

from __future__ import annotations

import json
import time
from collections.abc import AsyncIterator
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import litellm
import structlog
from fastapi import Request

from omnia_gateway.core.errors import GatewayError, UpstreamProviderError
from omnia_gateway.providers import yandex as yandex_provider
from omnia_gateway.services import billing, file_logger
from omnia_gateway.services import litellm_router as router_module
from omnia_gateway.services.pricing import calculate_cost_rub
from omnia_gateway.services.token_counter import count_message_tokens, count_text_tokens

log = structlog.get_logger(__name__)


def _sse(payload: dict[str, Any] | str) -> str:
    if isinstance(payload, str):
        return f"data: {payload}\n\n"
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


async def _yandex_pseudo_stream(
    model: str,
    messages: list[dict[str, str]],
    temperature: float | None,
    max_tokens: int | None,
) -> AsyncIterator[tuple[str, str]]:
    """Yandex doesn't expose streaming via our wrapper; chunk the full response."""
    full = await yandex_provider.acompletion(
        model=model,
        messages=messages,
        temperature=0.6 if temperature is None else temperature,
        max_tokens=2000 if max_tokens is None else max_tokens,
    )
    text = full["choices"][0]["message"]["content"]
    # Coarse word-boundary chunking — better UX than char-by-char on a slow link.
    for token in text.split():
        yield token + " ", model


async def _litellm_stream(
    model: str,
    messages: list[dict[str, str]],
    user_id: UUID | None,
    temperature: float | None,
    max_tokens: int | None,
) -> AsyncIterator[tuple[str, str]]:
    kwargs: dict[str, Any] = {"model": model, "messages": messages, "stream": True}
    if user_id is not None:
        kwargs["user"] = str(user_id)
    if temperature is not None:
        kwargs["temperature"] = temperature
    if max_tokens is not None:
        kwargs["max_tokens"] = max_tokens

    router = router_module.get_router()
    try:
        stream = await router.acompletion(**kwargs)
        async for chunk in stream:
            slug = getattr(chunk, "model", "") or ""
            choices = getattr(chunk, "choices", None) or []
            delta_text = ""
            if choices:
                delta_obj = getattr(choices[0], "delta", None)
                if delta_obj is not None:
                    delta_text = getattr(delta_obj, "content", "") or ""
            yield delta_text, slug
    except litellm.AuthenticationError as exc:
        raise UpstreamProviderError(f"stream auth failure: {exc}") from exc
    except litellm.RateLimitError as exc:
        raise UpstreamProviderError(f"stream rate-limited: {exc}") from exc
    except (litellm.APIConnectionError, litellm.Timeout) as exc:
        raise UpstreamProviderError(f"stream upstream error: {exc}") from exc
    except litellm.APIError as exc:
        raise UpstreamProviderError(f"stream provider error: {exc}") from exc


async def stream_completion(
    *,
    request: Request,
    model: str,
    messages: list[dict[str, str]],
    user_id: UUID | None,
    project_id: UUID | None,
    message_id: UUID | None,
    temperature: float | None,
    max_tokens: int | None,
) -> AsyncIterator[str]:
    """Generate the SSE stream + bill at end."""
    response_id = f"chatcmpl-{uuid4()}"
    created = int(time.time())

    accumulated: list[str] = []
    actual_model = model
    fallback_used = False
    cancelled = False
    upstream_error: GatewayError | None = None

    source: AsyncIterator[tuple[str, str]]
    if yandex_provider.is_yandex_model(model):
        source = _yandex_pseudo_stream(model, messages, temperature, max_tokens)
    else:
        source = _litellm_stream(model, messages, user_id, temperature, max_tokens)

    try:
        async for delta, slug in source:
            if await request.is_disconnected():
                cancelled = True
                break
            if slug:
                mapped = router_module.slug_to_omnia(slug)
                if mapped and mapped != actual_model:
                    fallback_used = True
                    actual_model = mapped
            if delta:
                accumulated.append(delta)
                yield _sse(
                    {
                        "id": response_id,
                        "object": "chat.completion.chunk",
                        "created": created,
                        "model": actual_model,
                        "choices": [
                            {
                                "index": 0,
                                "delta": {"content": delta},
                                "finish_reason": None,
                            }
                        ],
                    }
                )
    except GatewayError as exc:
        upstream_error = exc

    output_text = "".join(accumulated)
    tokens_in = count_message_tokens(actual_model, messages) if output_text else 0
    tokens_out = count_text_tokens(actual_model, output_text) if output_text else 0
    try:
        cost_rub = (
            calculate_cost_rub(actual_model, tokens_in, tokens_out)
            if tokens_out
            else Decimal("0")
        )
    except Exception:
        cost_rub = Decimal("0")

    if not cancelled:
        if upstream_error is not None:
            yield _sse(
                {
                    "error": {
                        "code": upstream_error.code,
                        "message": upstream_error.message,
                    }
                }
            )
        else:
            yield _sse(
                {
                    "id": response_id,
                    "object": "chat.completion.chunk",
                    "created": created,
                    "model": actual_model,
                    "choices": [
                        {"index": 0, "delta": {}, "finish_reason": "stop"}
                    ],
                    "usage": {
                        "prompt_tokens": tokens_in,
                        "completion_tokens": tokens_out,
                        "total_tokens": tokens_in + tokens_out,
                    },
                    "metadata": {
                        "actual_model_used": actual_model,
                        "fallback_used": fallback_used,
                        "cost_rub": str(cost_rub),
                    },
                }
            )
        yield _sse("[DONE]")

    if user_id is not None and tokens_out > 0:
        try:
            await billing.charge(
                user_id=user_id,
                project_id=project_id,
                message_id=message_id,
                model_id=actual_model,
                tokens_in=tokens_in,
                tokens_out=tokens_out,
                cost_rub=cost_rub,
                description=f"Streamed completion via {actual_model}",
            )
        except Exception:
            log.exception("stream.charge_failed", user_id=str(user_id), model=actual_model)

    file_logger.log_request(
        {
            "user_id": user_id,
            "project_id": project_id,
            "message_id": message_id,
            "model": actual_model,
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
            "cost_rub": cost_rub,
            "cache_hit": False,
            "fallback_used": fallback_used,
            "stream": True,
            "cancelled": cancelled,
            "error": upstream_error.code if upstream_error else None,
        }
    )
