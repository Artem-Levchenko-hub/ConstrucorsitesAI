"""Privacy-safe collection and bounded Moscow-time owner aggregates."""

import base64
import binascii
import hashlib
import hmac
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import case, distinct, func, literal, select, tuple_
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from yleum_api.core.config import get_settings
from yleum_api.core.errors import ApiError
from yleum_api.models.max_analytics import (
    EVENT_TYPE_KINDS,
    AnalyticsEventType,
    AnalyticsKind,
    MaxAnalyticsEvent,
)

ANALYTICS_TIMEZONE = "Europe/Moscow"


@dataclass(frozen=True)
class AnalyticsWindow:
    days: int
    observed_at: datetime
    start: datetime
    end: datetime

    def metadata(self) -> dict[str, Any]:
        return {
            "days": self.days,
            "from_date": self.start.date().isoformat(),
            "to_date": (self.end - timedelta(days=1)).date().isoformat(),
            "timezone": ANALYTICS_TIMEZONE,
        }


def analytics_window(days: int) -> AnalyticsWindow:
    observed_at = datetime.now(ZoneInfo(ANALYTICS_TIMEZONE))
    today = observed_at.replace(hour=0, minute=0, second=0, microsecond=0)
    return AnalyticsWindow(
        days, observed_at, today - timedelta(days=days - 1), today + timedelta(days=1)
    )


def decode_cursor(cursor: str) -> tuple[datetime, UUID]:
    try:
        if not 1 <= len(cursor) <= 512:
            raise ValueError("Invalid cursor size")
        payload = json.loads(
            base64.b64decode(cursor + "=" * (-len(cursor) % 4), altchars=b"-_", validate=True)
        )
        if (
            not isinstance(payload, list)
            or len(payload) != 2
            or not all(isinstance(value, str) for value in payload)
        ):
            raise ValueError("Invalid cursor shape")
        occurred_at, event_id = datetime.fromisoformat(payload[0]), UUID(payload[1])
        if occurred_at.tzinfo is None or occurred_at.utcoffset() is None:
            raise ValueError("Cursor timestamp must include a timezone")
        # Normalize before binding: an offset near datetime's limits may overflow UTC.
        return occurred_at.astimezone(UTC), event_id
    except (ValueError, TypeError, binascii.Error, OverflowError) as exc:
        raise ApiError("validation_failed", "Invalid analytics cursor", 422) from exc


def _encode_cursor(occurred_at: datetime, event_id: UUID) -> str:
    payload = json.dumps([occurred_at.isoformat(), str(event_id)], separators=(",", ":"))
    return base64.urlsafe_b64encode(payload.encode()).decode().rstrip("=")


def actor_key(project_id: UUID, user_id: int) -> str:
    settings = get_settings()
    secret = settings.secrets_encryption_key or settings.jwt_secret
    return hmac.new(
        secret.get_secret_value().encode(),
        f"omnia:max-analytics:v1:{project_id}:{user_id}".encode(),
        hashlib.sha256,
    ).hexdigest()


async def record_event(
    session: AsyncSession,
    project_id: UUID,
    user_id: int,
    event_id: UUID,
    kind: str,
    event_type: AnalyticsEventType | None = None,
) -> None:
    if event_type is not None and kind not in EVENT_TYPE_KINDS.get(event_type, ()):
        raise ApiError("validation_failed", "Event type does not match kind", 422)
    key = actor_key(project_id, user_id)
    await session.execute(
        insert(MaxAnalyticsEvent)
        .values(
            project_id=project_id,
            actor_key=key,
            event_id=event_id,
            kind=kind,
            event_type=event_type,
        )
        .on_conflict_do_nothing(constraint="uq_max_analytics_receipt")
    )
    existing = (
        await session.execute(
            select(MaxAnalyticsEvent.kind, MaxAnalyticsEvent.event_type).where(
                MaxAnalyticsEvent.project_id == project_id,
                MaxAnalyticsEvent.actor_key == key,
                MaxAnalyticsEvent.event_id == event_id,
            )
        )
    ).one()
    if existing.kind != kind or (
        existing.event_type is not None
        and event_type is not None
        and existing.event_type != event_type
    ):
        raise ApiError("idempotency_conflict", "Event identity was reused", 409)
    await session.commit()


async def aggregate_events(session: AsyncSession, project_id: UUID, days: int) -> dict[str, Any]:
    window = analytics_window(days)
    model = MaxAnalyticsEvent
    filters = (
        model.project_id == project_id,
        model.occurred_at >= window.start,
        model.occurred_at < window.end,
    )
    counts = (
        (
            await session.execute(
                select(
                    func.count().label("events"),
                    func.count(distinct(model.actor_key)).label("users"),
                    func.count(case((model.kind == "open", 1))).label("opens"),
                    func.count(case((model.kind == "action", 1))).label("actions"),
                ).where(*filters)
            )
        )
        .mappings()
        .one()
    )
    day = func.date(func.timezone(ANALYTICS_TIMEZONE, model.occurred_at))
    rows = (
        await session.execute(
            select(
                day.label("date"),
                func.count().label("events"),
                func.count(distinct(model.actor_key)).label("users"),
                func.count(case((model.kind == "open", 1))).label("opens"),
                func.count(case((model.kind == "action", 1))).label("actions"),
            )
            .where(*filters)
            .group_by(day)
            .order_by(day)
        )
    ).mappings()
    indexed = {str(row["date"]): dict(row) for row in rows}
    daily = []
    for offset in range(days):
        date = (window.start + timedelta(days=offset)).date().isoformat()
        row = indexed.get(date, {"events": 0, "users": 0, "opens": 0, "actions": 0})
        daily.append({**row, "date": date})
    measured_since = await session.scalar(
        select(func.min(model.occurred_at)).where(
            model.project_id == project_id,
        )
    )
    return {
        **dict(counts),
        **window.metadata(),
        "daily": daily,
        "measured_since": measured_since,
    }


async def list_events(
    session: AsyncSession,
    project_id: UUID,
    days: int,
    *,
    kind: AnalyticsKind | None = None,
    event_type: AnalyticsEventType | None = None,
    limit: int = 30,
    cursor: str | None = None,
) -> dict[str, Any]:
    window = analytics_window(days)
    model = MaxAnalyticsEvent
    # Only opens have an unambiguous historical meaning; never infer other types.
    visible_type = func.coalesce(model.event_type, case((model.kind == "open", "app_open")))
    query = select(model.id, model.kind, visible_type.label("event_type"), model.occurred_at).where(
        model.project_id == project_id,
        model.occurred_at >= window.start,
        model.occurred_at < window.end,
    )
    if kind is not None:
        query = query.where(model.kind == kind)
    if event_type is not None:
        query = query.where(visible_type == event_type)
    if cursor is not None:
        occurred_at, event_id = decode_cursor(cursor)
        query = query.where(
            tuple_(model.occurred_at, model.id) < tuple_(literal(occurred_at), literal(event_id))
        )
    rows = (
        (
            await session.execute(
                query.order_by(model.occurred_at.desc(), model.id.desc()).limit(limit + 1)
            )
        )
        .mappings()
        .all()
    )
    items = [dict(row) for row in rows[:limit]]
    next_cursor = None
    if len(rows) > limit:
        last = items[-1]
        next_cursor = _encode_cursor(last["occurred_at"], last["id"])
    return {
        **window.metadata(),
        "items": items,
        "next_cursor": next_cursor,
        "observed_at": window.observed_at,
    }
