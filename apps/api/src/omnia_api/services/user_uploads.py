"""Sanitise + store a USER-uploaded image in MinIO (public-read ``omnia-images``
bucket, under ``uploads/``).

The bytes are re-encoded through Pillow from raw pixels, which (a) proves they
are a real raster image and (b) strips EXIF / ICC / any embedded payload. SVG is
rejected on purpose — it can carry script and the preview is served to the
browser. The returned URL is the SAME public form ``image_resolver`` produces,
so it drops straight into an ``<img src>`` via the image-patch endpoint.

No LLM, no gateway, no wallet — uploading your own image is free.
"""

from __future__ import annotations

import hashlib
from io import BytesIO

from PIL import Image

from omnia_api.core.config import get_settings
from omnia_api.core.minio import get_minio_client

# Browser-renderable raster formats we can safely re-encode. SVG excluded (XSS).
_FMT_TO_EXT = {"PNG": "png", "JPEG": "jpg", "WEBP": "webp"}
_MAX_DIM = 2560  # longest side — downscale bigger uploads (bounds storage/bw)
_MAX_BYTES = 6 * 1024 * 1024  # 6 MB raw ceiling


class UploadRejected(Exception):
    """The bytes are not a safe, supported raster image (→ 400)."""


def _ensure_bucket(client, bucket: str) -> None:
    """Create the bucket if missing. On prod ``omnia-images`` already exists and
    is public-read; this just covers a fresh environment."""
    try:
        if not client.bucket_exists(bucket):
            client.make_bucket(bucket)
    except Exception:  # noqa: BLE001 — best-effort; put_object surfaces real errors
        pass


def sanitize_and_upload(raw: bytes, project_id: str) -> str:
    """Validate → re-encode → store. Returns the public URL.

    Raises ``UploadRejected`` for anything that isn't a supported raster image
    (Pillow can't open it, unsupported format, empty, or over the size cap).
    """
    if not raw:
        raise UploadRejected("пустой файл")
    if len(raw) > _MAX_BYTES:
        raise UploadRejected("файл слишком большой (макс. 6 МБ)")

    try:
        img = Image.open(BytesIO(raw))
        img.load()
    except Exception as exc:  # noqa: BLE001 — any decode failure → reject
        raise UploadRejected("не похоже на изображение") from exc

    fmt = (img.format or "").upper()
    ext = _FMT_TO_EXT.get(fmt)
    if ext is None:
        raise UploadRejected(f"неподдерживаемый формат: {fmt or 'неизвестно'}")

    # Downscale oversized images (longest side).
    if max(img.size) > _MAX_DIM:
        img.thumbnail((_MAX_DIM, _MAX_DIM))

    # Re-encode from decoded pixels — strips metadata / embedded payloads.
    buf = BytesIO()
    if fmt == "JPEG":
        img.convert("RGB").save(buf, format="JPEG", quality=88, optimize=True)
        content_type = "image/jpeg"
    elif fmt == "WEBP":
        img.save(buf, format="WEBP", quality=90)
        content_type = "image/webp"
    else:  # PNG
        img.save(buf, format="PNG", optimize=True)
        content_type = "image/png"
    data = buf.getvalue()

    settings = get_settings()
    client = get_minio_client()
    bucket = settings.minio_bucket_images
    _ensure_bucket(client, bucket)
    sha = hashlib.sha256(data).hexdigest()[:32]
    key = f"uploads/{project_id}/{sha}.{ext}"
    client.put_object(
        bucket, key, BytesIO(data), length=len(data), content_type=content_type
    )
    base = settings.minio_public_url.rstrip("/")
    return f"{base}/{bucket}/{key}"


__all__ = ["sanitize_and_upload", "UploadRejected"]
