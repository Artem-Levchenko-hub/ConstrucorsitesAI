"""POST /v1/images/generations — image generation.

ONE provider — aitunnel.ru, OpenAI-compatible `/images/generations` on the same
key + surface as the chat models (`https://api.aitunnel.ru/v1`). Serves both the
flux family (the live default) and OpenAI `gpt-image-1`.

We don't go through the chat router — image endpoints are out of scope for it.
A direct httpx call is simpler.

Wallet billing follows the chat-completions contract: if `user` is provided,
the caller is a real end-user and is debited; if `user` is null, the request
is service-account (apps/api's image_resolver) and the cost comes out of
Omnia's own balance.
"""

from __future__ import annotations

import asyncio
import time
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
from omnia_gateway.services import billing, file_logger

router = APIRouter(prefix="/v1", tags=["images"])
log = structlog.get_logger(__name__)

# Image model registry: exposed id → upstream aitunnel catalog id. Exposed ids
# stay stable (apps/api's image_resolver + IMAGE_GEN_MODEL env reference them);
# the upstream slugs use aitunnel's dotted naming (`flux.2-*`). The retired
# img-google/* entries (nano-banana, imagen) are gone — aitunnel does not carry
# them and they had zero call sites.
_IMAGE_MODELS: dict[str, str] = {
    "gpt-image-1": "gpt-image-1",
    "img-flux/flux-2-klein-4b": "flux.2-klein-4b",
    "img-flux/flux-2-pro": "flux.2-pro",
}

# Per-image price (RUB), low quality 1024x1024. Conservative ceiling — the api side
# enforces a 30-image-per-prompt cap so the worst case is ~₽45 per generation.
_PRICE_PER_IMAGE_RUB = Decimal("1.50")

# Hard timeout for one image call. Direct flux gens are ~4-6s warm; leave headroom
# for an occasional cold model-load on the provider side.
_IMAGE_TIMEOUT_SECONDS = 90.0


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
    upstream_model = _IMAGE_MODELS[req.model]
    if settings.aitunnel_api_key is None:
        raise _gateway_error_to_http(
            ModelUnavailableError("aitunnel_api_key not configured for image generation")
        )
    api_key = settings.aitunnel_api_key.get_secret_value()
    base_url = settings.aitunnel_base_url.rstrip("/")

    estimated_cost = _PRICE_PER_IMAGE_RUB * req.n
    if req.user is not None:
        try:
            await billing.precheck_balance(req.user, estimated_cost)
        except WalletEmptyError as exc:
            raise _gateway_error_to_http(exc) from exc
        except Exception:
            log.exception("images.precheck_failed", user=str(req.user))

    url = f"{base_url}/images/generations"
    payload: dict[str, Any] = {
        "model": upstream_model,
        "prompt": req.prompt,
        "n": req.n,
        "size": req.size,
    }
    # `quality` is an OpenAI gpt-image knob; flux rejects unknown fields, so only
    # send it on the gpt-image path. gpt-image always answers b64 and rejects
    # `response_format`; flux needs b64_json requested explicitly (live-verified
    # on aitunnel 15.07: data[0].b64_json, ~4s). The api resolver decodes b64_json.
    if req.model == "gpt-image-1":
        if req.quality != "auto":
            payload["quality"] = req.quality
    else:
        payload["response_format"] = "b64_json"

    headers = {"Authorization": f"Bearer {api_key}"}

    # aitunnel is hit through a SYNC httpx.Client on a worker thread with
    # trust_env=False + an explicit no-op mounts transport. Two failure modes
    # otherwise (see providers/aitunnel.py docstring): (1) the container
    # HTTPS_PROXY (Gemini geo-bypass) tunnels this endpoint, and (2)
    # httpx.AsyncClient inside the uvicorn loop intermittently hangs the TLS
    # handshake. A sync client on a fresh thread connects fast; a direct gen is
    # ~4-6s. Retry transient transport faults with a fresh client, and use a tight
    # connect timeout so a stalled handshake fails in ~15s and re-tries instead of
    # burning the ceiling.
    _TRANSIENT = (
        httpx.ConnectError,
        httpx.ConnectTimeout,
        httpx.ReadTimeout,
        httpx.RemoteProtocolError,
    )

    def _aitunnel_post() -> httpx.Response:
        last: Exception | None = None
        for attempt in range(3):
            try:
                with httpx.Client(
                    timeout=httpx.Timeout(_IMAGE_TIMEOUT_SECONDS, connect=15.0),
                    trust_env=False,
                    mounts={"all://": httpx.HTTPTransport()},
                ) as c:
                    r = c.post(url, json=payload, headers=headers)
                    r.read()  # buffer the body before the client closes
                    return r
            except _TRANSIENT as exc:
                last = exc
                if attempt < 2:
                    time.sleep(0.6)
                    continue
                raise
        raise last  # type: ignore[misc]  # unreachable — loop returns or raises

    async def _post() -> httpx.Response:
        return await asyncio.to_thread(_aitunnel_post)

    try:
        resp = await _post()
        # flux/nano rate-limit ~1 req/sec → 429. The api resolver serialises, but
        # retry once after a beat so a burst (or a co-tenant) doesn't lose a tile.
        if resp.status_code == 429:
            await asyncio.sleep(1.5)
            resp = await _post()
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
