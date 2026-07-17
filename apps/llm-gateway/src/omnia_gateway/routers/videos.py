"""POST /v1/videos/generations — text/image-to-video generation (Kling, async).

aitunnel's video surface is TASK-BASED: ``POST /v1/videos`` creates a job and
``GET /v1/videos/{id}`` polls it until ``status == "completed"``. This router
hides that dance behind ONE synchronous call — create → poll (hard-bounded) →
download the finished clip → return it as base64 — so it mirrors
``/v1/images/generations`` and apps/api's ``agent_media`` can treat image and
video the same. The same ``AITUNNEL_API_KEY`` authenticates create, poll, AND
the content download.

Video is SLOW (~1-3 min warm) and pricey, so the poll is hard-bounded
(``_POLL_BUDGET_S``) and fails fast on a dead/failed task instead of hanging the
caller forever (R-10: bound every wait, degrade fast). Billing mirrors images:
a real end-user (``user`` set) is debited the provider's reported ``cost_rub``;
a service-account call (``user`` null — the native agent building a site) comes
out of Omnia's own balance.

Transport uses the SAME hardening as providers/aitunnel.py: a sync ``httpx.Client``
on a worker thread with ``trust_env=False`` + a no-op mounts transport, so the
container's geo-bypass ``HTTPS_PROXY`` never tunnels these calls and a fresh
sync client connects fast.
"""

from __future__ import annotations

import asyncio
import base64
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

router = APIRouter(prefix="/v1", tags=["videos"])
log = structlog.get_logger(__name__)

# Exposed id → upstream aitunnel catalog slug (forwarded verbatim). Which ones
# aitunnel /v1/videos actually ACCEPTS was probed live 17.07:
#   * seedance-2.0-fast → 202 ✅ (the default; supports first/last frame keyframes)
#   * veo-3.1-fast / sora-2-pro → work, but reject duration=5 (veo: 4/6/8; sora:
#     4/8/12/16/20) — exposed for later, the api clamps duration to seedance's
#     window by default.
#   * kling-v3.0-std / kling-v3.0-pro → 500 on aitunnel right now (provider-side).
#     Kept so we flip back via VIDEO_GEN_MODEL the moment the provider fixes them.
_VIDEO_MODELS: dict[str, str] = {
    "seedance-2.0-fast": "seedance-2.0-fast",
    "veo-3.1-fast": "veo-3.1-fast",
    "sora-2-pro": "sora-2-pro",
    "kling-v3.0-std": "kling-v3.0-std",
    "kling-v3.0-pro": "kling-v3.0-pro",
}

# aspect → pixel size sent to aitunnel. 16:9 is the cinematic hero default; 9:16
# for mobile/story full-bleed; 1:1 for square cards.
_ASPECT_TO_SIZE: dict[str, str] = {
    "16:9": "1280x720",
    "9:16": "720x1280",
    "1:1": "960x960",
}

# Conservative RUB ceiling per clip when the provider omits a real cost — the
# api side caps how often the agent calls this, so the worst case stays bounded.
_PRICE_CEILING_RUB = Decimal("60.00")

# One create/poll/download HTTP call. Each is short; the overall wall-clock is
# governed by _POLL_BUDGET_S below, not this.
_HTTP_TIMEOUT_S = 60.0

# Hard wall-clock ceiling for the POLL loop. Kling std is ~1-3 min; beyond this
# we give up so the caller is never hung (R-10). This bounds ONLY the poll — the
# create POST and the clip download add their own time on top, so the api-side
# client timeout (agent_media._VIDEO_CLIENT_TIMEOUT = 360s) leaves ~120s of
# margin for create + download over this budget. Keep that invariant if you tune
# either number, or a near-budget completion + a fat clip download would trip the
# client timeout AFTER we already paid Kling (review 2026-07-17).
_POLL_BUDGET_S = 240.0
_POLL_INTERVAL_S = 6.0

_TRANSIENT = (
    httpx.ConnectError,
    httpx.ConnectTimeout,
    httpx.ReadTimeout,
    httpx.RemoteProtocolError,
)


class VideoGenerationRequest(BaseModel):
    model: str = Field(default="kling-v3.0-std")
    prompt: str = Field(min_length=1, max_length=4000)
    duration: int = Field(default=5, ge=3, le=10)
    aspect: Literal["16:9", "9:16", "1:1"] = "16:9"
    # Keyframe interpolation (the signature move): Flux paints a START and an END
    # still, Kling generates the UNIQUE motion BETWEEN them — a real fly-through,
    # not a generic text-to-video loop. Both are optional public URLs:
    #   * first_frame_url only  → animate FROM a still (image-to-video).
    #   * first + last          → interpolate start → end (the cinematic path).
    #   * neither               → pure text-to-video.
    first_frame_url: str | None = None
    last_frame_url: str | None = None
    # Back-compat alias for first_frame_url (older callers).
    image_url: str | None = None
    user: UUID | None = None


def _gateway_error_to_http(exc: GatewayError) -> HTTPException:
    return HTTPException(
        status_code=exc.http_status,
        detail={"error": {"code": exc.code, "message": exc.message, "details": exc.details}},
    )


def _client() -> httpx.Client:
    # trust_env=False + no-op mounts: ignore the container HTTPS_PROXY so aitunnel
    # is hit DIRECT (same rationale as providers/aitunnel.py). follow_redirects:
    # the finished-clip download can 302 to object storage — image_resolver's
    # media download does the same (review 2026-07-17); without it a redirect
    # returns an empty body and we'd discard a paid clip.
    return httpx.Client(
        timeout=httpx.Timeout(_HTTP_TIMEOUT_S, connect=15.0),
        trust_env=False,
        follow_redirects=True,
        mounts={"all://": httpx.HTTPTransport()},
    )


def _request_sync(
    method: str,
    url: str,
    headers: dict[str, str],
    json: Any | None,
    *,
    retry: bool = True,
) -> httpx.Response:
    """One aitunnel HTTP call. Buffers the body before the client closes so the
    caller can read it off-thread.

    ``retry`` toggles a single transient-fault retry. It MUST be False for the
    task-CREATE POST: /v1/videos is non-idempotent — a ReadTimeout after aitunnel
    accepted the request but before we read the id would, on retry, spawn a SECOND
    (paid) Kling task while we track only the second id, orphaning + double-billing
    the first (review 2026-07-17). GET poll/download are safe to retry."""
    attempts = 3 if retry else 1
    last: Exception | None = None
    for attempt in range(attempts):
        try:
            with _client() as c:
                r = c.request(method, url, headers=headers, json=json)
                r.read()
                return r
        except _TRANSIENT as exc:
            last = exc
            if attempt < attempts - 1:
                time.sleep(0.6)
                continue
            raise
    raise last  # type: ignore[misc]  # unreachable — loop returns or raises


@router.post("/videos/generations")
async def videos_generations(req: VideoGenerationRequest) -> dict[str, Any]:
    if req.model not in _VIDEO_MODELS:
        raise _gateway_error_to_http(ModelNotFoundError(f"Unknown video model: {req.model}"))

    settings = get_settings()
    if settings.aitunnel_api_key is None:
        raise _gateway_error_to_http(
            ModelUnavailableError("aitunnel_api_key not configured for video generation")
        )
    api_key = settings.aitunnel_api_key.get_secret_value()
    base_url = settings.aitunnel_base_url.rstrip("/")
    headers = {"Authorization": f"Bearer {api_key}"}
    upstream_model = _VIDEO_MODELS[req.model]

    # Balance precheck for real end-users (service-account calls skip it and are
    # billed to Omnia's own balance after the fact, like images).
    if req.user is not None:
        try:
            await billing.precheck_balance(req.user, _PRICE_CEILING_RUB)
        except WalletEmptyError as exc:
            raise _gateway_error_to_http(exc) from exc
        except Exception:
            log.exception("videos.precheck_failed", user=str(req.user))

    # ── 1) Create the task ────────────────────────────────────────────────
    create_payload: dict[str, Any] = {
        "model": upstream_model,
        "prompt": req.prompt,
        "size": _ASPECT_TO_SIZE.get(req.aspect, "1280x720"),
        "duration": req.duration,
    }
    # Keyframe images: Flux-made START and END stills → Kling interpolates the
    # motion between them. first_frame_url falls back to the legacy image_url.
    first_url = req.first_frame_url or req.image_url
    frames: list[dict[str, Any]] = []
    if first_url:
        frames.append({
            "type": "image_url",
            "image_url": {"url": first_url},
            "frame_type": "first_frame",
        })
    if req.last_frame_url:
        frames.append({
            "type": "image_url",
            "image_url": {"url": req.last_frame_url},
            "frame_type": "last_frame",
        })
    if frames:
        create_payload["frame_images"] = frames

    try:
        # retry=False: /v1/videos is non-idempotent (spawns a paid task) — never
        # blind-retry it (review 2026-07-17).
        create = await asyncio.to_thread(
            lambda: _request_sync(
                "POST", f"{base_url}/videos", headers, create_payload, retry=False
            )
        )
    except httpx.HTTPError as exc:
        raise _gateway_error_to_http(
            UpstreamProviderError(f"Video create transport error: {exc}")
        ) from exc
    if create.status_code in (401, 403):
        raise _gateway_error_to_http(
            ModelUnavailableError(f"Video provider auth failure ({create.status_code}): {create.text[:200]}")
        )
    if create.status_code >= 400:
        raise _gateway_error_to_http(
            UpstreamProviderError(f"Video provider {create.status_code}: {create.text[:300]}")
        )
    try:
        created = create.json()
    except Exception as exc:
        raise _gateway_error_to_http(
            UpstreamProviderError(f"Video provider returned non-JSON on create: {exc}")
        ) from exc
    task_id = created.get("id")
    if not task_id:
        raise _gateway_error_to_http(
            UpstreamProviderError(f"Video provider returned no task id: {str(created)[:200]}")
        )
    poll_url = created.get("polling_url") or f"{base_url}/videos/{task_id}"

    # ── 2) Poll until completed / failed / budget exhausted ───────────────
    deadline = time.monotonic() + _POLL_BUDGET_S
    final: dict[str, Any] | None = None
    while time.monotonic() < deadline:
        await asyncio.sleep(_POLL_INTERVAL_S)
        try:
            poll = await asyncio.to_thread(_request_sync, "GET", poll_url, headers, None)
        except httpx.HTTPError:
            continue  # transient — keep polling until the deadline
        if poll.status_code >= 400:
            # A hard error on the poll endpoint is terminal, not transient.
            raise _gateway_error_to_http(
                UpstreamProviderError(f"Video poll {poll.status_code}: {poll.text[:200]}")
            )
        try:
            body = poll.json()
        except Exception:
            continue
        status = str(body.get("status") or "").lower()
        if status == "completed":
            final = body
            break
        if status == "failed":
            raise _gateway_error_to_http(
                UpstreamProviderError(f"Video generation failed: {str(body.get('error') or body)[:200]}")
            )
        # pending / in_progress → keep waiting

    if final is None:
        raise _gateway_error_to_http(
            UpstreamProviderError(
                f"Video generation timed out after {_POLL_BUDGET_S:.0f}s (task {task_id})"
            )
        )

    # ── 3) Download the finished clip ─────────────────────────────────────
    urls = final.get("unsigned_urls") or []
    content_url = (urls[0] if urls else None) or f"{base_url}/videos/{task_id}/content?index=0"
    try:
        dl = await asyncio.to_thread(_request_sync, "GET", content_url, headers, None)
    except httpx.HTTPError as exc:
        raise _gateway_error_to_http(
            UpstreamProviderError(f"Video download transport error: {exc}")
        ) from exc
    if dl.status_code >= 400 or not dl.content:
        raise _gateway_error_to_http(
            UpstreamProviderError(f"Video download {dl.status_code}: {dl.text[:200]}")
        )

    b64_mp4 = base64.b64encode(dl.content).decode("ascii")
    content_type = dl.headers.get("content-type") or "video/mp4"

    # ── 4) Billing (provider-reported cost, else ceiling) ─────────────────
    usage = final.get("usage") or {}
    try:
        cost_rub = (
            Decimal(str(usage.get("cost_rub")))
            if usage.get("cost_rub") is not None
            else _PRICE_CEILING_RUB
        )
    except Exception:
        cost_rub = _PRICE_CEILING_RUB

    if req.user is not None:
        try:
            await billing.charge(
                user_id=req.user,
                project_id=None,
                message_id=None,
                model_id=req.model,
                tokens_in=0,
                tokens_out=0,
                cost_rub=cost_rub,
                description=f"Video gen ({req.model}, {req.duration}s)",
            )
        except WalletEmptyError as exc:
            raise _gateway_error_to_http(exc) from exc
        except Exception:
            log.exception("videos.charge_failed", user=str(req.user))

    try:
        file_logger.log_request(
            {
                "user_id": req.user,
                "project_id": None,
                "message_id": None,
                "model": req.model,
                "tokens_in": 0,
                "tokens_out": 1,
                "cost_rub": cost_rub,
                "cache_hit": False,
                "fallback_used": False,
                "stream": False,
            }
        )
    except Exception:
        log.exception("videos.file_log_failed")

    return {
        "b64_mp4": b64_mp4,
        "content_type": content_type,
        "metadata": {
            "actual_model_used": req.model,
            "cost_rub": str(cost_rub),
            "duration": req.duration,
            "aspect": req.aspect,
        },
    }
