"""Durable included-generation reservations, independent of project deletion."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Text, func
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from yleum_api.models.base import Base


class GenerationAllowance(Base):
    __tablename__ = "generation_allowances"

    # A historical ledger identity, deliberately without a generation_runs FK.
    run_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    billing_account_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("billing_accounts.id", ondelete="RESTRICT"), nullable=False
    )
    user_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    subscription_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("subscriptions.id", ondelete="RESTRICT"), nullable=False
    )
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    period_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    period_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="reserved")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    settled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        CheckConstraint("status IN ('reserved', 'consumed', 'released')", name="status"),
        CheckConstraint("kind IN ('creation', 'edit')", name="kind"),
        CheckConstraint(
            "(period_start IS NULL AND period_end IS NULL) OR "
            "(period_start IS NOT NULL AND period_end IS NOT NULL AND period_end > period_start)",
            name="period_order",
        ),
        CheckConstraint("(status = 'reserved') = (settled_at IS NULL)", name="settlement"),
        Index(
            "ix_generation_allowances_account_subscription_period",
            "billing_account_id",
            "subscription_id",
            "kind",
            "period_start",
            "period_end",
            "status",
        ),
        Index(
            "ix_generation_allowances_account_lifetime_kind",
            "billing_account_id",
            "user_id",
            "kind",
            "status",
            postgresql_where=(period_start.is_(None) & period_end.is_(None)),
        ),
    )
