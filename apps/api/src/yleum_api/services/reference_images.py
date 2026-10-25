"""Private immutable visual references, resolved only through owner-scoped IDs."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import json
import warnings
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from minio.error import S3Error
from PIL import Image
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from yleum_api.core.errors import ApiError
from yleum_api.models.message import Message
from yleum_api.models.project import Project
from yleum_api.models.reference_image import ReferenceImage
from yleum_api.models.task_board import TaskBoardAttachmentCleanup
from yleum_api.services.private_attachment_quota import (
    PRIVATE_BUCKET_BYTES_LIMIT,
    lock_private_attachment_quota,
    private_attachment_bytes,
)
from yleum_api.services.task_board_attachments import _ensure_private_bucket

MAX_BYTES = 5 * 1024 * 1024
MAX_TOTAL_BYTES = 12 * 1024 * 1024
MAX_IMAGES = 4
MAX_USER_BYTES = 256 * 1024 * 1024
FORMATS = {"image/png": "PNG", "image/jpeg": "JPEG", "image/webp": "WEBP"}


def validate_image(raw: bytes, mime_type: str) -> tuple[int, int]:
    if mime_type not in FORMATS or not 0 < len(raw) <= MAX_BYTES:
        raise ApiError("validation_failed", "Допустимы PNG, JPEG и WebP до 5 МБ", 422)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(raw), formats=list(FORMATS.values())) as image:
                if (
                    image.format != FORMATS[mime_type]
                    or max(image.size) > 8000
                    or image.width * image.height > 20_000_000
                    or getattr(image, "n_frames", 1) != 1
                ):
                    raise ValueError("image limits")
                dimensions = image.size
                image.verify()
            with Image.open(io.BytesIO(raw), formats=[FORMATS[mime_type]]) as image:
                image.load()
        return dimensions
    except Exception:
        raise ApiError(
            "validation_failed",
            "Изображение повреждено, анимировано или слишком большое",
            422,
        ) from None


def _storage() -> tuple[Any, str]:
    client, bucket = _ensure_private_bucket()
    # Referenced objects must outlive recovery/history. Refuse a bucket with any
    # enabled expiration policy instead of silently accepting expiring uploads.
    try:
        lifecycle = client.get_bucket_lifecycle(bucket)
    except S3Error as exc:
        if exc.code != "NoSuchLifecycleConfiguration":
            raise
    else:
        if lifecycle is not None and any(
            rule.status == "Enabled"
            and (rule.expiration is not None or rule.noncurrent_version_expiration is not None)
            for rule in lifecycle.rules
        ):
            raise RuntimeError("private reference storage retention unavailable")
    return client, bucket


def _store(key: str, raw: bytes, mime_type: str) -> None:
    client, bucket = _storage()
    client.put_object(bucket, key, io.BytesIO(raw), len(raw), content_type=mime_type)


def _load(row: ReferenceImage) -> bytes:
    client, bucket = _storage()
    response = client.get_object(bucket, row.object_key)
    try:
        raw = response.read(MAX_BYTES + 1)
    finally:
        response.close()
        response.release_conn()
    if (
        not isinstance(raw, bytes)
        or len(raw) != row.size_bytes
        or hashlib.sha256(raw).hexdigest() != row.sha256
    ):
        raise RuntimeError("reference image integrity failed")
    return raw


def public_image(row: ReferenceImage) -> dict[str, object]:
    return {
        "id": str(row.id),
        "filename": row.filename,
        "mime_type": row.mime_type,
        "size_bytes": row.size_bytes,
        "width": row.width,
        "height": row.height,
        "sha256": row.sha256,
        "url": f"/api/projects/{row.project_id}/reference-images/{row.id}",
    }


async def create_image(
    session: AsyncSession,
    project_id: UUID,
    user_id: UUID,
    raw: bytes,
    mime_type: str,
    filename: str,
) -> ReferenceImage:
    width, height = await asyncio.to_thread(validate_image, raw, mime_type)
    await lock_private_attachment_quota(session)
    user_bytes = await session.scalar(
        select(func.coalesce(func.sum(ReferenceImage.size_bytes), 0)).where(
            ReferenceImage.uploader_id == user_id
        )
    )
    if (
        int(user_bytes or 0) + len(raw) > MAX_USER_BYTES
        or await private_attachment_bytes(session) + len(raw) > PRIVATE_BUCKET_BYTES_LIMIT
    ):
        raise ApiError("too_large", "Хранилище изображений заполнено", 413)
    image_id = uuid4()
    row = ReferenceImage(
        id=image_id,
        project_id=project_id,
        uploader_id=user_id,
        object_key=f"reference-images/{project_id}/{image_id}",
        filename=filename.replace("\\", "/").rsplit("/", 1)[-1][:180] or "reference",
        mime_type=mime_type,
        size_bytes=len(raw),
        width=width,
        height=height,
        sha256=hashlib.sha256(raw).hexdigest(),
    )
    session.add(row)
    # Reserve bytes durably before PUT. A crash/unknown object outcome cannot
    # evade quota by rolling back metadata. Stale pending rows enter cleanup.
    await session.commit()
    await session.refresh(row, with_for_update=True)
    try:
        await asyncio.to_thread(_store, row.object_key, raw, mime_type)
        row.status = "ready"
        await session.commit()
    except Exception:
        await session.rollback()
        raise ApiError(
            "upload_failed",
            "Не удалось сохранить изображение. Повторите загрузку",
            503,
        ) from None
    return row


async def resolve_images(
    session: AsyncSession,
    project_id: UUID,
    user_id: UUID,
    image_ids: list[UUID],
    *,
    bind: bool = False,
) -> list[ReferenceImage]:
    if len(image_ids) > MAX_IMAGES or len(set(image_ids)) != len(image_ids):
        raise ApiError("validation_failed", "Не более четырёх разных изображений", 422)
    if not image_ids:
        return []
    statement = (
        select(ReferenceImage)
        .join(Project)
        .where(
            ReferenceImage.id.in_(image_ids),
            ReferenceImage.project_id == project_id,
            ReferenceImage.uploader_id == user_id,
            ReferenceImage.status == "ready",
            Project.owner_id == user_id,
        )
    )
    if bind:
        statement = statement.with_for_update(of=ReferenceImage)
    rows = (await session.execute(statement)).scalars().all()
    by_id = {row.id: row for row in rows}
    if set(by_id) != set(image_ids):
        raise ApiError("not_found", "Изображение не найдено", 404)
    ordered = [by_id[image_id] for image_id in image_ids]
    if sum(row.size_bytes for row in ordered) > MAX_TOTAL_BYTES:
        raise ApiError(
            "validation_failed", "Общий размер изображений не должен превышать 12 МБ", 422
        )
    if bind:
        for row in ordered:
            if not row.is_bound:
                row.bound_at = datetime.now(UTC)
    return ordered


async def enqueue_expired_reference_images(session: AsyncSession, limit: int = 20) -> None:
    """Reclaim only unpublished drafts or deleted projects, never accepted history.

    Pending uploads retain a 24h grace and a row lock across their synchronous
    PUT; a live uploader is skipped. Metadata stays charged until deletion succeeds.
    """
    stale = func.now() - timedelta(hours=24)
    rows = (
        await session.scalars(
            select(ReferenceImage)
            .where(
                or_(
                    and_(ReferenceImage.status == "pending", ReferenceImage.created_at < stale),
                    and_(
                        ReferenceImage.status == "ready",
                        or_(
                            ReferenceImage.project_id.is_(None),
                            ReferenceImage.uploader_id.is_(None),
                            and_(
                                ReferenceImage.is_bound.is_(False),
                                func.coalesce(ReferenceImage.bound_at, ReferenceImage.created_at)
                                < stale,
                            ),
                        ),
                    ),
                )
            )
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
    ).all()
    for row in rows:
        row.status = "deleting"
        session.add(TaskBoardAttachmentCleanup(object_key=row.object_key, size=row.size_bytes))
    await session.flush()


async def image_bytes(row: ReferenceImage) -> bytes:
    try:
        return await asyncio.to_thread(_load, row)
    except Exception:
        raise ApiError("upload_failed", "Изображение временно недоступно", 503) from None


def request_identity(prompt: str, references: Sequence[Mapping[str, object]]) -> str:
    if not references:
        return prompt  # Preserve existing text-only replay hashes.
    return json.dumps(
        {
            "prompt": prompt,
            "reference_images": [
                {"id": image["id"], "sha256": image["sha256"]} for image in references
            ],
        },
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )


async def message_images(
    session: AsyncSession, project_id: UUID, user_id: UUID, message_id: UUID
) -> list[dict[str, Any]]:
    message = await session.get(Message, message_id)
    if message is None or message.project_id != project_id or message.role != "user":
        raise ApiError("not_found", "Сообщение не найдено", 404)
    bindings = message.reference_images or []
    rows = await resolve_images(
        session, project_id, user_id, [UUID(item["id"]) for item in bindings]
    )
    blocks: list[dict[str, Any]] = []
    for row, binding in zip(rows, bindings, strict=True):
        if row.sha256 != binding["sha256"]:
            raise ApiError("conflict", "Изображение изменилось", 409)
        raw = await image_bytes(row)
        blocks.append(
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": row.mime_type,
                    "data": base64.b64encode(raw).decode("ascii"),
                },
            }
        )
    return blocks


def user_content(
    text: str, images: list[dict[str, Any]] | None, *, native: bool
) -> str | list[dict[str, Any]]:
    if not images:
        return text
    if native:
        return [{"type": "text", "text": text}, *images]
    return [
        {"type": "text", "text": text},
        *[
            {
                "type": "image_url",
                "image_url": {
                    "url": f"data:{image['source']['media_type']};base64,{image['source']['data']}"
                },
            }
            for image in images
        ],
    ]
