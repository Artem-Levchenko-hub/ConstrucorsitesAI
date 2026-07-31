import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from omnia_api.models.base import Base


class AuthSession(Base):
    __tablename__ = "auth_sessions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    user_agent: Mapped[str | None] = mapped_column(Text, nullable=True)
    ip_address: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (Index("ix_auth_sessions_user_active", "user_id", "revoked_at"),)


class AuthToken(Base):
    __tablename__ = "auth_tokens"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    purpose: Mapped[str] = mapped_column(Text, nullable=False)
    token_hash: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        CheckConstraint(
            "purpose IN ('verify_email', 'reset_password')",
            name="ck_auth_tokens_purpose",
        ),
        Index("ix_auth_tokens_user_purpose", "user_id", "purpose"),
    )


class LegalAcceptance(Base):
    __tablename__ = "legal_acceptances"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    document_type: Mapped[str] = mapped_column(Text, nullable=False)
    document_version: Mapped[str] = mapped_column(Text, nullable=False)
    ip_address: Mapped[str | None] = mapped_column(Text, nullable=True)
    user_agent: Mapped[str | None] = mapped_column(Text, nullable=True)
    accepted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        CheckConstraint(
            "document_type IN ('terms', 'privacy', 'personal_data', 'marketing')",
            name="ck_legal_acceptances_document_type",
        ),
        Index("ix_legal_acceptances_user_created", "user_id", "accepted_at"),
    )


class BusinessProfile(Base):
    __tablename__ = "business_profiles"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    inn: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    ogrn: Mapped[str | None] = mapped_column(Text, nullable=True)
    legal_name: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default="pending", default="pending"
    )
    verification_source: Mapped[str | None] = mapped_column(Text, nullable=True)
    verification_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    verification_data: Mapped[dict[str, object]] = mapped_column(
        JSONB, nullable=False, server_default="{}", default=dict
    )
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        CheckConstraint(
            "kind IN ('legal_entity', 'sole_proprietor', 'self_employed')",
            name="ck_business_profiles_kind",
        ),
        CheckConstraint(
            "status IN ('pending', 'verified', 'rejected', 'suspended')",
            name="ck_business_profiles_status",
        ),
    )


class BusinessMember(Base):
    __tablename__ = "business_members"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    business_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("business_profiles.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[str] = mapped_column(Text, nullable=False, server_default="owner", default="owner")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        CheckConstraint("role IN ('owner', 'admin', 'member')", name="ck_business_members_role"),
        UniqueConstraint("business_id", "user_id", name="uq_business_members_pair"),
        UniqueConstraint("user_id", name="uq_business_members_user"),
        Index("ix_business_members_business", "business_id"),
    )


class BusinessEntitlement(Base):
    __tablename__ = "business_entitlements"

    business_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("business_profiles.id", ondelete="CASCADE"),
        primary_key=True,
    )
    free_generation_limit: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="3", default=3
    )
    free_generations_used: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0", default=0
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Payment(Base):
    __tablename__ = "payments"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    billing_account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("billing_accounts.id", ondelete="RESTRICT"),
        nullable=False,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    provider: Mapped[str] = mapped_column(Text, nullable=False, server_default="yookassa")
    provider_payment_id: Mapped[str | None] = mapped_column(Text, nullable=True, unique=True)
    idempotency_key: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    purpose: Mapped[str] = mapped_column(
        Text, nullable=False, server_default="wallet_topup", default="wallet_topup"
    )
    subscription_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("subscriptions.id", ondelete="SET NULL"),
        nullable=True,
    )
    package_code: Mapped[str] = mapped_column(Text, nullable=False)
    amount_rub: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    credit_rub: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="pending")
    confirmation_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    provider_payload: Mapped[dict[str, object]] = mapped_column(
        JSONB, nullable=False, server_default="{}", default=dict
    )
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    refunded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        CheckConstraint(
            "purpose IN ('wallet_topup', 'subscription_initial', 'subscription_renewal')",
            name="ck_payments_purpose",
        ),
        CheckConstraint(
            "(purpose = 'wallet_topup' AND subscription_id IS NULL) OR "
            "(purpose IN ('subscription_initial', 'subscription_renewal') "
            "AND subscription_id IS NOT NULL)",
            name="ck_payments_subscription_link",
        ),
        CheckConstraint(
            "status IN ('pending', 'waiting_for_capture', 'succeeded', 'cancelled', "
            "'refunded', 'failed')",
            name="ck_payments_status",
        ),
        Index("ix_payments_subscription_created", "subscription_id", "created_at"),
        Index("ix_payments_account_created", "billing_account_id", "created_at"),
        Index("ix_payments_user_created", "user_id", "created_at"),
    )
