import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Numeric, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from omnia_api.models.base import Base


class WalletCharge(Base):
    __tablename__ = "wallet_charges"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    billing_account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("billing_accounts.id", ondelete="RESTRICT"),
        nullable=False,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    message_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("messages.id"),
        nullable=True,
    )
    subscription_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("subscriptions.id", ondelete="SET NULL"),
        nullable=True,
    )
    entry_type: Mapped[str] = mapped_column(
        Text, nullable=False, server_default="usage", default="usage"
    )
    amount_rub: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False)
    balance_after_rub: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False)
    external_ref: Mapped[str | None] = mapped_column(Text, nullable=True, unique=True)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        CheckConstraint(
            "entry_type IN "
            "('usage', 'topup', 'payment', 'refund', 'subscription_credit', 'adjustment')",
            name="ck_wallet_charges_entry_type",
        ),
        Index(
            "ix_wallet_charges_account_created_at",
            "billing_account_id",
            "created_at",
        ),
        Index("ix_wallet_charges_user_id_created_at", "user_id", "created_at"),
        Index("ix_wallet_charges_subscription_id", "subscription_id"),
    )
