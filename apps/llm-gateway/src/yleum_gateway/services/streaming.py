"""SSE event-stream generator for chat completions.

Completed streams settle the provider receipt before reporting success.
Interrupted streams retain received provider evidence and estimate only missing usage.

There is exactly one chat model (`gemini-3.1-pro-preview-customtools`) and one upstream
(llmgw), so the stream source is always `providers/llmgw.astream`.
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
from anyio import CancelScope
from fastapi import Request

from yleum_gateway.core.errors import BillingReconciliationRequiredError, GatewayError
from yleum_gateway.providers import llmgw
from yleum_gateway.services import billing, file_logger
from yleum_gateway.services import model_router as router_module
from yleum_gateway.services.cache_pricing import anthropic_cache_write_factor
from yleum_gateway.services.pricing import read_reported_cost, resolve_request_cost
from yleum_gateway.services.token_counter import count_message_tokens, count_text_tokens

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
    run_id: UUID | None = None,
    stage: str | None = None,
) -> AsyncIterator[str]:
    """Generate the SSE stream + bill at end.

    ``free=True`` routes the end-of-stream charge through ``billing.charge(free=True)``
    — Usage is logged with the real cost, but the wallet is not debited.
    """
    response_id = f"chatcmpl-{uuid4()}"
    created = int(time.time())
    accumulated: list[str] = []
    actual_model = model
    cancelled = False
    upstream_error: GatewayError | None = None
    settlement_error: GatewayError | None = None
    receipt: dict[str, Any] = {}
    settlement_task: asyncio.Task[None] | None = None
    usage: dict[str, Any] = {}
    cost_rub = Decimal("0")
    provenance: dict[str, Any] = {}
    provider_request_id: str | None = None

    source = llmgw.astream(
        model, messages,
        temperature=0.5 if temperature is None else temperature,
        receipt=receipt,
        **({"max_tokens": max_tokens} if max_tokens is not None else {}),
    )

    async def settle() -> None:
        nonlocal usage, cost_rub, provenance, actual_model, provider_request_id
        provider_request_id = receipt.get("provider_request_id") or receipt.get("id")
        actual_model = llmgw.resolve_response_model(
            receipt.get("model", ""), actual_model, provider_request_id,
        )
        output_text = "".join(accumulated)
        usage, estimated = llmgw.normalize_usage(
            receipt.get("usage") or {},
            count_message_tokens(actual_model, messages) if output_text else 0,
            count_text_tokens(actual_model, output_text) if output_text else 0,
        )
        tokens_in, tokens_out = usage["prompt_tokens"], usage["completion_tokens"]
        cache_read = usage["prompt_cache_hit_tokens"]
        cache_write = usage["cache_creation_input_tokens"]
        # A lost DONE means transport failure, not invalidation of a monetary
        # receipt or token counters already received from the provider.
        reported = read_reported_cost(receipt, receipt.get("headers", {}))
        cost_rub, provenance = await resolve_request_cost(
            actual_model, reported=reported, tokens_in=tokens_in, tokens_out=tokens_out,
            cache_read_tokens=cache_read, cache_write_tokens=cache_write,
            estimated_tokens=estimated,
            cache_write_factor=anthropic_cache_write_factor(actual_model, {
                # The chat adapter transmits content, but drops message-level
                # cache_control and has no top-level controls or tools.
                "messages": [{"content": message.get("content", "")} for message in messages],
            }),
        )
        if user_id is not None and (output_text or receipt):
            try:
                await billing.charge(
                    user_id=user_id, project_id=project_id, message_id=message_id,
                    run_id=run_id, stage=stage, model_id=actual_model,
                    tokens_in=tokens_in, tokens_out=tokens_out, cost_rub=cost_rub,
                    cache_read_tokens=cache_read, cache_write_tokens=cache_write,
                    provider_request_id=provider_request_id, cost_provenance=provenance,
                    provider_cost_usd=reported.cost_usd,
                    description=f"Streamed completion via {actual_model}", free=free,
                )
            except GatewayError:
                raise
            except Exception as exc:
                raise BillingReconciliationRequiredError(
                    "Stream completed but billing could not be confirmed",
                    details={"provider_request_id": provider_request_id},
                ) from exc

    try:
        try:
            async for delta, slug in source:
                actual_model = router_module.slug_to_omnia(slug) or actual_model
                if delta:
                    accumulated.append(delta)
                    yield _sse({
                        "id": response_id, "object": "chat.completion.chunk",
                        "created": created, "model": actual_model,
                        "choices": [{"index": 0, "delta": {"content": delta}, "finish_reason": None}],
                    })
        except GatewayError as exc:
            upstream_error = exc
        settlement_task = asyncio.create_task(settle())
        try:
            await asyncio.shield(settlement_task)
        except GatewayError as exc:
            settlement_error = exc
        error = settlement_error or upstream_error
        if error is not None:
            yield _sse({"error": {
                "code": error.code, "message": error.message, "details": error.details,
            }})
            return
        yield _sse({
            "id": response_id, "object": "chat.completion.chunk", "created": created,
            "model": actual_model,
            "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
            "usage": usage,
            "metadata": {
                "actual_model_used": actual_model, "fallback_used": actual_model != model,
                "cost_rub": str(cost_rub), "provider_request_id": provider_request_id,
                "cost_provenance": provenance,
            },
        })
        yield _sse("[DONE]")
    except asyncio.CancelledError:
        cancelled = True
        raise
    finally:
        # SSE disconnects cancel their AnyIO scope repeatedly. Protect cleanup
        # from that scope and wait for the SAME owned task: never abort a debit
        # mid-transaction and never replay one whose commit is ambiguous.
        with CancelScope(shield=True):
            await source.aclose()
            if settlement_task is None:
                settlement_task = asyncio.create_task(settle())
            try:
                await asyncio.shield(settlement_task)
            except GatewayError as exc:
                if settlement_error is None:
                    log.exception("stream.charge_failed", user_id=str(user_id), model=actual_model)
                settlement_error = exc
            except Exception:
                log.exception("stream.charge_failed", user_id=str(user_id), model=actual_model)
        try:
            logged_error = settlement_error or upstream_error
            file_logger.log_request({
                "user_id": user_id, "project_id": project_id, "message_id": message_id,
                "model": actual_model, "tokens_in": usage.get("prompt_tokens", 0),
                "tokens_out": usage.get("completion_tokens", 0), "cost_rub": cost_rub,
                "cache_hit": False, "fallback_used": actual_model != model,
                "stream": True, "cancelled": cancelled,
                "error": logged_error.code if logged_error else None,
                "provider_request_id": provider_request_id,
                "provider_charge_ambiguous": bool(upstream_error and upstream_error.details.get("provider_charge_ambiguous")),
            })
        except Exception:
            log.exception("stream.file_log_failed")
