from __future__ import annotations

import json
import re
from collections.abc import Awaitable, Callable, Mapping
from typing import Any, TypedDict, cast
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from omnia_api.core.redis import publish_event
from omnia_api.models.generation_event import GenerationEvent
from omnia_api.models.generation_run import GenerationRun
from omnia_api.services.agent_progress import REDACTED, bounded_redacted_text, sanitize_agent_step

_MAX_STRING_BYTES = 4096
_MAX_PAYLOAD_BYTES = 16_384
_MAX_COLLECTION_ITEMS = 64
_MAX_DEPTH = 8
_MAX_EVENT_TYPE_BYTES = 128
_SENSITIVE_KEY_RE = re.compile(
    r"(?i)(?:authorization|cookie|dsn|password|passphrase|private[_-]?key|"
    r"secret|token|access[_-]?key|api[_-]?key)"
)


class GenerationEventEnvelope(TypedDict):
    event_id: str
    run_id: str
    seq: int
    type: str
    data: dict[str, object]


def _trim_text(value: str, *, max_bytes: int = _MAX_STRING_BYTES) -> str:
    return bounded_redacted_text(value, max_bytes=max_bytes)


def _normalize_json(value: Any, *, depth: int = 0) -> Any:
    if depth >= _MAX_DEPTH:
        return "[TRUNCATED]"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return _trim_text(value)
    if isinstance(value, list):
        return [
            _normalize_json(item, depth=depth + 1)
            for item in value[:_MAX_COLLECTION_ITEMS]
        ]
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, item in list(value.items())[:_MAX_COLLECTION_ITEMS]:
            if isinstance(key, str):
                safe_key = _trim_text(key, max_bytes=256)
                out[safe_key] = (
                    REDACTED
                    if _SENSITIVE_KEY_RE.search(safe_key)
                    else _normalize_json(item, depth=depth + 1)
                )
        return out
    return _trim_text(str(value))


def redact_event_payload(payload: Mapping[str, object]) -> dict[str, object]:
    normalized = cast(dict[str, object], _normalize_json(dict(payload)))
    if len(json.dumps(normalized, ensure_ascii=False).encode("utf-8")) <= _MAX_PAYLOAD_BYTES:
        return normalized

    bounded: dict[str, object] = {"_truncated": True}
    for key, value in normalized.items():
        candidate = {**bounded, key: value}
        if len(json.dumps(candidate, ensure_ascii=False).encode("utf-8")) <= _MAX_PAYLOAD_BYTES:
            bounded[key] = value
    return bounded


def _event_envelope(event: GenerationEvent) -> GenerationEventEnvelope:
    payload = event.payload
    safe_payload = (
        sanitize_agent_step(cast(dict[str, Any], payload))
        if isinstance(payload, dict) and {"step", "kind", "action"}.issubset(payload)
        else payload
    )
    return {
        "event_id": str(event.id),
        "run_id": str(event.generation_run_id),
        "seq": event.seq,
        "type": event.event_type,
        "data": cast(dict[str, object], safe_payload),
    }


async def append_generation_event(
    session: AsyncSession,
    *,
    run_id: UUID,
    project_id: UUID,
    message_id: UUID | None,
    event_type: str,
    payload: Mapping[str, object],
) -> GenerationEvent:
    safe_event_type = _trim_text(event_type.strip(), max_bytes=_MAX_EVENT_TYPE_BYTES)
    if not safe_event_type:
        raise ValueError("event_type must not be blank")
    run = await session.scalar(
        select(GenerationRun).where(GenerationRun.id == run_id).with_for_update()
    )
    if run is None:
        raise ValueError("generation run not found")
    if run.project_id != project_id:
        raise ValueError("generation run project does not match project_id")
    next_seq = await session.scalar(
        select(func.coalesce(func.max(GenerationEvent.seq), 0) + 1).where(
            GenerationEvent.generation_run_id == run_id
        )
    )
    event = GenerationEvent(
        generation_run_id=run.id,
        project_id=run.project_id,
        message_id=message_id,
        seq=int(next_seq or 1),
        event_type=safe_event_type,
        payload=redact_event_payload(payload),
    )
    session.add(event)
    await session.flush()
    return event


async def replay_generation_events(
    session: AsyncSession,
    *,
    run_id: UUID,
    after_seq: int,
    high_water: int | None = None,
    limit: int | None = None,
) -> list[GenerationEvent]:
    if after_seq < 0:
        raise ValueError("after_seq must be non-negative")
    if high_water is not None and high_water < after_seq:
        return []
    stmt = (
        select(GenerationEvent)
        .where(
            GenerationEvent.generation_run_id == run_id,
            GenerationEvent.seq > after_seq,
        )
        .order_by(GenerationEvent.seq.asc())
    )
    if high_water is not None:
        stmt = stmt.where(GenerationEvent.seq <= high_water)
    if limit is not None:
        if limit <= 0:
            raise ValueError("limit must be positive")
        stmt = stmt.limit(limit)
    return list(await session.scalars(stmt))


async def generation_event_high_water(
    session: AsyncSession,
    *,
    run_id: UUID,
) -> int:
    return int(
        await session.scalar(
            select(func.coalesce(func.max(GenerationEvent.seq), 0)).where(
                GenerationEvent.generation_run_id == run_id
            )
        )
        or 0
    )


async def persist_and_publish_generation_event(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    run_id: UUID,
    project_id: UUID,
    message_id: UUID | None,
    event_type: str,
    payload: dict[str, object],
    publisher: Callable[[UUID | str, str, dict[str, Any]], Awaitable[None]] = publish_event,
) -> GenerationEvent:
    async with session_factory() as session:
        event = await append_generation_event(
            session,
            run_id=run_id,
            project_id=project_id,
            message_id=message_id,
            event_type=event_type,
            payload=payload,
        )
        await session.commit()
        await session.refresh(event)
        session.expunge(event)
    await publisher(event.project_id, "generation.event", dict(_event_envelope(event)))
    return event


def generation_event_envelope(event: GenerationEvent) -> GenerationEventEnvelope:
    return _event_envelope(event)


def generation_event_json(event: GenerationEvent) -> str:
    return json.dumps(_event_envelope(event), ensure_ascii=False, default=str)


__all__ = [
    "GenerationEventEnvelope",
    "append_generation_event",
    "generation_event_envelope",
    "generation_event_high_water",
    "generation_event_json",
    "persist_and_publish_generation_event",
    "redact_event_payload",
    "replay_generation_events",
]
