"""Append-only operator evidence, registered for safe Alembic autogeneration."""

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from yleum_api.models.base import Base


class LedgerCreated:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )


class ProviderLedgerEntry(LedgerCreated, Base):
    __tablename__ = "provider_ledger_entries"

    organization_id: Mapped[str] = mapped_column(Text, primary_key=True)
    operation_id: Mapped[str] = mapped_column(Text, primary_key=True)
    ref_id: Mapped[str | None] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    delta_kopecks: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(Text, nullable=False)
    receipt_hash: Mapped[str] = mapped_column(Text, nullable=False)
    source_sha256: Mapped[str] = mapped_column(Text, nullable=False)
    source_kind: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (
        CheckConstraint("kind IN ('usage','correction','other')", name="kind"),
        CheckConstraint("delta_kopecks > -9223372036854775808", name="delta_kopecks"),
        CheckConstraint("currency='RUB'", name="currency"),
        CheckConstraint("receipt_hash ~ '^[a-f0-9]{64}$'", name="receipt_hash"),
        CheckConstraint("source_sha256 ~ '^[a-f0-9]{64}$'", name="source_sha256"),
        CheckConstraint(
            "source_kind IN ('balance_ledger_export','authorized_ledger_api')", name="source_kind"
        ),
        Index("ix_provider_ledger_ref", "organization_id", "ref_id"),
    )


class ProviderLedgerConflict(LedgerCreated, Base):
    __tablename__ = "provider_ledger_conflicts"

    organization_id: Mapped[str] = mapped_column(Text, primary_key=True)
    operation_id: Mapped[str] = mapped_column(Text, primary_key=True)
    observed_hash: Mapped[str] = mapped_column(Text, primary_key=True)
    observed_ref_id: Mapped[str | None] = mapped_column(Text)
    observed_kind: Mapped[str] = mapped_column(Text, nullable=False)
    observed_delta_kopecks: Mapped[int] = mapped_column(BigInteger, nullable=False)
    source_sha256: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (
        CheckConstraint("observed_hash ~ '^[a-f0-9]{64}$'", name="observed_hash"),
        CheckConstraint("source_sha256 ~ '^[a-f0-9]{64}$'", name="source_sha256"),
        ForeignKeyConstraint(
            ["organization_id", "operation_id"],
            ["provider_ledger_entries.organization_id", "provider_ledger_entries.operation_id"],
        ),
    )


class ProviderLedgerConfirmation(LedgerCreated, Base):
    __tablename__ = "provider_ledger_confirmations"

    organization_id: Mapped[str] = mapped_column(Text, primary_key=True)
    operation_id: Mapped[str] = mapped_column(Text, primary_key=True)
    call_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("provider_calls.id"),
        nullable=False,
    )
    entry_receipt_hash: Mapped[str] = mapped_column(Text, nullable=False)
    call_receipt_hash: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id", "operation_id"],
            ["provider_ledger_entries.organization_id", "provider_ledger_entries.operation_id"],
        ),
    )
