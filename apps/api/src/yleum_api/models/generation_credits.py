"""Append-only grants and account-serialized generation reservations."""

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from yleum_api.models.base import Base


class GenerationCreditGrant(Base):
    __tablename__ = "generation_credit_grants"
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    billing_account_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("billing_accounts.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    bucket: Mapped[str] = mapped_column(Text, nullable=False)
    scope: Mapped[str] = mapped_column(Text, nullable=False)
    units: Mapped[int] = mapped_column(Integer, nullable=False)
    external_ref: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    payment_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("payments.id", ondelete="RESTRICT"), nullable=True
    )
    subscription_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("subscriptions.id", ondelete="RESTRICT"), nullable=True
    )
    period_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    period_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    __table_args__ = (
        CheckConstraint("units >= 0", name="units_nonnegative"),
        CheckConstraint("bucket IN ('initial','edit','edit_only','flexible')", name="bucket"),
        CheckConstraint("scope IN ('free','period','package')", name="scope"),
        CheckConstraint(
            (
                "(scope='period' AND payment_id IS NOT NULL AND sub"
                "scription_id IS NOT NULL AND period_start IS NOT N"
                "ULL AND period_end IS NOT NULL AND period_end>peri"
                "od_start) OR (scope='package' AND payment_id IS NO"
                "T NULL AND period_start IS NULL AND period_end IS "
                "NULL) OR (scope='free' AND payment_id IS NULL AND "
                "period_start IS NULL AND period_end IS NULL)"
            ),
            name="source_binding",
        ),
    )


class GenerationCreditAdjustment(Base):
    """Append-only administrative debits and explicit reversals of those debits."""

    __tablename__ = "generation_credit_adjustments"
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    billing_account_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("billing_accounts.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    actor_user_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    allocations: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    reverses_adjustment_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("generation_credit_adjustments.id", ondelete="RESTRICT"),
        nullable=True,
        unique=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    __table_args__ = (
        CheckConstraint(
            "reason = btrim(reason) AND char_length(reason) BETWEEN 1 AND 500",
            name="reason",
        ),
        CheckConstraint("jsonb_typeof(allocations)='array'", name="allocations"),
        CheckConstraint(
            "reverses_adjustment_id IS NULL OR allocations='[]'::jsonb",
            name="reversal_allocations",
        ),
    )


class GenerationCreditReservation(Base):
    __tablename__ = "generation_credit_reservations"
    # No cascading run/project FK: deleting application history cannot refund consumed units.
    run_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    billing_account_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("billing_accounts.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    project_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    state: Mapped[str] = mapped_column(Text, nullable=False, default="held")
    units: Mapped[int] = mapped_column(Integer, nullable=False)
    allocations: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    accepted_snapshot_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    settled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    __table_args__ = (
        CheckConstraint("units > 0", name="positive_units"),
        CheckConstraint("kind IN ('initial','edit')", name="kind"),
        CheckConstraint("state IN ('held','consumed','released')", name="state"),
        CheckConstraint(
            (
                "(state='held' AND accepted_snapshot_id IS NULL AND"
                " settled_at IS NULL) OR (state='consumed' AND acce"
                "pted_snapshot_id IS NOT NULL AND settled_at IS NOT"
                " NULL) OR (state='released' AND accepted_snapshot_"
                "id IS NULL AND settled_at IS NOT NULL)"
            ),
            name="state_binding",
        ),
        CheckConstraint(
            "jsonb_typeof(allocations)='array' AND jsonb_array_length(allocations)>0",
            name="allocations",
        ),
    )
