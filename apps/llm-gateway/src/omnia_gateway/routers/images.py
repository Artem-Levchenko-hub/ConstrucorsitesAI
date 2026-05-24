"""POST /v1/images/generations — image generation via OpenAI-compatible proxyapi.

Routes `gpt-image-1` through proxyapi.ru/openai/v1/images/generations (the same
proxy that fronts the GPT-5 family). Unlike chat completions we don't go
through LiteLLM Router — the Router treats images endpoints as out-of-scope
and falls back to chat-completion semantics. A direct httpx call is simpler.

Wallet billing follows the chat-completions contract: if `user` is provided,
the caller is a real end-user and is debited; if `user` is null, the request
is service-account (apps/api's image_resolver) and the cost comes out of
Omnia's own proxyapi balance.

Pricing: gpt-image-1 low-quality 1024x1024 ≈ $0.011 / image (OpenAI list).
At ~100₽/USD + 20% markup we charge `_PRICE_PER_IMAGE_RUB` per call.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

import httpx
import structlog
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from omnia_gateway.core.config import get_settings
from omnia_gateway.core.errors import (
    GatewayError,
    ModelNotFoundError,
    ModelUnavailableError,
    UpstreamProviderError,
    WalletEmptyError,
)
from omnia_gateway.core.http import get_http
from omnia_gateway.services import billing, file_logger

router = APIRouter(prefix="/v1", tags=["images"])
log = structlog.get_logger(__name__)

# Models we expose. Maps Omnia ID → upstream model name on proxyapi/openai.
_IMAGE_MODELS: dict[str, str] = {
    "gpt-image-1": "gpt-image-1",
}

# Per-image price (RUB), low quality 1024x1024. Conservative ceiling — actual
# proxyapi list is ~₽1.3/image; we round up to absorb FX wiggle. The api side
# enforces a 30-image-per-prompt cap so the worst case is ~₽45 per generation.
_PRICE_PER_IMAGE_RUB = Decimal("1.50")

# Hard timeout for one image call. proxyapi typically returns in 6-15s; give
# a generous ceiling so legitimate slow generations don't get killed.
_IMAGE_TIMEOUT_SECONDS = 60.0


class ImageGenerationRequest(BaseModel):
    model: str = Field(default="gpt-image-1")
    prompt: str = Field(min_length=1, max_length=4000)
    n: int = Field(default=1, ge=1, le=4)
    size: Literal["1024x1024", "1536x1024", "1024x1536", "auto"] = "1024x1024"
    quality: Literal["low", "medium", "high", "auto"] = "low"
    user: UUID | None = None


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


@router.post("/images/generations")
async def images_generations(req: ImageGenerationRequest) -> dict[str, Any]:
    if req.model not in _IMAGE_MODELS:
        raise _gateway_error_to_http(
            ModelNotFoundError(f"Unknown image model: {req.model}")
        )

    settings = get_settings()
    if settings.proxyapi_api_key is None:
        raise _gateway_error_to_http(
            ModelUnavailableError("proxyapi_api_key not configured for image generation")
        )

    estimated_cost = _PRICE_PER_IMAGE_RUB * req.n
    if req.user is not None:
        try:
            await billing.precheck_balance(req.user, estimated_cost)
        except WalletEmptyError as exc:
            raise _gateway_error_to_http(exc) from exc
        except Exception:
            log.exception("images.precheck_failed", user=str(req.user))

    upstream_model = _IMAGE_MODELS[req.model]
    api_key = settings.proxyapi_api_key.get_secret_value()
    base_url = settings.proxyapi_openai_base_url.rstrip("/")
    url = f"{base_url}/images/generations"

    payload: dict[str, Any] = {
        "model": upstream_model,
        "prompt": req.prompt,
        "n": req.n,
        "size": req.size,
    }
    if req.quality != "auto":
        payload["quality"] = req.quality

    client = get_http()
    try:
        resp = await client.post(
            url,
            json=payload,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=_IMAGE_TIMEOUT_SECONDS,
        )
    except httpx.TimeoutException as exc:
        raise _gateway_error_to_http(
            UpstreamProviderError(f"Image generation timed out: {exc}")
        ) from exc
    except httpx.RequestError as exc:
        raise _gateway_error_to_http(
            UpstreamProviderError(f"Image generation transport error: {exc}")
        ) from exc

    if resp.status_code in (401, 403):
        raise _gateway_error_to_http(
            ModelUnavailableError(
                f"Image provider auth failure ({resp.status_code}): {resp.text[:200]}"
            )
        )
    if resp.status_code == 429:
        raise _gateway_error_to_http(
            ModelUnavailableError(f"Image provider rate limited: {resp.text[:200]}")
        )
    if resp.status_code >= 400:
        raise _gateway_error_to_http(
            UpstreamProviderError(
                f"Image provider {resp.status_code}: {resp.text[:300]}"
            )
        )

    try:
        body = resp.json()
    except Exception as exc:
        raise _gateway_error_to_http(
            UpstreamProviderError(f"Image provider returned non-JSON: {exc}")
        ) from exc

    actual_n = len(body.get("data") or [])
    cost_rub = _PRICE_PER_IMAGE_RUB * actual_n

    if req.user is not None and actual_n > 0:
        try:
            await billing.charge(
                user_id=req.user,
                project_id=None,
                message_id=None,
                model_id=req.model,
                tokens_in=0,
                tokens_out=0,
                cost_rub=cost_rub,
                description=f"Image gen ({actual_n}x {req.model})",
            )
        except WalletEmptyError as exc:
            raise _gateway_error_to_http(exc) from exc
        except Exception:
            log.exception("images.charge_failed", user=str(req.user))

    body.setdefault("metadata", {})
    body["metadata"]["actual_model_used"] = req.model
    body["metadata"]["cost_rub"] = str(cost_rub)
    body["metadata"]["images_returned"] = actual_n

    try:
        file_logger.log_request(
            {
                "user_id": req.user,
                "project_id": None,
                "message_id": None,
                "model": req.model,
                "tokens_in": 0,
                "tokens_out": actual_n,
                "cost_rub": cost_rub,
                "cache_hit": False,
                "fallback_used": False,
                "stream": False,
            }
        )
    except Exception:
        log.exception("images.file_log_failed")

    return body
