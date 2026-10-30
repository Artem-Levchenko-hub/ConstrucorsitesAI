"""Support conversations and transactional delivery; no raw guest capabilities."""

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from yleum_api.models.base import Base


class SupportTicket(Base):
    __tablename__ = "support_tickets"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    number: Mapped[int] = mapped_column(BigInteger, Identity(), unique=True, nullable=False)
    owner_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    guest_token_hash: Mapped[str | None] = mapped_column(Text)
    requester_email: Mapped[str] = mapped_column(Text)
    subject: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, default="new", server_default="new")
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    create_request_key: Mapped[str] = mapped_column(Text, unique=True)
    create_payload_hash: Mapped[str] = mapped_column(Text)
    source_path: Mapped[str | None] = mapped_column(Text)
    assigned_admin_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    first_response_due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    first_responded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        CheckConstraint("status IN ('new', 'in_progress', 'done')", name="status"),
        CheckConstraint("version > 0", name="version"),
        CheckConstraint(
            "(owner_id IS NOT NULL AND guest_token_hash IS NULL) OR "
            "(owner_id IS NULL AND guest_token_hash IS NOT NULL)",
            name="owner",
        ),
        CheckConstraint("(status = 'done') = (resolved_at IS NOT NULL)", name="resolved"),
        Index("ix_support_tickets_owner_updated", "owner_id", "updated_at"),
        Index("ix_support_tickets_status_updated", "status", "updated_at"),
        Index("ix_support_tickets_resolved", "resolved_at"),
        Index("ix_support_tickets_first_response", "first_response_due_at", "first_responded_at"),
    )


class SupportMessage(Base):
    __tablename__ = "support_messages"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    ticket_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("support_tickets.id", ondelete="CASCADE")
    )
    actor_key: Mapped[str] = mapped_column(Text)
    request_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    payload_hash: Mapped[str] = mapped_column(Text)
    author: Mapped[str] = mapped_column(Text)
    body: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.clock_timestamp()
    )
    __table_args__ = (
        UniqueConstraint("ticket_id", "actor_key", "request_id", name="uq_support_message_request"),
        CheckConstraint("author IN ('customer', 'support')", name="author"),
        Index("ix_support_messages_ticket_created", "ticket_id", "created_at"),
    )


class SupportEmailOutbox(Base):
    __tablename__ = "support_email_outbox"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    message_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("support_messages.id", ondelete="CASCADE")
    )
    recipient_email: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(Text, default="message", server_default="message")
    delivery_payload_encrypted: Mapped[str | None] = mapped_column(Text)
    recipient_admin_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE")
    )
    status: Mapped[str] = mapped_column(Text, default="pending", server_default="pending")
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    next_attempt_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (
        UniqueConstraint(
            "message_id", "recipient_email", "kind", name="uq_support_email_recipient"
        ),
        CheckConstraint("kind IN ('message', 'acknowledgement')", name="kind"),
        CheckConstraint(
            "status IN ('pending', 'sending', 'sent', 'uncertain', 'cancelled', 'failed')",
            name="status",
        ),
        Index("ix_support_email_ready", "status", "next_attempt_at"),
    )


class SupportRateBucket(Base):
    __tablename__ = "support_rate_buckets"
    key: Mapped[str] = mapped_column(Text, primary_key=True)
    window: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    count: Mapped[int] = mapped_column(Integer)
    __table_args__ = (Index("ix_support_rate_window", "window"),)


class SupportInboundReceipt(Base):
    """Durable inbox delivery/message dedup; stores no raw MIME or capabilities."""

    __tablename__ = "support_inbound_receipts"
    delivery_key: Mapped[str] = mapped_column(Text, primary_key=True)
    message_key: Mapped[str] = mapped_column(Text, unique=True)
    ticket_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("support_tickets.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SupportInboxCursor(Base):
    __tablename__ = "support_inbox_cursors"
    mailbox_key: Mapped[str] = mapped_column(Text, primary_key=True)
    last_uid: Mapped[int] = mapped_column(BigInteger)


class SupportAudit(Base):
    __tablename__ = "support_audit"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    ticket_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("support_tickets.id", ondelete="CASCADE")
    )
    admin_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    old_status: Mapped[str] = mapped_column(Text)
    new_status: Mapped[str] = mapped_column(Text)
    version: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (Index("ix_support_audit_ticket", "ticket_id", "created_at"),)
