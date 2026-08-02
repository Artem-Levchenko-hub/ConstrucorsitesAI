"""Тонкая обёртка над RQ для preview-задач."""

from __future__ import annotations

from uuid import UUID

import redis as sync_redis
from rq import Queue, Retry
from rq.job import JobStatus

from omnia_api.core.config import get_settings

QUEUE_NAME = "omnia-previews"
PREVIEW_JOB = "omnia_api.workers.preview.render_preview"
# V1.6 16/5 — composition-gate an entity app's live container. Runs in the worker
# (the only process on the runtime network that can reach omnia-dev-<slug>:3000).
ENTITY_GATE_JOB = "omnia_api.workers.quality.gate_entity_app"
# Exe-build: package a Python project into a Windows .exe + NSIS Setup installer.
# job_timeout covers PyInstaller (~120s) + NSIS (~30s) + upload headroom.
BUILD_EXE_JOB = "omnia_api.workers.build_exe.build_exe_job"
# Hero-media MVP: one asynchronous hero render (planner already completed).
HERO_MEDIA_JOB = "omnia_api.workers.hero_media.hero_media_job"


def _connection() -> sync_redis.Redis:
    return sync_redis.Redis.from_url(get_settings().redis_url)


def get_preview_queue() -> Queue:
    return Queue(QUEUE_NAME, connection=_connection())


def enqueue_preview(snapshot_id: UUID) -> bool:
    """Queue one exact snapshot render, deduplicated by its RQ lifecycle."""
    connection = _connection()
    enqueue_lock = f"omnia:preview:enqueue:{snapshot_id}"
    if not connection.set(enqueue_lock, "1", nx=True, ex=30):
        return False
    try:
        queue = Queue(QUEUE_NAME, connection=connection)
        job_id = f"snapshot-preview-{snapshot_id}"
        existing = queue.fetch_job(job_id)
        active = {
            JobStatus.QUEUED,
            JobStatus.STARTED,
            JobStatus.DEFERRED,
            JobStatus.SCHEDULED,
        }
        if existing is not None and existing.get_status(refresh=True) in active:
            return False
        if existing is not None:
            existing.delete()
        queue.enqueue(
            PREVIEW_JOB,
            str(snapshot_id),
            job_id=job_id,
            job_timeout=240,
            retry=Retry(max=2, interval=[10, 30]),
            failure_ttl=86_400,
        )
    except Exception:
        raise
    finally:
        connection.delete(enqueue_lock)
    return True


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


def enqueue_build_exe(
    project_id: UUID, build_id: str, slug: str, files: dict[str, str]
) -> None:
    """Queue a Windows exe+installer build for the given project snapshot.

    job_timeout=420 covers PyInstaller (~120s) + NSIS (~30s) + MinIO upload +
    generous headroom. The RQ worker must have access to the orchestrator
    network (same as the API container).
    """
    get_preview_queue().enqueue(
        BUILD_EXE_JOB,
        str(project_id),
        build_id,
        slug,
        files,
        job_timeout=420,
    )


def enqueue_hero_media_render(render_id: UUID) -> None:
    """Queue one hero-media render pipeline.

    Video generation may legitimately run for minutes, so this path must never
    sit inside the ordinary request/response loop.
    """
    get_preview_queue().enqueue(
        HERO_MEDIA_JOB,
        str(render_id),
        job_timeout=900,
    )
