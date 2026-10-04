"""Project-scoped MAX telemetry receipts; never store launch data or profiles."""

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from yleum_api.models.base import Base


class MaxAnalyticsEvent(Base):
    __tablename__ = "max_analytics_events"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    actor_key: Mapped[str] = mapped_column(String(64), nullable=False)
    event_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    __table_args__ = (
        UniqueConstraint("project_id", "actor_key", "event_id", name="uq_max_analytics_receipt"),
        CheckConstraint("kind IN ('open','action','event')", name="max_analytics_kind"),
        Index("ix_max_analytics_project_time", "project_id", "occurred_at"),
    )
