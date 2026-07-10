"""SSE event-stream generator for chat completions.

Bills only for tokens actually delivered to the wire. If the client disconnects
mid-stream, the loop short-circuits and the bill reflects the partial output —
never the un-streamed tail (per AGENT-C-LLM-GATEWAY.md, M1 cancellation rule).

There is exactly one chat model (`claude-opus-4-8`) and one upstream
(oneprovider), so the stream source is always `providers/oneprovider.astream`.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import structlog
from fastapi import Request

from omnia_gateway.core.errors import GatewayError
from omnia_gateway.providers import oneprovider
from omnia_gateway.services import billing, file_logger
from omnia_gateway.services import model_router as router_module
from omnia_gateway.services.pricing import calculate_cost_rub
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

    # TRUE token streaming from oneprovider — the page builds live in the preview.
    source: AsyncIterator[tuple[str, str]] = oneprovider.astream(
        model,
        messages,
        temperature=0.5 if temperature is None else temperature,
        **({"max_tokens": max_tokens} if max_tokens is not None else {}),
    )

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
