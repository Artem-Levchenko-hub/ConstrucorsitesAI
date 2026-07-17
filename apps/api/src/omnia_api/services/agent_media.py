"""Agent-facing media generation — the native builder's ``generate_media`` tool.

The native agent calls this DIRECTLY mid-build to get a real hosted asset it can
embed, on the SAME aitunnel key as everything else:

  * ``kind="image"`` → flux (via ``image_resolver.generate_and_store_image``) —
    a photoreal still for a hero / section, ~5s.
  * ``kind="video"`` → Kling short clip (via the gateway
    ``/v1/videos/generations`` async task). The SIGNATURE move is keyframe
    interpolation: give it a ``first_frame`` prompt AND a ``last_frame`` prompt
    and Flux paints both stills while Kling generates the UNIQUE motion between
    them — a real fly-through ("летишь по острову при скролле"), not a generic
    text-to-video loop. Each stage (first frame → last frame → Kling stitch) is
    surfaced as its OWN live transcript sub-step via ``emit`` so the user SEES it
    happening.

Both return a public MinIO URL under ``url`` AND inside ``content`` (the model
only reads content/detail/error from a tool result — the bare ``url`` key would
be invisible to it, review 2026-07-17), so the agent can drop it into
``<img src>`` / ``<video src>``.

Deep module (R-01): callers hand over ``(project_id, kind, prompt)`` and get back
``{"ok", "url"|"error", "content", "kind"}``. Every failure is caught and returned
as ``{"ok": False, "error": ...}`` — a missing asset must NEVER crash the build
loop; the agent reads the reason and moves on (fail-soft, R-10)."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
from collections.abc import Awaitable, Callable
from io import BytesIO

import httpx
from minio.error import S3Error

from omnia_api.core.config import get_settings
from omnia_api.core.minio import get_minio_client
from omnia_api.services import image_resolver

log = logging.getLogger(__name__)

# The gateway create→poll→download runs up to its poll budget (240s) PLUS create
# and clip download, so the client wait must sit above that whole envelope — a
# real gateway hang then surfaces as a clean read timeout here, not a socket
# teardown that hides the cause AND discards a paid clip (review 2026-07-17).
_VIDEO_CLIENT_TIMEOUT = 360.0

# Emit-callback type: (event_name, data) → awaitable. Same shape messages.py's
# _agent_emit consumes; ``human`` in data becomes the visible step phrase.
Emit = Callable[[str, dict[str, object]], Awaitable[None]]


def _public_read_policy(bucket: str) -> str:
    """Anonymous ``s3:GetObject`` policy JSON for ``bucket`` — the browser must be
    able to GET a clip by its plain public URL (no presign)."""
    return json.dumps(
        {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Principal": {"AWS": ["*"]},
                    "Action": ["s3:GetObject"],
                    "Resource": [f"arn:aws:s3:::{bucket}/*"],
                }
            ],
        }
    )


def _ensure_video_bucket(client, bucket: str) -> bool:
    """Make ``bucket`` if absent and (RE)ASSERT its public-read policy EVERY call.

    Re-asserting on every upload (not only at creation) self-heals a bucket that
    was pre-created without a policy or whose one-time policy-set transiently
    failed — otherwise every clip would 403 in the browser forever with no repair
    (review 2026-07-17). Returns False only if the bucket itself is unusable."""
    try:
        if not client.bucket_exists(bucket):
            client.make_bucket(bucket)
    except S3Error as exc:
        log.warning("agent_media: video bucket-ensure failed %s err=%r", bucket, exc)
        return False
    try:
        client.set_bucket_policy(bucket, _public_read_policy(bucket))
    except S3Error as exc:
        # A failed policy re-assert is non-fatal for THIS upload (bucket may
        # already be public from infra/minio-init); log so a genuine 403 is
        # traceable, but still let the upload proceed.
        log.warning("agent_media: video bucket policy re-assert failed %s err=%r", bucket, exc)
    return True


def _cached_video_url(project_id: str, cache_key: str) -> str | None:
    """Public URL of an ALREADY-generated clip for ``(project_id, cache_key)``, or
    None. Same content-addressing as ``_upload_video`` — a re-run / repair reuses
    the MinIO object instead of paying Kling again. Fail-soft → None."""
    settings = get_settings()
    try:
        client = get_minio_client()
        bucket = settings.minio_bucket_videos
        sha = hashlib.sha256(f"{project_id}|{cache_key}".encode("utf-8")).hexdigest()[:32]
        client.stat_object(bucket, f"{project_id}/{sha}.mp4")
    except Exception:  # noqa: BLE001 — absent/transport → regenerate
        return None
    base = settings.minio_public_url.rstrip("/")
    return f"{base}/{bucket}/{project_id}/{sha}.mp4"


def _upload_video(data: bytes, project_id: str, cache_key: str) -> str | None:
    """Store an mp4 in MinIO, content-addressed by ``(project_id, cache_key)`` so
    an identical request costs one generation + one object. Returns the public
    URL or None on any bucket/upload error (caller degrades)."""
    settings = get_settings()
    client = get_minio_client()
    bucket = settings.minio_bucket_videos
    if not _ensure_video_bucket(client, bucket):
        return None
    sha = hashlib.sha256(f"{project_id}|{cache_key}".encode("utf-8")).hexdigest()[:32]
    key = f"{project_id}/{sha}.mp4"
    try:
        client.put_object(
            bucket, key, BytesIO(data), length=len(data), content_type="video/mp4"
        )
    except S3Error as exc:
        log.warning("agent_media: video upload failed key=%s err=%r", key, exc)
        return None
    base = settings.minio_public_url.rstrip("/")
    return f"{base}/{bucket}/{key}"


async def _emit_sub(emit: Emit | None, step: int | None, human: str, detail: str = "") -> None:
    """Publish one live transcript SUB-step (fail-soft). ``human`` is the visible
    phrase; messages.py._agent_emit honours it verbatim."""
    if emit is None:
        return
    try:
        await emit(
            "agent.step",
            {"step": step, "human": human, "tool": "generate_media", "path": "",
             "detail": detail, "ok": True},
        )
    except Exception:  # noqa: BLE001 — a progress event must never break a gen
        pass


async def _gen_frame(
    project_id: str, prompt: str, emit: Emit | None, step: int | None, human: str
) -> str | None:
    """Emit a live sub-step, then generate ONE Flux still and return its URL."""
    await _emit_sub(emit, step, human, prompt[:80])
    return await image_resolver.generate_and_store_image(project_id, prompt)


async def _generate_video(
    project_id: str,
    prompt: str,
    *,
    duration: int,
    aspect: str,
    first_frame_url: str | None,
    last_frame_url: str | None,
    emit: Emit | None,
    step: int | None,
) -> dict[str, object]:
    settings = get_settings()
    if not settings.use_video_gen:
        return {"ok": False, "error": "video generation disabled (USE_VIDEO_GEN=false)"}

    model = settings.video_gen_model
    cache_key = f"{model}|{duration}|{aspect}|{first_frame_url or ''}|{last_frame_url or ''}|{prompt}"
    cached = await asyncio.to_thread(_cached_video_url, project_id, cache_key)
    if cached:
        return {"ok": True, "url": cached, "kind": "video",
                "content": f"Готовое видео уже есть. Вставь его: <video src=\"{cached}\" autoPlay muted loop playsInline />. URL: {cached}",
                "detail": f"переиспользую готовый клип · {cached}"}

    await _emit_sub(emit, step, "Собираю видео из кадров (ИИ-видео)", f"{model} · {duration}с")
    url = f"{settings.llm_gateway_url.rstrip('/')}/v1/videos/generations"
    payload: dict[str, object] = {
        "model": model,
        "prompt": prompt,
        "duration": duration,
        "aspect": aspect,
    }
    if first_frame_url:
        payload["first_frame_url"] = first_frame_url
    if last_frame_url:
        payload["last_frame_url"] = last_frame_url

    try:
        async with httpx.AsyncClient(timeout=_VIDEO_CLIENT_TIMEOUT) as client:
            resp = await client.post(url, json=payload)
    except Exception as exc:  # noqa: BLE001 — never crash the build over a clip
        return {"ok": False, "error": f"video gateway transport: {type(exc).__name__}: {exc}"}
    if resp.status_code >= 400:
        return {"ok": False, "error": f"video gateway {resp.status_code}: {resp.text[:300]}"}
    try:
        body = resp.json()
    except ValueError:
        return {"ok": False, "error": "video gateway returned non-JSON"}
    b64 = body.get("b64_mp4")
    if not b64:
        return {"ok": False, "error": "video gateway returned no clip data"}
    try:
        data = base64.b64decode(b64)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"video b64 decode failed: {exc}"}

    stored = await asyncio.to_thread(_upload_video, data, project_id, cache_key)
    if not stored:
        return {"ok": False, "error": "video MinIO upload failed"}
    return {
        "ok": True,
        "url": stored,
        "kind": "video",
        # content is what the MODEL reads back (url alone is invisible to it):
        "content": f"Видео готово. Вставь его: <video src=\"{stored}\" autoPlay muted loop playsInline /> (или скролл-скраб через currentTime). URL: {stored}",
        "detail": f"{model} {duration}с · {stored}",
    }


async def generate_media(
    project_id: str,
    *,
    kind: str,
    prompt: str,
    duration: int | None = None,
    aspect: str | None = None,
    first_frame: str | None = None,
    last_frame: str | None = None,
    first_frame_url: str | None = None,
    last_frame_url: str | None = None,
    image_url: str | None = None,
    emit: Emit | None = None,
    step: int | None = None,
) -> dict[str, object]:
    """Generate one image or (cinematic) video and return a public URL to embed.

    Video with ``first_frame``/``last_frame`` PROMPTS runs the keyframe pipeline:
    Flux paints each still (live sub-step) → Kling interpolates between them.
    Pre-made frame URLs (``first_frame_url``/``last_frame_url``) skip the Flux
    step. Returns ``{"ok": True, "url", "content", "kind"}`` on success, else
    ``{"ok": False, "error"}`` — the ``content`` carries the URL so the model
    actually receives it.
    """
    prompt = (prompt or "").strip()
    if not prompt:
        return {"ok": False, "error": "generate_media needs a non-empty prompt"}
    kind = (kind or "image").strip().lower()

    if kind == "image":
        url = await image_resolver.generate_and_store_image(project_id, prompt)
        if not url:
            return {"ok": False, "error": "image generation failed (see gateway logs)"}
        return {
            "ok": True,
            "url": url,
            "kind": "image",
            "content": f"Картинка готова. Вставь её: <img src=\"{url}\" alt=\"...\" />. URL: {url}",
            "detail": url,
        }

    if kind == "video":
        dur = int(duration) if isinstance(duration, (int, float)) else 5
        dur = max(3, min(10, dur))
        asp = aspect if aspect in ("16:9", "9:16", "1:1") else "16:9"

        # Resolve keyframes: a PROMPT means "paint it with Flux now" (visible
        # sub-step); a *_url means it's already made. first_frame falls back to
        # image_url for the legacy single-still animate-from case.
        ff_url = first_frame_url
        if first_frame and not ff_url:
            ff_url = await _gen_frame(
                project_id, first_frame, emit, step, "Рисую первый кадр (Flux)")
            if not ff_url:
                return {"ok": False, "error": "first-frame image generation failed"}
        ff_url = ff_url or image_url

        lf_url = last_frame_url
        if last_frame and not lf_url:
            lf_url = await _gen_frame(
                project_id, last_frame, emit, step, "Рисую последний кадр (Flux)")
            if not lf_url:
                return {"ok": False, "error": "last-frame image generation failed"}

        return await _generate_video(
            project_id, prompt, duration=dur, aspect=asp,
            first_frame_url=ff_url, last_frame_url=lf_url, emit=emit, step=step,
        )

    return {"ok": False, "error": f"unknown media kind '{kind}' (use 'image' or 'video')"}
