"""Append-only settlement identity for a completed provider receipt."""

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from yleum_api.models.base import Base


class UsageSettlement(Base):
    __tablename__ = "usage_settlements"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    billing_account_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("billing_accounts.id", ondelete="RESTRICT")
    )
    provider_scope: Mapped[str] = mapped_column(Text, nullable=False)
    provider_request_id: Mapped[str | None] = mapped_column(Text)
    receipt_hash: Mapped[str] = mapped_column(Text, nullable=False)
    usage_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("usage.id", ondelete="RESTRICT"), nullable=False, unique=True
    )
    wallet_charge_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("wallet_charges.id", ondelete="RESTRICT"), unique=True
    )
    status: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "provider_scope",
            "provider_request_id",
            name="uq_usage_settlements_provider_receipt",
        ),
        CheckConstraint(
            "status IN ('settled', 'free', 'unpaid')", name="ck_usage_settlements_status"
        ),
        CheckConstraint(
            "(status = 'settled') = (wallet_charge_id IS NOT NULL)",
            name="ck_usage_settlements_charge",
        ),
    )
