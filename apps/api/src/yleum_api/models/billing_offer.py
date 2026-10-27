"""Audited one-off payment/email test, independent of subscriptions and credits."""

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Numeric, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from yleum_api.models.base import Base


class BillingPurchaseOffer(Base):
    __tablename__ = "billing_purchase_offers"
    __table_args__ = (
        CheckConstraint("amount_rub = 1.00", name="one_ruble"),
        CheckConstraint("currency = 'RUB'", name="currency"),
        CheckConstraint("expires_at > created_at", name="expiry"),
        CheckConstraint(
            "(consumed_payment_id IS NULL) = (consumed_at IS NULL)", name="consumption"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    billing_account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("billing_accounts.id", ondelete="RESTRICT"), unique=True, nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    issued_by_user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    amount_rub: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(Text, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    reserved_payment_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("payments.id", ondelete="RESTRICT"), unique=True
    )
    consumed_payment_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("payments.id", ondelete="RESTRICT"), unique=True
    )
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class BillingOfferPayment(Base):
    __tablename__ = "billing_offer_payments"

    payment_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("payments.id", ondelete="CASCADE"), primary_key=True
    )
    offer_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("billing_purchase_offers.id", ondelete="RESTRICT"), index=True, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
