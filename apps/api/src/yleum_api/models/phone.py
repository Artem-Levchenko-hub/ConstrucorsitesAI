"""Durable SMS admission claims and one-time phone ownership challenges."""

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from yleum_api.models.base import Base


class PhoneChallenge(Base):
    __tablename__ = "phone_challenges"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    session_version: Mapped[int] = mapped_column(Integer, nullable=False)
    phone_e164: Mapped[str] = mapped_column(Text, nullable=False)
    code_hash: Mapped[str] = mapped_column(Text, nullable=False)
    ip_hash: Mapped[str] = mapped_column(Text, nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    # A claim is committed BEFORE the provider call. A crash remains charged to
    # all limits and cannot be retried invisibly (sending is also ambiguous).
    delivery_status: Mapped[str] = mapped_column(Text, nullable=False, default="sending")
    provider_message_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint("attempts BETWEEN 0 AND 5", name="phone_attempts"),
        CheckConstraint(
            "delivery_status IN ('sending', 'sent', 'ambiguous', 'failed')",
            name="phone_delivery_status",
        ),
        Index("ix_phone_challenges_user_created", "user_id", "created_at"),
        Index("ix_phone_challenges_phone_created", "phone_e164", "created_at"),
        Index("ix_phone_challenges_ip_created", "ip_hash", "created_at"),
        Index("ix_phone_challenges_created", "created_at"),
    )
