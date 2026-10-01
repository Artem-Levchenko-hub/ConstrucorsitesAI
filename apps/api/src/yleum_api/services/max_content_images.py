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
from PIL import Image

from yleum_api.core.config import get_settings
from yleum_api.core.minio import ensure_public_bucket, get_minio_client

log = logging.getLogger(__name__)

MAX_CONTENT_IMAGE_BYTES = 8 * 1024 * 1024
MAX_CONTENT_IMAGE_PIXELS = 32_000_000
MAX_CONTENT_IMAGE_TOTAL_PIXELS = 64_000_000
MAX_CONTENT_IMAGE_FRAMES = 256
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


class ContentImageValidationError(ContentImageError):
    """The supplied bytes are not a complete supported image within decode limits."""


def _validate(data: bytes, content_type: str) -> None:
    if content_type not in CONTENT_IMAGE_TYPES or not 0 < len(data) <= MAX_CONTENT_IMAGE_BYTES:
        raise ContentImageValidationError("unsupported image or encoded size")
    formats = {"image/jpeg": "JPEG", "image/png": "PNG", "image/webp": "WEBP", "image/gif": "GIF"}
    if content_type == "image/gif" and not data.endswith(b";"):
        raise ContentImageValidationError("incomplete GIF")
    try:
        with Image.open(BytesIO(data), formats=list(formats.values())) as image:
            if image.format != formats[content_type]:
                raise ContentImageValidationError("declared image type differs from actual format")
            if image.width * image.height > MAX_CONTENT_IMAGE_PIXELS:
                raise ContentImageValidationError("image pixel limit exceeded")
            image.verify()
        # verify checks the container (including PNG CRCs); load every frame as
        # well, so a valid header or first GIF frame cannot conceal bad data.
        with Image.open(BytesIO(data), formats=list(formats.values())) as image:
            frames = 0
            total_pixels = 0
            while True:
                frames += 1
                pixels = image.width * image.height
                total_pixels += pixels
                if (
                    pixels > MAX_CONTENT_IMAGE_PIXELS
                    or total_pixels > MAX_CONTENT_IMAGE_TOTAL_PIXELS
                    or frames > MAX_CONTENT_IMAGE_FRAMES
                ):
                    raise ContentImageValidationError("image decode budget exceeded")
                image.load()
                try:
                    image.seek(frames)
                except EOFError:
                    break
    except ContentImageValidationError:
        raise
    except Exception as exc:
        raise ContentImageValidationError("invalid or oversized decoded image") from exc


def _optimized(data: bytes, content_type: str) -> tuple[bytes, str, str]:
    """Validate completely, then optimize; preserve validated GIF animation."""
    _validate(data, content_type)
    extension = CONTENT_IMAGE_TYPES[content_type]
    if content_type == "image/gif":
        return data, content_type, extension
    try:
        image: Image.Image = Image.open(BytesIO(data))
        image.load()
    except Exception as exc:
        # Decoder errors never take the optimization fallback into storage.
        raise ContentImageValidationError("image decode failed") from exc
    try:
        with image:
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
        # A codec/resize failure may retain only bytes already fully validated.
        log.warning("max_content_image: optimize failed type=%s", type(exc).__name__)
        return data, content_type, extension


def store_content_image(project_id: str, data: bytes, content_type: str) -> str:
    """Upload one catalog photo and return its public URL."""
    payload, stored_type, extension = _optimized(data, content_type)
    settings = get_settings()
    client = get_minio_client()
    bucket = settings.minio_bucket_images
    ensure_public_bucket(client, bucket)
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
