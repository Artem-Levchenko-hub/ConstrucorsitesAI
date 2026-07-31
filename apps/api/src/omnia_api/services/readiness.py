"""Public-safe dependency readiness and worker heartbeat."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from sqlalchemy import text

from omnia_api.core.db import get_engine
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


async def probe_readiness() -> dict[str, str]:
    database_ok, (redis_ok, worker_ok) = await asyncio.gather(
        _database_ok(),
        _redis_and_worker(),
    )
    return {
        "database": "ok" if database_ok else "failed",
        "redis": "ok" if redis_ok else "failed",
        "worker": "ok" if worker_ok else "failed",
    }
