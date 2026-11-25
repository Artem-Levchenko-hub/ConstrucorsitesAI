from __future__ import annotations

import json
import re
from collections.abc import Awaitable, Callable, Mapping
from typing import Any, TypedDict, cast
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from yleum_api.core.redis import publish_event
from yleum_api.models.generation_event import GenerationEvent
from yleum_api.models.generation_run import GenerationRun
from yleum_api.services.agent_progress import (
    REDACTED,
    TOOL_LIFECYCLE_EVENTS,
    agent_step_identity,
    bounded_redacted_text,
    sanitize_agent_step,
    tool_lifecycle_step,
)
from yleum_api.services.agent_tool_diagnostics import safe_tool_diagnostic
from yleum_api.services.generation.recovery_trace import bind_recovery_payload, safe_recovery_trace

_MAX_STRING_BYTES = 4096
_MAX_PAYLOAD_BYTES = 16_384
_MAX_COLLECTION_ITEMS = 64
_MAX_DEPTH = 8
_MAX_EVENT_TYPE_BYTES = 128
PROVISIONAL_TOOL_FAILURE = "_provisional_tool_failure"
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
        if value.get("kind") == "provider_error" or value.get("tool") == "provider_response":
            value = sanitize_agent_step(value)
        out: dict[str, Any] = {}
        for key, item in list(value.items())[:_MAX_COLLECTION_ITEMS]:
            if isinstance(key, str):
                safe_key = _trim_text(key, max_bytes=256)
                if safe_key == "tool_diagnostic":
                    item = safe_tool_diagnostic(item)
                elif safe_key == "recovery_trace":
                    item = safe_recovery_trace(item) if isinstance(item, Mapping) else {}
                if _SENSITIVE_KEY_RE.search(safe_key):
                    # Validated token counts and sent/omitted budget metadata
                    # are protocol facts, not credentials.
                    out[safe_key] = safe_recovery_trace({safe_key: item}).get(safe_key, REDACTED)
                else:
                    out[safe_key] = _normalize_json(item, depth=depth + 1)
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


def _bound_payload(
    event_type: str, payload: Mapping[str, object], run_id: UUID, message_id: UUID | None,
) -> dict[str, object]:
    if event_type in TOOL_LIFECYCLE_EVENTS:
        step = tool_lifecycle_step(event_type, payload)
        if not step:
            return {}
        # Only server-assigned transcript identity can accompany a lifecycle event.
        step.update(agent_step_identity({**payload, "runId": str(run_id)}))
        if message_id is not None:
            step["message_id"] = str(message_id)
        return step
    bound = bind_recovery_payload(payload, run_id=str(run_id), event_type=event_type)
    if ((event_type == "agent.step" and "tool_diagnostic" in bound)
            or {"step", "kind", "action"}.issubset(bound)
            or bound.get("kind") == "provider_error" or bound.get("tool") == "provider_response"):
        return sanitize_agent_step(bound)
    return bound


def _event_envelope(event: GenerationEvent) -> GenerationEventEnvelope:
    payload = _bound_payload(
        event.event_type, event.payload, event.generation_run_id, event.message_id,
    )
    if event.event_type in {"generation.trace", "recovery.trace"}:
        payload = redact_event_payload(payload)
    if event.event_type == "tool.finished" and payload.get(PROVISIONAL_TOOL_FAILURE) is True:
        # Keep the operation and replay cursor visible without making an
        # internal check the final user verdict. The stored event stays exact.
        payload = {
            key: value for key, value in payload.items()
            if key not in {PROVISIONAL_TOOL_FAILURE, "state", "detail", "ok"}
        }
    return {
        "event_id": str(event.id),
        "run_id": str(event.generation_run_id),
        "seq": event.seq,
        "type": event.event_type,
        "data": payload,
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
        payload=redact_event_payload(
            _bound_payload(safe_event_type, payload, run.id, message_id)
        ),
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
