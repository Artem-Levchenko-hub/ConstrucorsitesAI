"""Тонкая обёртка над RQ для preview-задач."""

from __future__ import annotations

from uuid import UUID

import redis as sync_redis
from rq import Queue

from omnia_api.core.config import get_settings

QUEUE_NAME = "omnia-previews"
PREVIEW_JOB = "omnia_api.workers.preview.render_preview"


def _connection() -> sync_redis.Redis:
    return sync_redis.Redis.from_url(get_settings().redis_url)


def get_preview_queue() -> Queue:
    return Queue(QUEUE_NAME, connection=_connection())


def enqueue_preview(snapshot_id: UUID) -> None:
    get_preview_queue().enqueue(PREVIEW_JOB, str(snapshot_id), job_timeout=60)
