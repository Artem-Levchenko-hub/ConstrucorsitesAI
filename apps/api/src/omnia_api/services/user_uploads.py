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
from dataclasses import dataclass
from io import BytesIO

from minio import Minio
from PIL import Image, ImageOps

from omnia_api.core.config import get_settings
from omnia_api.core.minio import get_minio_client

# Browser-renderable raster formats we can safely decode. SVG excluded (XSS).
_SUPPORTED_FORMATS = {"PNG", "JPEG", "WEBP"}
_MAX_DIM = 2560  # longest side — downscale bigger uploads (bounds storage/bw)
_MAX_BYTES = 6 * 1024 * 1024  # 6 MB raw ceiling
_WEBP_QUALITY = 88


class UploadRejected(Exception):
    """The bytes are not a safe, supported raster image (→ 400)."""


@dataclass(frozen=True)
class UploadedAsset:
    url: str
    storage_key: str
    mime_type: str
    width: int
    height: int
    bytes_size: int


def _ensure_bucket(client: Minio, bucket: str) -> None:
    """Create the bucket if missing. On prod ``omnia-images`` already exists and
    is public-read; this just covers a fresh environment."""
    try:
        if not client.bucket_exists(bucket):
            client.make_bucket(bucket)
    except Exception:  # Best-effort; put_object surfaces real errors.
        pass


def sanitize_and_upload(raw: bytes, project_id: str) -> str:
    """Validate → re-encode → store. Returns only the public URL.

    Backward-compatible wrapper for the original manual image upload flow.
    """
    return sanitize_and_upload_record(raw, project_id).url


def sanitize_and_upload_record(raw: bytes, project_id: str) -> UploadedAsset:
    """Validate → re-encode → store, returning full asset metadata.

    Raises ``UploadRejected`` for anything that isn't a supported raster image
    (Pillow can't open it, unsupported format, empty, or over the size cap).
    """
    data, width, height = _sanitize_image(raw)
    ext = "webp"
    content_type = "image/webp"

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
    return UploadedAsset(
        url=f"{base}/{bucket}/{key}",
        storage_key=key,
        mime_type=content_type,
        width=width,
        height=height,
        bytes_size=len(data),
    )


def _sanitize_image(raw: bytes) -> tuple[bytes, int, int]:
    """Decode untrusted raster bytes and return a metadata-free WebP."""
    if not raw:
        raise UploadRejected("пустой файл")
    if len(raw) > _MAX_BYTES:
        raise UploadRejected("файл слишком большой (макс. 6 МБ)")

    try:
        img = Image.open(BytesIO(raw))
        img.load()
    except Exception as exc:  # Any decode failure is rejected.
        raise UploadRejected("не похоже на изображение") from exc

    fmt = (img.format or "").upper()
    if fmt not in _SUPPORTED_FORMATS:
        raise UploadRejected(f"неподдерживаемый формат: {fmt or 'неизвестно'}")

    normalized = ImageOps.exif_transpose(img)
    if max(normalized.size) > _MAX_DIM:
        normalized.thumbnail((_MAX_DIM, _MAX_DIM), Image.Resampling.LANCZOS)

    has_alpha = normalized.mode in {"RGBA", "LA"} or (
        normalized.mode == "P" and "transparency" in normalized.info
    )
    clean = normalized.convert("RGBA" if has_alpha else "RGB")
    buf = BytesIO()
    clean.save(
        buf,
        format="WEBP",
        quality=_WEBP_QUALITY,
        method=6,
        exact=has_alpha,
    )
    return buf.getvalue(), int(clean.width), int(clean.height)


__all__ = [
    "UploadRejected",
    "UploadedAsset",
    "sanitize_and_upload",
    "sanitize_and_upload_record",
]
