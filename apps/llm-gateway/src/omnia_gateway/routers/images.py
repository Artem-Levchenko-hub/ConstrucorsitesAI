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
from omnia_gateway.core.http import get_http
from omnia_gateway.services import billing, file_logger

router = APIRouter(prefix="/v1", tags=["images"])
log = structlog.get_logger(__name__)

# Image model registry: exposed id → (provider, upstream model id).
#   proxyapi — OpenAI gpt-image via proxyapi.ru/openai (dead while balance dry).
#   vsegpt   — flux / nano-banana / imagen via api.vsegpt.ru, OpenAI-compatible
#              /images/generations on the SAME key as the chat models.
_IMAGE_MODELS: dict[str, tuple[str, str]] = {
    "gpt-image-1": ("proxyapi", "gpt-image-1"),
    "img-flux/flux-2-klein-4b": ("vsegpt", "img-flux/flux-2-klein-4b"),
    "img-flux/flux-2-pro": ("vsegpt", "img-flux/flux-2-pro"),
    "img-google/nano-banana-2": ("vsegpt", "img-google/nano-banana-2"),
    "img-google/nano-banana-pro": ("vsegpt", "img-google/nano-banana-pro"),
    "img-google/imagen4-preview": ("vsegpt", "img-google/imagen4-preview"),
}

# Per-image price (RUB), low quality 1024x1024. Conservative ceiling — actual
# proxyapi list is ~₽1.3/image; we round up to absorb FX wiggle. The api side
# enforces a 30-image-per-prompt cap so the worst case is ~₽45 per generation.
_PRICE_PER_IMAGE_RUB = Decimal("1.50")

# Hard timeout for one image call. Direct vsegpt flux gens are ~4-6s warm;
# leave headroom for an occasional cold model-load on the provider side.
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
    provider, upstream_model = _IMAGE_MODELS[req.model]
    if provider == "vsegpt":
        if settings.vsegpt_api_key is None:
            raise _gateway_error_to_http(
                ModelUnavailableError("vsegpt_api_key not configured for image generation")
            )
        api_key = settings.vsegpt_api_key.get_secret_value()
        base_url = settings.vsegpt_base_url.rstrip("/")
    else:  # proxyapi (OpenAI gpt-image)
        if settings.proxyapi_api_key is None:
            raise _gateway_error_to_http(
                ModelUnavailableError("proxyapi_api_key not configured for image generation")
            )
        api_key = settings.proxyapi_api_key.get_secret_value()
        base_url = settings.proxyapi_openai_base_url.rstrip("/")

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
    # `quality` is an OpenAI gpt-image knob; vsegpt flux/nano reject unknown
    # fields, so only send it on the proxyapi path.
    if provider == "proxyapi" and req.quality != "auto":
        payload["quality"] = req.quality
    # vsegpt flux/nano return base64 ONLY and require it be requested explicitly
    # — otherwise "Only response_format = b64_json is supported" (400). The api
    # resolver already decodes b64_json.
    if provider == "vsegpt":
        payload["response_format"] = "b64_json"

    headers = {"Authorization": f"Bearer {api_key}"}

    # vsegpt.ru needs a SYNC httpx.Client on a worker thread with trust_env=False
    # + an explicit no-op mounts transport. Two failure modes otherwise (see
    # providers/vsegpt.py docstring): (1) the container HTTPS_PROXY (Gemini
    # geo-bypass) tunnels this RU endpoint, and (2) httpx.AsyncClient inside the
    # uvicorn loop intermittently hangs the TLS handshake to api.vsegpt.ru. A
    # sync client on a fresh thread connects in ~300ms; a direct gen is ~4-6s.
    # proxyapi stays on the shared async client (it's whitelisted in NO_PROXY).
    # vsegpt's TLS handshake to api.vsegpt.ru INTERMITTENTLY stalls inside a
    # long-lived process (the standalone call connects in ~300ms, the in-loop one
    # sometimes hangs — same flake providers/vsegpt.py documents). Retry transient
    # transport faults with a fresh client, and use a tight connect timeout so a
    # stalled handshake fails in ~15s and re-tries instead of burning the ceiling.
    _TRANSIENT = (
        httpx.ConnectError,
        httpx.ConnectTimeout,
        httpx.ReadTimeout,
        httpx.RemoteProtocolError,
    )

    def _vsegpt_post() -> httpx.Response:
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
        if provider == "vsegpt":
            return await asyncio.to_thread(_vsegpt_post)
        return await get_http().post(
            url, json=payload, headers=headers, timeout=_IMAGE_TIMEOUT_SECONDS
        )

    try:
        resp = await _post()
        # vsegpt rate-limits ~1 req/sec → 429. The api resolver serialises, but
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
