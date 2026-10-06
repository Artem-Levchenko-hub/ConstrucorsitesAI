"""Provider expenses independent of customer liability; retained across app deletion."""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Index,
    Numeric,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from yleum_api.models.base import Base


class ProviderCall(Base):
    __tablename__ = "provider_calls"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    provider_scope: Mapped[str] = mapped_column(Text, nullable=False)
    route: Mapped[str] = mapped_column(Text, nullable=False)
    requested_model: Mapped[str] = mapped_column(Text, nullable=False)
    actual_model: Mapped[str | None] = mapped_column(Text)
    expense_owner: Mapped[str] = mapped_column(Text, nullable=False)
    # Deliberately no cascading FKs: operational deletion must not erase expenses.
    user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    project_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    message_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    usage_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    stage: Mapped[str] = mapped_column(Text, nullable=False)
    free: Mapped[bool] = mapped_column(Boolean, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    provider_request_id: Mapped[str | None] = mapped_column(Text)
    tokens_in: Mapped[int | None] = mapped_column(BigInteger)
    tokens_out: Mapped[int | None] = mapped_column(BigInteger)
    cache_read_tokens: Mapped[int | None] = mapped_column(BigInteger)
    cache_write_tokens: Mapped[int | None] = mapped_column(BigInteger)
    calculated_cost_rub: Mapped[Decimal | None] = mapped_column(Numeric(24, 8))
    provider_cost_rub: Mapped[Decimal | None] = mapped_column(Numeric(24, 8))
    provider_cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(24, 8))
    cost_provenance: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    error_type: Mapped[str | None] = mapped_column(Text)
    receipt_hash: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint("provider_scope = 'llmgw'", name="ck_provider_calls_scope"),
        CheckConstraint(
            "status IN ('started','completed','failed','ambiguous')",
            name="ck_provider_calls_status",
        ),
        CheckConstraint(
            "(expense_owner='user' AND user_id IS NOT NULL) OR "
            "(expense_owner='platform' AND user_id IS NULL)",
            name="ck_provider_calls_owner",
        ),
        CheckConstraint(
            "(status='started' AND finished_at IS NULL) OR "
            "(status<>'started' AND finished_at IS NOT NULL)",
            name="ck_provider_calls_finished",
        ),
        *[
            CheckConstraint(f"{name} IS NULL OR {name} >= 0", name=f"ck_provider_calls_{name}")
            for name in (
                "tokens_in",
                "tokens_out",
                "cache_read_tokens",
                "cache_write_tokens",
                "calculated_cost_rub",
                "provider_cost_rub",
                "provider_cost_usd",
            )
        ],
        Index(
            "uq_provider_calls_receipt",
            "provider_scope",
            "provider_request_id",
            unique=True,
            postgresql_where=text("provider_request_id IS NOT NULL"),
        ),
        Index("ix_provider_calls_created_at", "created_at"),
        Index("ix_provider_calls_run_id", "run_id"),
    )
