"""Account-level usage journal for spend that leaves no other trace in the DB.

Generations are already durable (``generation_runs`` + the gateway's ``usage``
rows) and every AI answer of a published app is a ``usage`` row with
``stage = 'runtime_ai'``. A publication, however, only existed in the
orchestrator's journal on the host: the platform DB could not say how many
apps an account had published or when. This table records such events per
billing account so the usage report and the publish-slot entitlement read one
source of truth. ``external_ref`` makes a retried request idempotent.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any, Final

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, Numeric, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from yleum_api.models.base import Base

USAGE_EVENT_KINDS: Final[tuple[str, ...]] = ("publication",)


class BillingUsageEvent(Base):
    __tablename__ = "billing_usage_events"

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
    # SET NULL on purpose: deleting a project frees its publish slot but must
    # not erase the fact that the account published something in the period.
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="SET NULL"),
        nullable=True,
    )
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    cost_rub: Mapped[Decimal] = mapped_column(
        Numeric(12, 4), nullable=False, default=Decimal("0"), server_default="0"
    )
    external_ref: Mapped[str | None] = mapped_column(Text, nullable=True, unique=True)
    details: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        CheckConstraint("kind IN ('publication')", name="ck_billing_usage_events_kind"),
        CheckConstraint("quantity > 0", name="ck_billing_usage_events_quantity"),
        CheckConstraint("cost_rub >= 0", name="ck_billing_usage_events_cost"),
        Index(
            "ix_billing_usage_events_account_kind_created",
            "billing_account_id",
            "kind",
            "created_at",
        ),
        Index("ix_billing_usage_events_project_kind", "project_id", "kind"),
    )
