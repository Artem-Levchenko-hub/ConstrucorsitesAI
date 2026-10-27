"""Durable subscription mail outbox; ambiguous delivery is never replayed."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, Numeric, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from yleum_api.models.base import Base


class EmailNotification(Base):
    __tablename__ = "email_notifications"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    event_key: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    event_type: Mapped[str] = mapped_column(Text, nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    billing_account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("billing_accounts.id", ondelete="CASCADE"), nullable=False
    )
    subscription_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("subscriptions.id", ondelete="CASCADE"), nullable=True
    )
    payment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("payments.id", ondelete="CASCADE"), nullable=False
    )
    period_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    plan_name: Mapped[str] = mapped_column(Text, nullable=False)
    amount_rub: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    status: Mapped[str] = mapped_column(Text, default="pending", server_default="pending")
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failure_category: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint(
            "event_type IN ('paid', 'ending_3', 'ending_1', 'payment_test')", name="event_type"
        ),
        CheckConstraint(
            "(event_type = 'payment_test' AND subscription_id IS NULL AND period_end IS NULL) OR "
            "(event_type <> 'payment_test' AND subscription_id IS NOT NULL "
            "AND period_end IS NOT NULL)",
            name="subscription_shape",
        ),
        CheckConstraint(
            "status IN ('pending', 'sending', 'sent', 'skipped', 'uncertain')", name="status"
        ),
        CheckConstraint("attempts >= 0", name="attempts"),
        Index("ix_email_notifications_ready", "status", "available_at"),
    )
