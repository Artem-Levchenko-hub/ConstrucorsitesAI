"""Public-safe dependency readiness and worker heartbeat."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import httpx
from sqlalchemy import text

from omnia_api.core.config import get_settings
from omnia_api.core.db import get_engine
from omnia_api.core.minio import get_minio_client
from omnia_api.core.redis import get_redis

WORKER_HEARTBEAT_KEY = "omnia:health:worker"
_PROBE_TIMEOUT_SECONDS = 3


async def write_worker_heartbeat(ttl_seconds: int) -> None:
    await get_redis().set(
        WORKER_HEARTBEAT_KEY,
        datetime.now(UTC).isoformat(),
        ex=max(ttl_seconds, 30),
    )


async def _database_ok() -> bool:
    try:
        async with asyncio.timeout(_PROBE_TIMEOUT_SECONDS):
            async with get_engine().connect() as connection:
                await connection.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


async def _redis_and_worker() -> tuple[bool, bool]:
    try:
        async with asyncio.timeout(_PROBE_TIMEOUT_SECONDS):
            redis = get_redis()
            redis_ok = bool(await redis.ping())
            worker_ok = bool(await redis.get(WORKER_HEARTBEAT_KEY))
        return redis_ok, worker_ok
    except Exception:
        return False, False


async def _deploy_control_plane_ok() -> bool:
    try:
        settings = get_settings()
        async with httpx.AsyncClient(timeout=_PROBE_TIMEOUT_SECONDS) as client:
            response = await client.get(f"{settings.orchestrator_url.rstrip('/')}/health")
        return response.status_code == 200 and response.json().get("status") == "ok"
    except Exception:
        return False


async def _preview_storage_ok() -> bool:
    def _probe() -> bool:
        settings = get_settings()
        client = get_minio_client()
        return client.bucket_exists(
            settings.minio_bucket_projects
        ) and client.bucket_exists(settings.minio_bucket_previews)

    try:
        async with asyncio.timeout(_PROBE_TIMEOUT_SECONDS):
            return await asyncio.to_thread(_probe)
    except Exception:
        return False


async def probe_readiness() -> dict[str, str]:
    database_ok, (redis_ok, worker_ok), deploy_ok, preview_ok = await asyncio.gather(
        _database_ok(),
        _redis_and_worker(),
        _deploy_control_plane_ok(),
        _preview_storage_ok(),
    )
    return {
        "database": "ok" if database_ok else "failed",
        "redis": "ok" if redis_ok else "failed",
        "worker": "ok" if worker_ok else "failed",
        "deploy_control_plane": "ok" if deploy_ok else "failed",
        "preview_storage": "ok" if preview_ok else "failed",
    }
