from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUuid
from sqlalchemy.orm import Mapped, mapped_column

from yleum_api.models.base import Base


class MaxStaffPaymentAttempt(Base):
    """Merchant payment evidence, independent of platform billing and cell storage."""

    __tablename__ = "max_staff_payment_attempts"

    id: Mapped[UUID] = mapped_column(PgUuid(as_uuid=True), primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(
        PgUuid(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    order_id: Mapped[UUID] = mapped_column(PgUuid(as_uuid=True), nullable=False)
    owner_id: Mapped[UUID] = mapped_column(PgUuid(as_uuid=True), nullable=False)
    binding_id: Mapped[UUID] = mapped_column(PgUuid(as_uuid=True), nullable=False)
    integration_id: Mapped[UUID] = mapped_column(PgUuid(as_uuid=True), nullable=False)
    actor_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    shop_id: Mapped[str] = mapped_column(Text, nullable=False)
    auth_mode: Mapped[str] = mapped_column(Text, nullable=False)
    grant_revision: Mapped[int] = mapped_column(BigInteger, nullable=False)
    oauth_client_key: Mapped[str | None] = mapped_column(Text)
    amount_kopecks: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(Text, nullable=False, server_default="RUB")
    request_body: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    provider_payment_id: Mapped[str | None] = mapped_column(Text)
    confirmation_url: Mapped[str | None] = mapped_column(Text)
    failure_reason: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="pending")
    is_test: Mapped[bool | None] = mapped_column(Boolean)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    create_dispatch_claimed: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default="false",
    )
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    __table_args__ = (
        CheckConstraint("amount_kopecks > 0", name="ck_max_staff_payment_attempts_amount"),
        CheckConstraint("currency = 'RUB'", name="ck_max_staff_payment_attempts_currency"),
        CheckConstraint(
            "status IN ('pending', 'succeeded', 'canceled', 'needs_review')",
            name="ck_max_staff_payment_attempts_status",
        ),
        CheckConstraint("attempts >= 0", name="ck_max_staff_payment_attempts_attempts"),
        CheckConstraint(
            "failure_reason IS NULL OR failure_reason = 'receipt_required'",
            name="ck_max_staff_payment_attempts_failure_reason",
        ),
        UniqueConstraint("project_id", "order_id", name="uq_max_staff_payment_attempts_order"),
        UniqueConstraint("provider_payment_id", name="uq_max_staff_payment_attempts_provider_id"),
        Index("ix_max_staff_payment_attempts_due", "status", "next_attempt_at"),
    )
