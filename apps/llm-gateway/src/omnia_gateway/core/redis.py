"""Redis client — single async connection pool, used by `services.cache`."""

from __future__ import annotations

import redis.asyncio as redis_async

from omnia_gateway.core.config import get_settings

_client: redis_async.Redis | None = None


async def init_redis() -> redis_async.Redis:
    global _client
    if _client is None:
        _client = redis_async.from_url(
            get_settings().redis_url,
            encoding="utf-8",
            decode_responses=False,
        )
    return _client


async def close_redis() -> None:
    global _client
    if _client is not None:
        await _client.close()
        _client = None


def get_redis() -> redis_async.Redis:
    if _client is None:
        raise RuntimeError("Redis client not initialized — call init_redis() in lifespan")
    return _client
