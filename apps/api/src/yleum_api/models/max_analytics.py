"""Project-scoped MAX telemetry receipts; never store launch data or profiles."""

import uuid
from datetime import datetime
from typing import Literal

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from yleum_api.models.base import Base

AnalyticsKind = Literal["open", "action", "event"]
AnalyticsEventType = Literal[
    "app_open",
    "screen_view",
    "item_view",
    "button_click",
    "form_submit",
    "cart_add",
    "checkout_start",
    "order_created",
    "booking_created",
    "lead_created",
]
EVENT_TYPE_KINDS: dict[AnalyticsEventType, tuple[AnalyticsKind, ...]] = {
    "app_open": ("open",),
    "screen_view": ("event",),
    "item_view": ("event",),
    "button_click": ("event",),
    "form_submit": ("action", "event"),
    "cart_add": ("event",),
    "checkout_start": ("event",),
    "order_created": ("action", "event"),
    "booking_created": ("action", "event"),
    "lead_created": ("action", "event"),
}


class MaxAnalyticsEvent(Base):
    __tablename__ = "max_analytics_events"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    actor_key: Mapped[str] = mapped_column(String(64), nullable=False)
    event_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    event_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    __table_args__ = (
        UniqueConstraint("project_id", "actor_key", "event_id", name="uq_max_analytics_receipt"),
        CheckConstraint("kind IN ('open','action','event')", name="max_analytics_kind"),
        CheckConstraint(
            "event_type IS NULL OR "
            "(kind = 'open' AND event_type = 'app_open') OR "
            "(kind IN ('action','event') AND event_type IN "
            "('form_submit','order_created','booking_created','lead_created')) OR "
            "(kind = 'event' AND event_type IN "
            "('screen_view','item_view','button_click','cart_add','checkout_start'))",
            name="max_analytics_event_type",
        ),
        Index("ix_max_analytics_project_time", "project_id", "occurred_at"),
        Index("ix_max_analytics_project_feed", "project_id", "occurred_at", "id"),
    )
