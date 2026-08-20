"""Public-safe dependency readiness and worker heartbeat."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from typing import NamedTuple

import httpx
from sqlalchemy import text

from omnia_api.core.config import get_settings
from omnia_api.core.db import get_engine
from omnia_api.core.minio import get_minio_client
from omnia_api.core.redis import get_redis
from omnia_api.core.release import normalize_release_sha

WORKER_HEARTBEAT_KEY = "omnia:health:worker"
_PROBE_TIMEOUT_SECONDS = 3


class ReadinessReport(NamedTuple):
    checks: dict[str, str]
    dependencies: dict[str, str]


def parse_worker_heartbeat(raw: bytes | str | None) -> tuple[bool, str]:
    if not raw:
        return False, "unknown"
    text_value = raw.decode() if isinstance(raw, bytes) else raw
    try:
        value = json.loads(text_value)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return True, "unknown"
    release_sha = value.get("release_sha") if isinstance(value, dict) else None
    return True, normalize_release_sha(release_sha if isinstance(release_sha, str) else None)


async def write_worker_heartbeat(ttl_seconds: int) -> None:
    payload = json.dumps(
        {
            "at": datetime.now(UTC).isoformat(),
            "release_sha": normalize_release_sha(get_settings().omnia_release_sha),
        },
        separators=(",", ":"),
    )
    await get_redis().set(
        WORKER_HEARTBEAT_KEY,
        payload,
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


async def _redis_and_worker() -> tuple[bool, bool, str]:
    try:
        async with asyncio.timeout(_PROBE_TIMEOUT_SECONDS):
            redis = get_redis()
            redis_ok = bool(await redis.ping())
            worker_ok, worker_release_sha = parse_worker_heartbeat(
                await redis.get(WORKER_HEARTBEAT_KEY)
            )
        return redis_ok, worker_ok, worker_release_sha
    except Exception:
        return False, False, "unknown"


async def _deploy_control_plane_ok() -> tuple[bool, str]:
    try:
        settings = get_settings()
        async with httpx.AsyncClient(timeout=_PROBE_TIMEOUT_SECONDS) as client:
            response = await client.get(f"{settings.orchestrator_url.rstrip('/')}/health")
        payload = response.json()
        healthy = (
            response.status_code == 200
            and isinstance(payload, dict)
            and payload.get("status") == "ok"
        )
        raw_release = payload.get("release_sha") if isinstance(payload, dict) else None
        release_sha = normalize_release_sha(raw_release if isinstance(raw_release, str) else None)
        return healthy, release_sha
    except Exception:
        return False, "unknown"


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


async def probe_readiness() -> ReadinessReport:
    (
        database_ok,
        (redis_ok, worker_ok, worker_release_sha),
        (deploy_ok, orchestrator_release_sha),
        preview_ok,
    ) = await asyncio.gather(
        _database_ok(),
        _redis_and_worker(),
        _deploy_control_plane_ok(),
        _preview_storage_ok(),
    )
    return ReadinessReport(
        checks={
            "database": "ok" if database_ok else "failed",
            "redis": "ok" if redis_ok else "failed",
            "worker": "ok" if worker_ok else "failed",
            "deploy_control_plane": "ok" if deploy_ok else "failed",
            "preview_storage": "ok" if preview_ok else "failed",
        },
        dependencies={
            "worker_release_sha": worker_release_sha,
            "orchestrator_release_sha": orchestrator_release_sha,
        },
    )
