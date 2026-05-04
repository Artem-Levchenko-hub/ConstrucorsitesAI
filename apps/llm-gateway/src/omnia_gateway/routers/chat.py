"""POST /v1/chat/completions — orchestrates the full M0…M3 pipeline.

Order of operations (non-streaming path):
    safety filter → cache lookup → balance precheck → LLM → bill → cache store → file log

Streaming path defers billing + file logging to `services.streaming`, which
emits SSE chunks and bills based on what the wire actually delivered.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

import structlog
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from omnia_gateway.core.errors import GatewayError, WalletEmptyError
from omnia_gateway.services import billing, cache, file_logger, safety, streaming
from omnia_gateway.services import litellm_router as router_module
from omnia_gateway.services.pricing import calculate_cost_rub
from omnia_gateway.services.token_counter import count_message_tokens

router = APIRouter(prefix="/v1", tags=["chat"])
log = structlog.get_logger(__name__)


class ChatMessage(BaseModel):
    role: Literal["system", "user", "assistant"]
    content: str


class ChatMetadata(BaseModel):
    project_id: UUID | None = None
    message_id: UUID | None = None


class ChatCompletionRequest(BaseModel):
    model: str
    messages: list[ChatMessage] = Field(min_length=1)
    stream: bool = False
    user: UUID | None = None
    temperature: float | None = None
    max_tokens: int | None = None
    metadata: ChatMetadata | None = None


def _gateway_error_to_http(exc: GatewayError) -> HTTPException:
    return HTTPException(
        status_code=exc.http_status,
        detail={
            "error": {
                "code": exc.code,
                "message": exc.message,
                "details": exc.details,
            }
        },
    )


def _estimate_cost(model: str, messages: list[dict[str, str]]) -> Decimal:
    """Conservative pre-flight estimate: input tokens + 25% as output guess."""
    tokens_in = count_message_tokens(model, messages)
    estimated_out = max(64, tokens_in // 4)
    try:
        return calculate_cost_rub(model, tokens_in, estimated_out)
    except Exception:
        return Decimal("0")


@router.post("/chat/completions")
async def chat_completions(
    req: ChatCompletionRequest, request: Request
) -> Any:
    raw_messages = [m.model_dump() for m in req.messages]
    filtered_messages = safety.sanitize_messages(raw_messages)

    meta = req.metadata or ChatMetadata()

    if req.stream:
        # Pre-check balance before opening the SSE generator (cheap reject).
        if req.user is not None:
            try:
                await billing.precheck_balance(req.user, _estimate_cost(req.model, filtered_messages))
            except WalletEmptyError as exc:
                raise _gateway_error_to_http(exc) from exc
            except Exception:
                # DB unavailable — allow through; the post-stream charge will surface real failures.
                log.exception("stream.precheck_failed", user=str(req.user))

        return EventSourceResponse(
            streaming.stream_completion(
                request=request,
                model=req.model,
                messages=filtered_messages,
                user_id=req.user,
                project_id=meta.project_id,
                message_id=meta.message_id,
                temperature=req.temperature,
                max_tokens=req.max_tokens,
            )
        )

    # ---- non-streaming path ----
    cache_key = cache.make_cache_key(req.model, filtered_messages)
    try:
        cached = await cache.get(cache_key)
    except Exception:
        cached = None
        log.exception("cache.get_failed", key=cache_key)

    if cached is not None:
        cached.setdefault("metadata", {})
        cached["metadata"]["cache_hit"] = True
        try:
            file_logger.log_request(
                {
                    "user_id": req.user,
                    "project_id": meta.project_id,
                    "message_id": meta.message_id,
                    "model": req.model,
                    "tokens_in": cached.get("usage", {}).get("prompt_tokens", 0),
                    "tokens_out": cached.get("usage", {}).get("completion_tokens", 0),
                    "cost_rub": Decimal("0"),
                    "cache_hit": True,
                    "fallback_used": False,
                    "stream": False,
                }
            )
        except Exception:
            log.exception("cache_hit.file_log_failed")
        return cached

    # Cache miss — pre-check balance.
    if req.user is not None:
        try:
            await billing.precheck_balance(req.user, _estimate_cost(req.model, filtered_messages))
        except WalletEmptyError as exc:
            raise _gateway_error_to_http(exc) from exc
        except Exception:
            log.exception("precheck.db_unavailable", user=str(req.user))

    try:
        response = await router_module.acompletion(
            model=req.model,
            messages=filtered_messages,
            user=str(req.user) if req.user else None,
            temperature=req.temperature,
            max_tokens=req.max_tokens,
        )
    except GatewayError as exc:
        raise _gateway_error_to_http(exc) from exc

    usage = response.get("usage") or {}
    tokens_in = int(usage.get("prompt_tokens", 0))
    tokens_out = int(usage.get("completion_tokens", 0))

    actual_model = router_module.slug_to_omnia(response.get("model", "")) or req.model
    fallback_used = actual_model != req.model

    cost_rub = calculate_cost_rub(actual_model, tokens_in, tokens_out)

    # Bill (atomic): user only — service-account requests skip billing.
    if req.user is not None:
        try:
            await billing.charge(
                user_id=req.user,
                project_id=meta.project_id,
                message_id=meta.message_id,
                model_id=actual_model,
                tokens_in=tokens_in,
                tokens_out=tokens_out,
                cost_rub=cost_rub,
                description=f"Completion via {actual_model}",
            )
        except WalletEmptyError as exc:
            raise _gateway_error_to_http(exc) from exc
        except Exception:
            log.exception("charge_failed", user=str(req.user), model=actual_model)

    response.setdefault("metadata", {})
    response["metadata"]["actual_model_used"] = actual_model
    response["metadata"]["fallback_used"] = fallback_used
    response["metadata"]["cost_rub"] = str(cost_rub)
    response["metadata"]["cache_hit"] = False

    try:
        await cache.set(cache_key, response)
    except Exception:
        log.exception("cache.set_failed", key=cache_key)

    try:
        file_logger.log_request(
            {
                "user_id": req.user,
                "project_id": meta.project_id,
                "message_id": meta.message_id,
                "model": actual_model,
                "tokens_in": tokens_in,
                "tokens_out": tokens_out,
                "cost_rub": cost_rub,
                "cache_hit": False,
                "fallback_used": fallback_used,
                "stream": False,
            }
        )
    except Exception:
        log.exception("file_log_failed")

    return response
