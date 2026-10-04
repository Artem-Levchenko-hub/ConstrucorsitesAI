"""Privacy-safe collection and bounded Moscow-time owner aggregates."""

import hashlib
import hmac
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import case, distinct, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from yleum_api.core.config import get_settings
from yleum_api.core.errors import ApiError
from yleum_api.models.max_analytics import MaxAnalyticsEvent

ANALYTICS_TIMEZONE = "Europe/Moscow"


def actor_key(project_id: UUID, user_id: int) -> str:
    settings = get_settings()
    secret = settings.secrets_encryption_key or settings.jwt_secret
    return hmac.new(
        secret.get_secret_value().encode(),
        f"omnia:max-analytics:v1:{project_id}:{user_id}".encode(),
        hashlib.sha256,
    ).hexdigest()


async def record_event(
    session: AsyncSession, project_id: UUID, user_id: int, event_id: UUID, kind: str
) -> None:
    key = actor_key(project_id, user_id)
    await session.execute(
        insert(MaxAnalyticsEvent)
        .values(
            project_id=project_id,
            actor_key=key,
            event_id=event_id,
            kind=kind,
        )
        .on_conflict_do_nothing(constraint="uq_max_analytics_receipt")
    )
    existing_kind = await session.scalar(
        select(MaxAnalyticsEvent.kind).where(
            MaxAnalyticsEvent.project_id == project_id,
            MaxAnalyticsEvent.actor_key == key,
            MaxAnalyticsEvent.event_id == event_id,
        )
    )
    if existing_kind != kind:
        raise ApiError("idempotency_conflict", "Event identity was reused", 409)
    await session.commit()


async def aggregate_events(session: AsyncSession, project_id: UUID, days: int) -> dict[str, Any]:
    today = datetime.now(ZoneInfo(ANALYTICS_TIMEZONE)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    start = today - timedelta(days=days - 1)
    end = today + timedelta(days=1)
    model = MaxAnalyticsEvent
    filters = (model.project_id == project_id, model.occurred_at >= start, model.occurred_at < end)
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
        date = (start + timedelta(days=offset)).date().isoformat()
        row = indexed.get(date, {"events": 0, "users": 0, "opens": 0, "actions": 0})
        daily.append({**row, "date": date})
    measured_since = await session.scalar(
        select(func.min(model.occurred_at)).where(
            model.project_id == project_id,
        )
    )
    return {
        **dict(counts),
        "days": days,
        "from_date": start.date().isoformat(),
        "to_date": today.date().isoformat(),
        "timezone": ANALYTICS_TIMEZONE,
        "daily": daily,
        "measured_since": measured_since,
    }
