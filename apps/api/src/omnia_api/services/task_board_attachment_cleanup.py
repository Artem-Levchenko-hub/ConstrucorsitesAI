"""Periodic, durable cleanup for task-board objects removed from metadata."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from omnia_api.core.config import get_settings
from omnia_api.models.task_board import TaskBoardAttachmentCleanup
from omnia_api.services import task_board_attachments as attachment_storage

log = logging.getLogger(__name__)

_delete_attachment = attachment_storage.delete_attachment
_BATCH_SIZE = 20
_DELETE_TIMEOUT_SECONDS = 5.0
_IDLE_POLL_SECONDS = 5.0
_MAX_BACKOFF_SECONDS = 300


async def _delete_with_timeout(object_key: str, timeout_seconds: float) -> str | None:
    try:
        await asyncio.wait_for(
            asyncio.to_thread(_delete_attachment, object_key),
            timeout=timeout_seconds,
        )
    except (TimeoutError, attachment_storage.AttachmentStorageError) as exc:
        return type(exc).__name__
    return None


async def drain_attachment_cleanup_batch(
    session: AsyncSession,
    *,
    limit: int = _BATCH_SIZE,
    delete_timeout_seconds: float = _DELETE_TIMEOUT_SECONDS,
) -> int:
    """Delete one locked batch and durably back off failed object removals."""

    pending = list(
        (
            await session.execute(
                select(TaskBoardAttachmentCleanup)
                .where(TaskBoardAttachmentCleanup.next_attempt_at <= func.now())
                .order_by(
                    TaskBoardAttachmentCleanup.next_attempt_at,
                    TaskBoardAttachmentCleanup.created_at,
                )
                .limit(limit)
                .with_for_update(skip_locked=True)
            )
        ).scalars()
    )
    if not pending:
        await session.commit()
        return 0

    errors = await asyncio.gather(
        *(_delete_with_timeout(cleanup.object_key, delete_timeout_seconds) for cleanup in pending)
    )
    now = datetime.now(UTC)
    for cleanup, error_name in zip(pending, errors, strict=True):
        if error_name is None:
            await session.delete(cleanup)
            continue
        cleanup.attempts += 1
        delay_seconds = min(
            _MAX_BACKOFF_SECONDS,
            5 * (2 ** min(cleanup.attempts - 1, 6)),
        )
        cleanup.next_attempt_at = now + timedelta(seconds=delay_seconds)
        cleanup.last_error = error_name[:255]
    await session.commit()
    return len(pending)


async def run_attachment_cleanup_forever() -> None:
    """Continuously drain the outbox from the long-lived worker process."""

    engine = create_async_engine(
        get_settings().database_url,
        pool_pre_ping=True,
        pool_size=2,
        max_overflow=0,
        pool_recycle=1800,
        connect_args={"timeout": 5.0, "command_timeout": 10.0},
    )
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        while True:
            processed = 0
            try:
                async with session_factory() as session:
                    processed = await drain_attachment_cleanup_batch(session)
            except Exception:
                log.exception("task_board_attachment_cleanup_worker_failed")
            await asyncio.sleep(0.25 if processed >= _BATCH_SIZE else _IDLE_POLL_SECONDS)
    finally:
        await engine.dispose()


__all__ = ["drain_attachment_cleanup_batch", "run_attachment_cleanup_forever"]
