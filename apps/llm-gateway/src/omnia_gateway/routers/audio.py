"""POST /v1/audio/transcriptions — speech-to-text for voice prompt dictation.

Routes audio to proxyapi.ru/openai/v1/audio/transcriptions (whisper-1 /
gpt-4o-transcribe), the same proxy that fronts the GPT family — it's the only
RU-reachable STT surface (vsegpt exposes none; OpenAI's own host is blocked from
the prod box). Direct httpx, NOT LiteLLM — mirrors /v1/images/generations.

The audio arrives as a RAW body (apps/api forwards the recorded blob, no
python-multipart needed on the way in); we re-wrap it as multipart/form-data for
the OpenAI-compatible upstream via httpx `files=`. proxyapi is whitelisted in
NO_PROXY, so the shared async client is fine (no vsegpt sync-thread dance).

Billing is charge-after + best-effort: voice is a cheap INPUT affordance, so an
empty wallet must NEVER block dictation (unlike a paid generation). Abuse is
bounded by the api side (owner-scoped + rate-limited).
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any
from uuid import UUID

import httpx
import structlog
from fastapi import APIRouter, HTTPException, Request

from omnia_gateway.core.config import get_settings
from omnia_gateway.core.errors import (
    GatewayError,
    ModelUnavailableError,
    UpstreamProviderError,
)
from omnia_gateway.core.http import get_http
from omnia_gateway.services import billing, file_logger

router = APIRouter(prefix="/v1", tags=["audio"])
log = structlog.get_logger(__name__)

# whisper-1 ≈ $0.006/min; a dictated prompt is seconds. Flat per-call ceiling in
# RUB, charged AFTER success and best-effort (an empty wallet never blocks input).
_PRICE_PER_TRANSCRIBE_RUB = Decimal("0.50")
_TRANSCRIBE_TIMEOUT_SECONDS = 60.0
# OpenAI's own /audio upload cap. The api side enforces a tighter cap too.
_MAX_AUDIO_BYTES = 25 * 1024 * 1024


def _gateway_error_to_http(exc: GatewayError) -> HTTPException:
    return HTTPException(
        status_code=exc.http_status,
        detail={"error": {"code": exc.code, "message": exc.message, "details": exc.details}},
    )


def _filename_for(content_type: str) -> str:
    """Pick an extension the decoder recognises from the upload Content-Type."""
    for token, ext in (("webm", "webm"), ("ogg", "ogg"), ("mp4", "mp4"), ("mpeg", "mp3"), ("wav", "wav")):
        if token in content_type:
            return f"audio.{ext}"
    return "audio.webm"


@router.post("/audio/transcriptions")
async def audio_transcriptions(
    request: Request,
    user: UUID | None = None,
    language: str = "ru",
    model: str | None = None,
) -> dict[str, Any]:
    settings = get_settings()
    if settings.proxyapi_api_key is None:
        raise _gateway_error_to_http(
            ModelUnavailableError("proxyapi_api_key not configured for transcription")
        )

    audio = await request.body()
    if not audio:
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": "empty_audio", "message": "empty audio body"}},
        )
    if len(audio) > _MAX_AUDIO_BYTES:
        raise HTTPException(
            status_code=413,
            detail={"error": {"code": "audio_too_large", "message": f"audio exceeds {_MAX_AUDIO_BYTES} bytes"}},
        )

    upstream_model = model or settings.transcribe_model
    content_type = request.headers.get("content-type") or "audio/webm"
    url = f"{settings.proxyapi_openai_base_url.rstrip('/')}/audio/transcriptions"
    headers = {"Authorization": f"Bearer {settings.proxyapi_api_key.get_secret_value()}"}
    files = {"file": (_filename_for(content_type), audio, content_type)}
    data = {"model": upstream_model, "language": language, "response_format": "json"}

    try:
        resp = await get_http().post(
            url, files=files, data=data, headers=headers, timeout=_TRANSCRIBE_TIMEOUT_SECONDS
        )
    except httpx.TimeoutException as exc:
        raise _gateway_error_to_http(
            UpstreamProviderError(f"Transcription timed out: {exc}")
        ) from exc
    except httpx.RequestError as exc:
        raise _gateway_error_to_http(
            UpstreamProviderError(f"Transcription transport error: {exc}")
        ) from exc

    if resp.status_code in (401, 403):
        raise _gateway_error_to_http(
            ModelUnavailableError(
                f"Transcription auth failure ({resp.status_code}): {resp.text[:200]}"
            )
        )
    if resp.status_code >= 400:
        raise _gateway_error_to_http(
            UpstreamProviderError(f"Transcription provider {resp.status_code}: {resp.text[:300]}")
        )

    try:
        body = resp.json()
    except Exception as exc:
        raise _gateway_error_to_http(
            UpstreamProviderError(f"Transcription returned non-JSON: {exc}")
        ) from exc

    text = str(body.get("text") or "").strip()
    charged = Decimal("0")

    # Charge-after, best-effort: voice must not be wallet-gated. Swallow ANY billing
    # error (incl. empty wallet) — the transcription still returns.
    if user is not None and text:
        try:
            await billing.charge(
                user_id=user,
                project_id=None,
                message_id=None,
                model_id=upstream_model,
                tokens_in=0,
                tokens_out=0,
                cost_rub=_PRICE_PER_TRANSCRIBE_RUB,
                description="Speech-to-text (voice prompt)",
            )
            charged = _PRICE_PER_TRANSCRIBE_RUB
        except Exception:
            log.warning("audio.charge_skipped", user=str(user))

    try:
        file_logger.log_request(
            {
                "user_id": user,
                "project_id": None,
                "message_id": None,
                "model": upstream_model,
                "tokens_in": 0,
                "tokens_out": 0,
                "cost_rub": charged,
                "cache_hit": False,
                "fallback_used": False,
                "stream": False,
            }
        )
    except Exception:
        log.exception("audio.file_log_failed")

    return {"text": text, "metadata": {"model": upstream_model, "cost_rub": str(charged)}}
