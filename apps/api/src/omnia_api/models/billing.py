from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Final

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from omnia_api.models.base import Base

FREE_PLAN_ID: Final = uuid.UUID("00000000-0000-4000-8000-000000000001")
PRO_PLAN_ID: Final = uuid.UUID("00000000-0000-4000-8000-000000000002")
BUSINESS_PLAN_ID: Final = uuid.UUID("00000000-0000-4000-8000-000000000003")

DEFAULT_BILLING_PLANS: Final[tuple[dict[str, object], ...]] = (
    {
        "id": FREE_PLAN_ID,
        "code": "free",
        "version": 1,
        "name": "Free",
        "price_rub": Decimal("0.00"),
        "billing_interval": "month",
        "included_credit_rub": Decimal("0.0000"),
        "entitlements": {
            "max_projects": 1,
            "static_publish_slots": 0,
            "always_on_slots": 0,
            "team_seats": 1,
            "custom_domains": 0,
            "integrations": False,
            "preview_idle_minutes": 15,
        },
        "sort_order": 0,
        "is_active": True,
    },
    {
        "id": PRO_PLAN_ID,
        "code": "pro",
        "version": 1,
        "name": "Pro",
        "price_rub": Decimal("1490.00"),
        "billing_interval": "month",
        "included_credit_rub": Decimal("500.0000"),
        "entitlements": {
            "max_projects": 3,
            "static_publish_slots": 3,
            "always_on_slots": 0,
            "team_seats": 1,
            "custom_domains": 1,
            "integrations": True,
            "preview_idle_minutes": 60,
        },
        "sort_order": 10,
        "is_active": True,
    },
    {
        "id": BUSINESS_PLAN_ID,
        "code": "business",
        "version": 1,
        "name": "Business",
        "price_rub": Decimal("4990.00"),
        "billing_interval": "month",
        "included_credit_rub": Decimal("1500.0000"),
        "entitlements": {
            "max_projects": 10,
            "static_publish_slots": 10,
            "always_on_slots": 1,
            "team_seats": 5,
            "custom_domains": 10,
            "integrations": True,
            "preview_idle_minutes": 0,
        },
        "sort_order": 20,
        "is_active": True,
    },
)


class BillingAccount(Base):
    """Canonical owner of a wallet, ledger and subscription.

    A new user starts with a personal account. MAX onboarding promotes that
    same account to business scope, so the existing balance and history stay
    attached while every member of the business resolves one shared account.
    """

    __tablename__ = "billing_accounts"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    scope: Mapped[str] = mapped_column(
        Text, nullable=False, default="personal", server_default="personal"
    )
    personal_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=True,
    )
    business_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("business_profiles.id", ondelete="RESTRICT"),
        nullable=True,
    )
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    currency: Mapped[str] = mapped_column(
        Text, nullable=False, default="RUB", server_default="RUB"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    __table_args__ = (
        CheckConstraint("scope IN ('personal', 'business')", name="ck_billing_accounts_scope"),
        CheckConstraint(
            "(scope = 'personal' AND personal_user_id IS NOT NULL AND business_id IS NULL) "
            "OR (scope = 'business' AND personal_user_id IS NULL AND business_id IS NOT NULL)",
            name="ck_billing_accounts_owner",
        ),
        CheckConstraint("currency = 'RUB'", name="ck_billing_accounts_currency"),
        Index(
            "uq_billing_accounts_personal_user",
            "personal_user_id",
            unique=True,
            postgresql_where=text("personal_user_id IS NOT NULL"),
        ),
        Index(
            "uq_billing_accounts_business",
            "business_id",
            unique=True,
            postgresql_where=text("business_id IS NOT NULL"),
        ),
    )


class BillingPlan(Base):
    """Immutable commercial terms.

    A price or entitlement change creates a new ``(code, version)`` row.
    Existing subscriptions keep pointing at the revision they bought.
    """

    __tablename__ = "billing_plans"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    code: Mapped[str] = mapped_column(Text, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    price_rub: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    billing_interval: Mapped[str] = mapped_column(Text, nullable=False, default="month")
    included_credit_rub: Mapped[Decimal] = mapped_column(
        Numeric(12, 4), nullable=False, default=Decimal("0")
    )
    entitlements: Mapped[dict[str, object]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    sort_order: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        CheckConstraint("price_rub >= 0", name="ck_billing_plans_price_non_negative"),
        CheckConstraint(
            "included_credit_rub >= 0",
            name="ck_billing_plans_credit_non_negative",
        ),
        CheckConstraint(
            "billing_interval IN ('month')",
            name="ck_billing_plans_interval",
        ),
        UniqueConstraint("code", "version", name="uq_billing_plans_code_version"),
        Index(
            "uq_billing_plans_active_code",
            "code",
            unique=True,
            postgresql_where=text("is_active"),
        ),
    )


class BillingPaymentMethod(Base):
    """Provider token for a payment method; raw card data never enters Omnia."""

    __tablename__ = "billing_payment_methods"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    billing_account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("billing_accounts.id", ondelete="RESTRICT"),
        nullable=False,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    provider: Mapped[str] = mapped_column(
        Text, nullable=False, default="yookassa", server_default="yookassa"
    )
    provider_payment_method_id: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(
        Text, nullable=False, default="active", server_default="active"
    )
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    last4: Mapped[str | None] = mapped_column(Text, nullable=True)
    consent_version: Mapped[str] = mapped_column(Text, nullable=False)
    consented_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('active', 'revoked', 'expired')",
            name="ck_billing_payment_methods_status",
        ),
        UniqueConstraint(
            "provider",
            "provider_payment_method_id",
            name="uq_billing_payment_methods_provider_id",
        ),
        Index(
            "ix_billing_payment_methods_account_status",
            "billing_account_id",
            "status",
        ),
        Index("ix_billing_payment_methods_user_status", "user_id", "status"),
    )


class Subscription(Base):
    """Subscription state machine.

    Renewal payments are intentionally not implemented here yet. The model
    supports them without making the current one-time payment flow recurring.
    """

    __tablename__ = "subscriptions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    billing_account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("billing_accounts.id", ondelete="RESTRICT"),
        nullable=False,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    plan_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("billing_plans.id", ondelete="RESTRICT"), nullable=False
    )
    payment_method_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("billing_payment_methods.id", ondelete="SET NULL"),
        nullable=True,
    )
    status: Mapped[str] = mapped_column(
        Text, nullable=False, default="active", server_default="active"
    )
    auto_renew: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    cancel_at_period_end: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    current_period_start: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    current_period_end: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    next_charge_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    grace_period_ends_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    canceled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('trialing', 'active', 'past_due', 'paused', 'canceled', 'expired')",
            name="ck_subscriptions_status",
        ),
        CheckConstraint(
            "current_period_end IS NULL OR current_period_start IS NULL "
            "OR current_period_end > current_period_start",
            name="ck_subscriptions_period_order",
        ),
        Index(
            "uq_subscriptions_account_live",
            "billing_account_id",
            unique=True,
            postgresql_where=text(
                "status IN ('trialing', 'active', 'past_due', 'paused')"
            ),
        ),
        Index("ix_subscriptions_plan_status", "plan_id", "status"),
        Index("ix_subscriptions_next_charge", "next_charge_at", "status"),
    )
