"""SSE event-stream generator for chat completions.

Bills only for tokens actually delivered to the wire. If the client disconnects
mid-stream, the loop short-circuits and the bill reflects the partial output —
never the un-streamed tail (per AGENT-C-LLM-GATEWAY.md, M1 cancellation rule).
"""

from __future__ import annotations

import asyncio
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
from omnia_gateway.providers import sber as sber_provider
from omnia_gateway.providers import vsegpt as vsegpt_provider
from omnia_gateway.providers import yandex as yandex_provider
from omnia_gateway.services import billing, file_logger
from omnia_gateway.services import litellm_router as router_module
from omnia_gateway.services.pricing import calculate_cost_rub
from omnia_gateway.services.prompt_cache import apply_anthropic_cache
from omnia_gateway.services.token_counter import count_message_tokens, count_text_tokens

log = structlog.get_logger(__name__)


def _sse(payload: dict[str, Any] | str) -> str:
    """Serialize payload for sse_starlette.

    EventSourceResponse adds the `data: ` prefix and trailing blank line itself
    — we only need the JSON body (or the `[DONE]` sentinel).
    """
    if isinstance(payload, str):
        return payload
    return json.dumps(payload, ensure_ascii=False)


async def _custom_provider_pseudo_stream(
    provider_acompletion: Any,
    model: str,
    messages: list[dict[str, str]],
    temperature: float | None,
    max_tokens: int | None,
    default_temperature: float,
    default_max_tokens: int = 2000,
) -> AsyncIterator[tuple[str, str]]:
    """For providers without native streaming (Yandex, Sber, vsegpt): one blocking
    call → word chunks. ``default_max_tokens`` lets a thinking model (vsegpt
    DeepSeek) reserve enough budget that chain-of-thought can't truncate the
    visible answer — 2000 is fine for the non-reasoning RU models.

    sse_starlette sends `: ping` keep-alives (~15 s) on its own task while this
    awaits, so the single long call won't trip nginx/httpx read timeouts.
    """
    full = await provider_acompletion(
        model=model,
        messages=messages,
        temperature=default_temperature if temperature is None else temperature,
        max_tokens=default_max_tokens if max_tokens is None else max_tokens,
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
    # Wrap the system prompt in `cache_control: ephemeral` for Anthropic models.
    # Saves 50-90% of input tokens on the 2nd+ request inside the 5-min TTL.
    # No-op for non-Anthropic models.
    cached_messages = apply_anthropic_cache(model, messages)  # type: ignore[arg-type]
    kwargs: dict[str, Any] = {"model": model, "messages": cached_messages, "stream": True}
    if user_id is not None:
        kwargs["user"] = str(user_id)
    if temperature is not None:
        kwargs["temperature"] = temperature
    if max_tokens is not None:
        kwargs["max_tokens"] = max_tokens

    # Gemini 2.5 AND GPT-5 family are reasoning models — without max_tokens
    # and minimal reasoning budget, all output goes into reasoning_tokens
    # and the visible response is 0–2 tokens. Mirrors the same guard in
    # services.litellm_router.acompletion — keep both in sync. See that
    # function's comment for the full justification.
    if model.startswith("gemini-"):
        kwargs.setdefault("max_tokens", 16384)
        kwargs.setdefault("reasoning_effort", "disable")
    elif model in ("gpt-5", "gpt-5-nano"):
        kwargs.setdefault("max_tokens", 16384)
        kwargs.setdefault("reasoning_effort", "minimal")

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
    free: bool = False,
) -> AsyncIterator[str]:
    """Generate the SSE stream + bill at end.

    ``free=True`` routes the end-of-stream charge through ``billing.charge(free=True)``
    — Usage is logged with the real cost, but the wallet is not debited.
    """
    response_id = f"chatcmpl-{uuid4()}"
    created = int(time.time())

    accumulated: list[str] = []
    actual_model = model
    fallback_used = False
    cancelled = False
    upstream_error: GatewayError | None = None

    source: AsyncIterator[tuple[str, str]]
    if yandex_provider.is_yandex_model(model):
        source = _custom_provider_pseudo_stream(
            yandex_provider.acompletion, model, messages, temperature, max_tokens, 0.6
        )
    elif sber_provider.is_sber_model(model):
        source = _custom_provider_pseudo_stream(
            sber_provider.acompletion, model, messages, temperature, max_tokens, 0.7
        )
    elif vsegpt_provider.is_vsegpt_model(model):
        # Thinking model — give it a 16k visible-answer budget (default_max_tokens)
        # so the chain-of-thought can't eat the real output.
        source = _custom_provider_pseudo_stream(
            vsegpt_provider.acompletion, model, messages, temperature, max_tokens, 0.5, 16384
        )
    else:
        source = _litellm_stream(model, messages, user_id, temperature, max_tokens)

    try:
        try:
            async for delta, slug in source:
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
        except asyncio.CancelledError:
            cancelled = True
            raise

        # Normal completion: emit final usage chunk + [DONE].
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
                    "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
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
    finally:
        # Bookkeeping based on what actually went out to the wire — runs
        # regardless of cancellation / error / clean exit.
        output_text = "".join(accumulated)
        if output_text:
            tokens_in_final = count_message_tokens(actual_model, messages)
            tokens_out_final = count_text_tokens(actual_model, output_text)
        else:
            tokens_in_final, tokens_out_final = 0, 0
        try:
            cost_rub_final = (
                calculate_cost_rub(actual_model, tokens_in_final, tokens_out_final)
                if tokens_out_final
                else Decimal("0")
            )
        except Exception:
            cost_rub_final = Decimal("0")

        if user_id is not None and tokens_out_final > 0:
            try:
                await billing.charge(
                    user_id=user_id,
                    project_id=project_id,
                    message_id=message_id,
                    model_id=actual_model,
                    tokens_in=tokens_in_final,
                    tokens_out=tokens_out_final,
                    cost_rub=cost_rub_final,
                    description=f"Streamed completion via {actual_model}",
                    free=free,
                )
            except Exception:
                log.exception("stream.charge_failed", user_id=str(user_id), model=actual_model)

        try:
            file_logger.log_request(
                {
                    "user_id": user_id,
                    "project_id": project_id,
                    "message_id": message_id,
                    "model": actual_model,
                    "tokens_in": tokens_in_final,
                    "tokens_out": tokens_out_final,
                    "cost_rub": cost_rub_final,
                    "cache_hit": False,
                    "fallback_used": fallback_used,
                    "stream": True,
                    "cancelled": cancelled,
                    "error": upstream_error.code if upstream_error else None,
                }
            )
        except Exception:
            log.exception("stream.file_log_failed")
