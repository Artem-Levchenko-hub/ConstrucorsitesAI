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


# ── Resumable stream state ────────────────────────────────────────────────
# Pub/sub дельты эфемерны: клиент, переживший F5 во время генерации, теряет
# весь прошлый поток и превью замирает. Чиним так: пишем накопленный контент
# (кумулятивный, как его видит клиент) в Redis на каждую дельту, и при
# (пере)подключении WS отдаём один кадр `stream.sync`. Дальше клиент дедупит
# живые дельты по `seq`. TTL короткий — это горячее состояние одной генерации,
# не долговечные данные.

_STREAM_TTL_S = 600


def _stream_buffer_key(message_id: UUID | str) -> str:
    return f"omnia:stream:{message_id}"


def _active_stream_key(project_id: UUID | str) -> str:
    return f"omnia:active:{project_id}"


async def set_stream_state(
    project_id: UUID | str, message_id: UUID | str, content: str, seq: int
) -> None:
    """Записать текущее состояние стрима (кумулятивный контент + seq).

    Вызывается на КАЖДУЮ дельту, поэтому буфер всегда актуален на момент
    reconnect — клиент не получит дыру в HTML. Обе записи под общим TTL.
    """
    client = get_redis()
    payload = json.dumps(
        {"message_id": str(message_id), "content": content, "seq": seq}
    )
    await client.set(_stream_buffer_key(message_id), payload, ex=_STREAM_TTL_S)
    await client.set(
        _active_stream_key(project_id), str(message_id), ex=_STREAM_TTL_S
    )


async def get_active_stream(project_id: UUID | str) -> str | None:
    val = await get_redis().get(_active_stream_key(project_id))
    return None if val is None else str(val)


async def get_stream_state(message_id: UUID | str) -> dict[str, Any] | None:
    raw = await get_redis().get(_stream_buffer_key(message_id))
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None
    return data if isinstance(data, dict) else None


async def clear_stream_state(
    project_id: UUID | str, message_id: UUID | str
) -> None:
    """Снять состояние стрима по завершении (done/error/cancel/crash).

    Активный указатель проекта чистим ТОЛЬКО если он всё ещё наш — новый
    промпт мог уже перехватить его (last-writer-wins на гонке билдов).
    """
    client = get_redis()
    await client.delete(_stream_buffer_key(message_id))
    current = await client.get(_active_stream_key(project_id))
    if current == str(message_id):
        await client.delete(_active_stream_key(project_id))
