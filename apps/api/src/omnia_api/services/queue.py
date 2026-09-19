"""Тонкая обёртка над RQ для preview-задач."""

from __future__ import annotations

from uuid import UUID

import redis as sync_redis
from rq import Queue

from omnia_api.core.config import get_settings

QUEUE_NAME = "omnia-previews"
PREVIEW_JOB = "omnia_api.workers.preview.render_preview"
# V1.6 16/5 — composition-gate an entity app's live container. Runs in the worker
# (the only process on the runtime network that can reach omnia-dev-<slug>:3000).
ENTITY_GATE_JOB = "omnia_api.workers.quality.gate_entity_app"


def _connection() -> sync_redis.Redis:
    return sync_redis.Redis.from_url(get_settings().redis_url)


def get_preview_queue() -> Queue:
    return Queue(QUEUE_NAME, connection=_connection())


def enqueue_preview(snapshot_id: UUID) -> None:
    get_preview_queue().enqueue(PREVIEW_JOB, str(snapshot_id), job_timeout=60)


def enqueue_entity_gate(message_id: UUID, project_id: UUID, slug: str) -> None:
    """Queue the live-container composition gate (V1.6 16/5). job_timeout covers
    the compile-settle poll (~9s) plus two desktop composition renders."""
    get_preview_queue().enqueue(
        ENTITY_GATE_JOB,
        str(message_id),
        str(project_id),
        slug,
        job_timeout=120,
    )
