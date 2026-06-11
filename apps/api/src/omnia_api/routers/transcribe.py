"""POST /api/transcribe — speech-to-text for voice prompt dictation.

Owner-scoped (logged-in) + rate-limited. Reads the recorded audio as a RAW body
(no python-multipart, like uploads.py), forwards it to the LLM gateway's
``/v1/audio/transcriptions`` (proxyapi whisper, RU-reachable), and returns
``{"text": ...}`` for the prompt box. Review-first by design: the client drops
the text into the textarea so the user edits + sends — a misheard word never
auto-fires an expensive build.
"""

from __future__ import annotations

import httpx
from fastapi import APIRouter, Request, status

from omnia_api.core.config import get_settings
from omnia_api.core.deps import CurrentUserDep
from omnia_api.core.errors import ApiError
from omnia_api.core.ratelimit import limiter

router = APIRouter(prefix="/api", tags=["transcribe"])

# ~ a couple of minutes of opus/webm — a dictated prompt, not a podcast.
_MAX_AUDIO_BYTES = 12 * 1024 * 1024


@router.post("/transcribe")
@limiter.limit(lambda: get_settings().prompt_rate_limit)
async def transcribe(request: Request, current_user: CurrentUserDep) -> dict[str, str]:
    raw = await request.body()
    if not raw:
        raise ApiError("validation_failed", "пустая запись", status.HTTP_400_BAD_REQUEST)
    if len(raw) > _MAX_AUDIO_BYTES:
        raise ApiError(
            "validation_failed",
            "запись слишком длинная (макс. 12 МБ)",
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
        )

    settings = get_settings()
    url = f"{settings.llm_gateway_url.rstrip('/')}/v1/audio/transcriptions"
    content_type = request.headers.get("content-type") or "audio/webm"
    timeout = httpx.Timeout(90.0, connect=5.0, read=90.0)
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(
                url,
                content=raw,
                params={"user": str(current_user.id), "language": "ru"},
                headers={"Content-Type": content_type},
            )
    except httpx.HTTPError as exc:
        raise ApiError(
            "model_unavailable", "не удалось распознать речь", status.HTTP_502_BAD_GATEWAY
        ) from exc

    if resp.status_code >= 400:
        raise ApiError(
            "model_unavailable", "не удалось распознать речь", status.HTTP_502_BAD_GATEWAY
        )
    try:
        data = resp.json()
    except Exception as exc:
        raise ApiError(
            "model_unavailable", "распознавание вернуло некорректный ответ",
            status.HTTP_502_BAD_GATEWAY,
        ) from exc
    return {"text": str(data.get("text") or "")}
