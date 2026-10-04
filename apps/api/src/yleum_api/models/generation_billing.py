"""Prospective run policy and immutable terminal settlement intent."""

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Numeric, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from yleum_api.models.base import Base


class GenerationBillingPolicy(Base):
    __tablename__ = "generation_billing_policies"
    run_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("generation_runs.id", ondelete="CASCADE"), primary_key=True
    )
    is_free: Mapped[bool] = mapped_column(Boolean, nullable=False)
    version: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class GenerationBillingIntent(Base):
    __tablename__ = "generation_billing_intents"
    run_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("generation_runs.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    billing_account_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("billing_accounts.id", ondelete="RESTRICT"), nullable=True
    )
    outcome: Mapped[str] = mapped_column(Text, nullable=False)
    amount_rub: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False)
    usage_ids: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    accepted_snapshot_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    receipt: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint("outcome IN ('billable','waived')", name="outcome"),
        CheckConstraint(
            "amount_rub >= 0 AND (outcome = 'billable' OR amount_rub = 0)", name="amount"
        ),
        CheckConstraint(
            "outcome != 'billable' OR (billing_account_id IS NOT NULL "
            "AND accepted_snapshot_id IS NOT NULL)",
            name="billable_binding",
        ),
    )


class GenerationBillingOutbox(Base):
    __tablename__ = "generation_billing_outbox"
    run_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("generation_billing_intents.run_id", ondelete="CASCADE"),
        primary_key=True,
    )
    next_attempt_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), index=True
    )
    settled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
