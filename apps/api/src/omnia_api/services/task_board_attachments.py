"""Private MinIO storage for task-board attachments."""

from __future__ import annotations

import io
import logging
from pathlib import PurePath
from typing import Any
from uuid import UUID

from minio.error import S3Error

from omnia_api.core.config import get_settings
from omnia_api.core.minio import get_minio_client

log = logging.getLogger(__name__)


class AttachmentStorageError(RuntimeError):
    """Raised when private attachment storage cannot complete an operation."""


def _ensure_private_bucket() -> tuple[Any, str]:
    settings = get_settings()
    client = get_minio_client()
    bucket = settings.minio_bucket_task_board
    reserved_buckets = {
        settings.minio_bucket_projects,
        settings.minio_bucket_previews,
        settings.minio_bucket_images,
        settings.minio_bucket_photos,
        "omnia-videos",
    }
    if bucket in reserved_buckets:
        raise AttachmentStorageError("attachment bucket must be dedicated")
    try:
        if not client.bucket_exists(bucket):
            try:
                client.make_bucket(bucket)
            except S3Error as exc:
                # A concurrent worker may have created it between both calls.
                if not client.bucket_exists(bucket):
                    raise AttachmentStorageError("attachment bucket is unavailable") from exc
        try:
            # Fail closed if an operator accidentally made the bucket public.
            client.delete_bucket_policy(bucket)
        except S3Error as exc:
            if exc.code != "NoSuchBucketPolicy":
                raise
    except AttachmentStorageError:
        raise
    except Exception as exc:
        try:
            if not client.bucket_exists(bucket):
                raise AttachmentStorageError("attachment bucket is unavailable") from exc
        except Exception as verify_exc:
            raise AttachmentStorageError("attachment bucket is unavailable") from verify_exc
        raise AttachmentStorageError("attachment bucket privacy check failed") from exc
    return client, bucket


def store_attachment(
    task_id: UUID,
    attachment_id: UUID,
    filename: str,
    content_type: str,
    raw: bytes,
) -> str:
    client, bucket = _ensure_private_bucket()
    suffix = PurePath(filename).suffix.lower()[:16]
    object_key = f"tasks/{task_id}/{attachment_id}{suffix}"
    try:
        client.put_object(
            bucket,
            object_key,
            io.BytesIO(raw),
            len(raw),
            content_type=content_type,
        )
    except Exception as exc:
        raise AttachmentStorageError("attachment upload failed") from exc
    return object_key


def load_attachment(object_key: str) -> Any | None:
    settings = get_settings()
    try:
        return get_minio_client().get_object(settings.minio_bucket_task_board, object_key)
    except S3Error as exc:
        if exc.code == "NoSuchKey":
            return None
        raise AttachmentStorageError("attachment download failed") from exc
    except Exception as exc:
        raise AttachmentStorageError("attachment download failed") from exc


def delete_attachment(object_key: str) -> None:
    settings = get_settings()
    try:
        get_minio_client().remove_object(settings.minio_bucket_task_board, object_key)
    except Exception as exc:
        log.warning("task_board_attachment_delete_failed key=%s err=%r", object_key, exc)
        raise AttachmentStorageError("attachment delete failed") from exc
