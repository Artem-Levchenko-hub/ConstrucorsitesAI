from __future__ import annotations

import json
from typing import Any
from uuid import UUID

import redis.asyncio as aioredis

from omnia_api.core.config import get_settings

_client: aioredis.Redis | None = None


def get_redis() -> aioredis.Redis:
    global _client
    if _client is None:
        s = get_settings()
        _client = aioredis.from_url(s.redis_url, decode_responses=True)
    return _client


async def dispose_redis() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


def project_channel(project_id: UUID | str) -> str:
    return f"omnia:project:{project_id}"


async def publish_event(
    project_id: UUID | str, event_type: str, data: dict[str, Any]
) -> None:
    payload = json.dumps({"type": event_type, "data": data}, default=str)
    await get_redis().publish(project_channel(project_id), payload)
