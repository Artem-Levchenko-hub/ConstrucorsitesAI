"""Hash-only dual-mailbox challenges and durable completed security notifications."""

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from yleum_api.models.base import Base


class EmailChangeChallenge(Base):
    __tablename__ = "email_change_challenges"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    old_email: Mapped[str] = mapped_column(Text)
    new_email: Mapped[str] = mapped_column(Text)
    session_version: Mapped[int] = mapped_column(Integer)
    old_token_hash: Mapped[str] = mapped_column(Text, unique=True)
    new_token_hash: Mapped[str] = mapped_column(Text, unique=True)
    old_confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    new_confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (
        CheckConstraint("lower(old_email) <> lower(new_email)", name="different_emails"),
        CheckConstraint("old_token_hash <> new_token_hash", name="different_tokens"),
        Index("ix_email_change_user_active", "user_id", "used_at"),
    )


class AccountSecurityEmail(Base):
    __tablename__ = "account_security_emails"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    event_key: Mapped[str] = mapped_column(Text, unique=True)
    kind: Mapped[str] = mapped_column(Text)
    recipient_email: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, default="pending", server_default="pending")
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (
        CheckConstraint(
            "kind IN ('password_changed', 'password_reset', 'email_changed')", name="kind"
        ),
        CheckConstraint(
            "status IN ('pending', 'sending', 'sent', 'uncertain', 'failed')", name="status"
        ),
        CheckConstraint("attempts >= 0", name="attempts"),
        Index("ix_account_security_email_ready", "status", "available_at"),
    )
