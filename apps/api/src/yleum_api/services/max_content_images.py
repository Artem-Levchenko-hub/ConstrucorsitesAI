"""Owner-uploaded catalog photos for MAX apps.

A catalog item without a picture reads as a line in a list, not as a product, so
the Content tab accepts a photo. The bytes go to the SAME public MinIO bucket the
generated sites already use (``minio_bucket_images``), because the picture has to
load for every visitor of the published app — including from another host — with
no signature and no session.

Keys are content-addressed (``max-content/<project>/<sha>.<ext>``) so re-uploading
the same file twice costs one object, and deleting an item never orphans a photo
another item still shows.
"""

from __future__ import annotations

import hashlib
import logging
from io import BytesIO

from minio.error import S3Error

from yleum_api.core.config import get_settings
from yleum_api.core.minio import ensure_public_bucket, get_minio_client

log = logging.getLogger(__name__)

MAX_CONTENT_IMAGE_BYTES = 8 * 1024 * 1024
# Formats a browser renders in <img> without a plugin. SVG is excluded on
# purpose: it is a script-carrying document, and it would be served from our
# own public origin.
CONTENT_IMAGE_TYPES = {
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
    "image/gif": "gif",
}
_MAX_WIDTH = 1280
_WEBP_QUALITY = 82


class ContentImageError(RuntimeError):
    """Storage refused the photo; the owner keeps the item and can retry."""


def _optimized(data: bytes, content_type: str) -> tuple[bytes, str, str]:
    """WebP re-encode at most 1280px wide, or the original when that is no win.

    A phone photo is several megabytes; inside a MAX mini app it is shown in a
    card a few hundred pixels wide. Fail-soft: any decode problem keeps the
    original bytes — a heavier picture is better than a lost one.
    """
    extension = CONTENT_IMAGE_TYPES[content_type]
    if content_type == "image/gif":
        return data, content_type, extension  # animation must survive
    try:
        from PIL import Image
    except Exception:
        return data, content_type, extension
    try:
        image: Image.Image = Image.open(BytesIO(data))
        image.load()
        width, height = image.size
        if width > _MAX_WIDTH:
            image = image.resize(
                (_MAX_WIDTH, max(1, round(height * _MAX_WIDTH / width))),
                Image.Resampling.LANCZOS,
            )
        if image.mode in ("RGBA", "P", "LA"):
            image = image.convert("RGB")
        buffer = BytesIO()
        image.save(buffer, "WEBP", quality=_WEBP_QUALITY, method=6)
        encoded = buffer.getvalue()
        if not encoded or len(encoded) >= len(data):
            return data, content_type, extension
        return encoded, "image/webp", "webp"
    except Exception as exc:
        log.warning("max_content_image: optimize failed err=%r — using original", exc)
        return data, content_type, extension


def store_content_image(project_id: str, data: bytes, content_type: str) -> str:
    """Upload one catalog photo and return its public URL."""
    if content_type not in CONTENT_IMAGE_TYPES:
        raise ContentImageError("unsupported content type")
    settings = get_settings()
    client = get_minio_client()
    bucket = settings.minio_bucket_images
    ensure_public_bucket(client, bucket)
    payload, stored_type, extension = _optimized(data, content_type)
    digest = hashlib.sha256(payload).hexdigest()[:32]
    key = f"max-content/{project_id}/{digest}.{extension}"
    try:
        client.put_object(
            bucket,
            key,
            BytesIO(payload),
            length=len(payload),
            content_type=stored_type,
        )
    except S3Error as exc:
        log.warning("max_content_image: upload failed key=%s err=%r", key, exc)
        raise ContentImageError("upload failed") from exc
    return f"{settings.minio_public_url.rstrip('/')}/{bucket}/{key}"
