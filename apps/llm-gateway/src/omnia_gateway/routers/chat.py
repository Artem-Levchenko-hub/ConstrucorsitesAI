"""POST /v1/chat/completions — non-streaming for M0.

Streaming (`stream: true`) returns 501 here; M1 will implement SSE per
docs/01-api-contract.md.
"""
from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

import structlog
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from omnia_gateway.core.errors import GatewayError
from omnia_gateway.services import litellm_router
from omnia_gateway.services.pricing import calculate_cost_rub
from omnia_gateway.services.usage_logger import log_usage

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


@router.post("/chat/completions")
async def chat_completions(req: ChatCompletionRequest) -> dict[str, Any]:
    if req.stream:
        # M1 will replace this branch with a real SSE EventSourceResponse.
        raise HTTPException(
            status_code=501,
            detail={
                "error": {
                    "code": "not_implemented",
                    "message": "stream=true lands in M1; use stream=false",
                }
            },
        )

    try:
        response = await litellm_router.acompletion(
            model=req.model,
            messages=[m.model_dump() for m in req.messages],
            user=str(req.user) if req.user else None,
            temperature=req.temperature,
            max_tokens=req.max_tokens,
        )
    except GatewayError as exc:
        raise _gateway_error_to_http(exc) from exc

    usage = response.get("usage") or {}
    tokens_in = int(usage.get("prompt_tokens", 0))
    tokens_out = int(usage.get("completion_tokens", 0))
    cost_rub = calculate_cost_rub(req.model, tokens_in, tokens_out)

    meta = req.metadata or ChatMetadata()
    try:
        await log_usage(
            user_id=req.user,
            project_id=meta.project_id,
            message_id=meta.message_id,
            model_id=req.model,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            cost_rub=cost_rub,
        )
    except Exception:
        # M0: don't deny the user a successful generation if analytics writes fail.
        # M3 makes this a hard failure when wallet debit is wired in.
        log.exception("usage_log_failed", model=req.model, message_id=str(meta.message_id))

    response.setdefault("metadata", {})
    response["metadata"]["actual_model_used"] = response.get("model", req.model)
    response["metadata"]["cost_rub"] = str(cost_rub)
    return response
