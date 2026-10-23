"""SSE event-stream generator for chat completions.

Completed streams settle the provider receipt before reporting success.
Interrupted streams retain received provider evidence and estimate only missing usage.

The shared OpenAI transport uses the admitted provider. Sonnet 5.5 may switch
to AI Tunnel after a committed primary refusal, before any accepted output.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncGenerator, AsyncIterator
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import structlog
from anyio import CancelScope
from fastapi import Request

from yleum_gateway.core.errors import (
    BillingReconciliationRequiredError,
    GatewayError,
    ModelNotFoundError,
    UpstreamProviderError,
)
from yleum_gateway.providers import llmgw
from yleum_gateway.services import (
    billing,
    file_logger,
    generation_attempts,
    provider_calls,
    text_failover,
)
from yleum_gateway.services import model_router as router_module
from yleum_gateway.services.cache_pricing import anthropic_cache_write_factor
from yleum_gateway.services.generation_identity import GenerationProviderIdentity
from yleum_gateway.services.output_stop import output_stop_error
from yleum_gateway.services.pricing import read_reported_cost, resolve_request_cost
from yleum_gateway.services.provider_receipt_cleanup import await_provider_receipt
from yleum_gateway.services.receipt_evidence import receipt_evidence
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
    provider_call_id: UUID | None = None,
    generation_identity: GenerationProviderIdentity | None = None,
    provider_deadline: float | None = None,
    provider_scope: str = "llmgw",
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

    try:
        if not llmgw.is_llmgw_model(model):
            raise ModelNotFoundError(f"Unknown model: {model}")
        if provider_call_id is None:
            provider_call_id = await provider_calls.start_call(
                route="/v1/chat/completions", model=model, user_id=user_id,
                project_id=project_id, message_id=message_id, run_id=run_id,
                stage=stage, free=free, **text_failover.scope_options(provider_scope),
            )
    except GatewayError as exc:
        yield _sse({"error": {"code": exc.code, "message": exc.message, "details": exc.details}})
        return
    receipt_finished = False
    source_options: dict[str, Any] = {}
    if max_tokens is not None:
        source_options["max_tokens"] = max_tokens
    if generation_identity is not None:
        source_options["retry_preconnect"] = False
    if provider_deadline is not None:
        source_options["timeout"] = provider_deadline - asyncio.get_running_loop().time()
    def stream_source() -> AsyncGenerator[tuple[str, str], None]:
        if provider_deadline is not None:
            source_options["timeout"] = provider_deadline - asyncio.get_running_loop().time()
        return llmgw.astream(
            model, messages, temperature=0.5 if temperature is None else temperature,
            receipt=receipt, **source_options, **text_failover.scope_options(provider_scope),
        )

    source = stream_source()

    async def settle_receipt() -> None:
        nonlocal usage, cost_rub, provenance, actual_model, provider_request_id, receipt_finished
        assert provider_call_id is not None
        if receipt_finished:
            return
        reported = read_reported_cost(receipt, receipt.get("headers", {}), **text_failover.scope_options(provider_scope))
        failure_type = provider_calls.classify_failure(upstream_error) if upstream_error else (
            "CancelledError" if cancelled else None
        )
        if (not accumulated and not receipt.get("usage")
                and reported.cost_rub is None and reported.cost_usd is None):
            receipt_finished = True
            await generation_attempts.finish(
                generation_identity, provider_call_id,
                reason="preconnect" if upstream_error and upstream_error.details.get("provider_failure_kind") == "preconnect" else None,
                status="failed" if upstream_error and upstream_error.details.get(
                    "provider_charge_ambiguous") is False else "ambiguous",
                **receipt_evidence({**receipt, **(upstream_error.details if upstream_error else {})}, **text_failover.scope_options(provider_scope)),
                error_type=failure_type,
            )
            if generation_identity is not None and upstream_error and upstream_error.details.get("provider_failure_kind") == "preconnect":
                upstream_error.details.update(retry_safe=True, retry_policy="llmgw_preconnect_v1")
            return
        provider_request_id = receipt_evidence(receipt, **text_failover.scope_options(provider_scope))["provider_request_id"]
        actual_model = llmgw.resolve_response_model(
            receipt.get("model", ""), actual_model, provider_request_id,
        )
        if provider_scope == "aitunnel" and actual_model != model:
            raise BillingReconciliationRequiredError("Backup returned an unexpected model")
        output_text = "".join(accumulated)
        usage, estimated = llmgw.normalize_usage(
            receipt.get("usage") or {},
            count_message_tokens(actual_model, messages) if output_text else 0,
            count_text_tokens(actual_model, output_text) if output_text else 0,
        )
        if provider_scope == "aitunnel":
            usage.pop("balance", None)
            usage.pop("cost", None)
        tokens_in, tokens_out = usage["prompt_tokens"], usage["completion_tokens"]
        cache_read = usage["prompt_cache_hit_tokens"]
        cache_write = usage["cache_creation_input_tokens"]
        # A lost DONE means transport failure, not invalidation of a monetary
        # receipt or token counters already received from the provider.
        cost_rub, provenance = await resolve_request_cost(
            actual_model, reported=reported, tokens_in=tokens_in, tokens_out=tokens_out,
            cache_read_tokens=cache_read, cache_write_tokens=cache_write,
            estimated_tokens=estimated,
            **text_failover.scope_options(provider_scope),
            cache_write_factor=anthropic_cache_write_factor(actual_model, {
                # The chat adapter transmits content, but drops message-level
                # cache_control and has no top-level controls or tools.
                "messages": [{"content": message.get("content", "")} for message in messages],
            }),
        )
        receipt_finished = True
        await generation_attempts.finish(
            generation_identity, provider_call_id, status="ambiguous" if estimated else "completed",
            actual_model=actual_model, provider_request_id=provider_request_id,
            tokens_in=tokens_in, tokens_out=tokens_out,
            cache_read_tokens=cache_read, cache_write_tokens=cache_write,
            calculated_cost_rub=(Decimal(provenance["calculated_cost_rub"]) if provenance["calculated_cost_rub"] is not None else None),
            provider_cost_rub=reported.cost_rub, provider_cost_usd=reported.cost_usd,
            cost_provenance=provenance,
            error_type=failure_type,
        )
        if user_id is not None and (output_text or receipt):
            try:
                await billing.charge(
                    provider_binding=(
                        billing.ProviderUsageBinding(generation_identity, provider_call_id)
                        if generation_identity is not None else None
                    ),
                    user_id=user_id, project_id=project_id, message_id=message_id,
                    run_id=run_id, stage=stage, model_id=actual_model,
                    tokens_in=tokens_in, tokens_out=tokens_out, cost_rub=cost_rub,
                    cache_read_tokens=cache_read, cache_write_tokens=cache_write,
                    provider_request_id=provider_request_id, cost_provenance=provenance,
                    provider_cost_usd=reported.cost_usd,
                    description=f"Streamed completion via {actual_model}", free=free,
                    **text_failover.scope_options(provider_scope),
                )
            except GatewayError:
                raise
            except Exception as exc:
                raise BillingReconciliationRequiredError(
                    "Stream completed but billing could not be confirmed",
                    details={"provider_request_id": provider_request_id},
                ) from exc

    async def settle() -> None:
        nonlocal receipt_finished
        assert provider_call_id is not None
        known_receipt = receipt_evidence(receipt, **text_failover.scope_options(provider_scope))
        try:
            await settle_receipt()
            from yleum_gateway.services.generation_response_capture import (
                confirm_response_accounting,
            )

            confirm_response_accounting()
        except Exception as exc:
            if not receipt_finished:
                receipt_finished = True
                await generation_attempts.finish(
                    generation_identity, provider_call_id, status="ambiguous",
                    error_type=provider_calls.classify_failure(exc),
                    **known_receipt,
                )
            if isinstance(exc, GatewayError):
                raise
            raise BillingReconciliationRequiredError(
                "Provider receipt requires reconciliation",
                details={"provider_charge_ambiguous": True},
            ) from None

    try:
        while True:
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
            except Exception as exc:
                upstream_error = UpstreamProviderError(
                    f"Provider stream failed: {type(exc).__name__}",
                    details={"provider_charge_ambiguous": True},
                )
            overload = upstream_error.details.pop("_overload_retry_candidate", None) if upstream_error else None
            if (provider_scope == "llmgw" and isinstance(overload, dict)
                    and not accumulated and not receipt):
                receipt_finished = True
                settlement_task = asyncio.create_task(generation_attempts.finish(
                    generation_identity, provider_call_id, status="failed", reason="upstream_refused",
                    error_type="HTTPStatusError",
                ))
                await await_provider_receipt(settlement_task)
                if generation_identity is not None:
                    from yleum_gateway.services.generation_response_capture import (
                        confirm_response_accounting,
                        remember_provider_refusal,
                    )

                    upstream_error = text_failover.ProviderRefusedError("Primary provider refused the request", details=overload)
                    remember_provider_refusal({"error": {"code": "api_error", "details": overload}})
                    confirm_response_accounting()
                else:
                    await source.aclose()
                    provider_scope = "aitunnel"
                    provider_call_id = await provider_calls.start_call(
                        route="/v1/chat/completions", model=model, user_id=user_id,
                        project_id=project_id, message_id=message_id, run_id=run_id,
                        stage=stage, free=free, provider_scope=provider_scope,
                    )
                    source_options["retry_preconnect"] = False
                    source = stream_source()
                    receipt_finished, settlement_task, upstream_error = False, None, None
                    continue
            break
        settlement_task = asyncio.create_task(settle())
        try:
            await asyncio.shield(settlement_task)
        except GatewayError as exc:
            settlement_error = exc
        output_error = output_stop_error(
            None if receipt.get("finish_reason_conflict") else receipt.get("finish_reason"),
        )
        error = settlement_error or upstream_error or output_error
        if error is not None:
            yield _sse({"error": {
                "code": error.code, "message": error.message, "details": error.details,
            }})
            return
        yield _sse({
            "id": response_id, "object": "chat.completion.chunk", "created": created,
            "model": actual_model,
            "choices": [{"index": 0, "delta": {}, "finish_reason": receipt["finish_reason"]}],
            "usage": usage,
            "metadata": {
                "actual_model_used": actual_model, "fallback_used": (actual_model != model or provider_scope == "aitunnel"),
                "cost_rub": str(cost_rub), "provider_request_id": provider_request_id,
                "cost_provenance": provenance,
            },
        })
        yield _sse("[DONE]")
    except (asyncio.CancelledError, GeneratorExit):
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
                "cache_hit": False, "fallback_used": (actual_model != model or provider_scope == "aitunnel"),
                "stream": True, "cancelled": cancelled,
                "error": logged_error.code if logged_error else None,
                "provider_request_id": provider_request_id,
                "provider_charge_ambiguous": bool(upstream_error and upstream_error.details.get("provider_charge_ambiguous")),
            })
        except Exception:
            log.exception("stream.file_log_failed")
