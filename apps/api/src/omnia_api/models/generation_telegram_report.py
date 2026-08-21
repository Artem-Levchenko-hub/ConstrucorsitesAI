from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, Index, Integer, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from omnia_api.models.base import Base


class GenerationTelegramReport(Base):
    """Durable Telegram delivery state for one observed generation run."""

    __tablename__ = "generation_telegram_reports"

    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("generation_runs.id", ondelete="CASCADE"),
        primary_key=True,
    )
    start_state: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default="pending",
        server_default="pending",
    )
    start_message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    finish_state: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default="waiting_terminal",
        server_default="waiting_terminal",
    )
    terminal_status: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_stage: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default="accepted",
        server_default="accepted",
    )
    start_attempts: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )
    finish_attempts: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )
    start_next_attempt_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    finish_next_attempt_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_delivery_error_code: Mapped[str | None] = mapped_column(Text, nullable=True)
    preview_error_code: Mapped[str | None] = mapped_column(Text, nullable=True)
    preview_deadline_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    __table_args__ = (
        CheckConstraint(
            "start_state IN ('pending', 'sending', 'sent', 'failed', 'suppressed')",
            name="ck_generation_telegram_reports_start_state",
        ),
        CheckConstraint(
            "finish_state IN ('waiting_terminal', 'waiting_preview', 'pending', 'sending', "
            "'sent', 'warning_sent', 'failed', 'suppressed')",
            name="ck_generation_telegram_reports_finish_state",
        ),
        CheckConstraint(
            "terminal_status IS NULL OR terminal_status IN "
            "('completed', 'failed', 'cancelled')",
            name="ck_generation_telegram_reports_terminal_status",
        ),
        CheckConstraint(
            "last_stage IN ('accepted', 'routing', 'director', 'writer', 'images', "
            "'acceptance', 'snapshot', 'preview')",
            name="ck_generation_telegram_reports_last_stage",
        ),
        CheckConstraint(
            "start_attempts >= 0",
            name="ck_generation_telegram_reports_start_attempts_nonnegative",
        ),
        CheckConstraint(
            "finish_attempts >= 0",
            name="ck_generation_telegram_reports_finish_attempts_nonnegative",
        ),
        Index(
            "ix_generation_telegram_reports_due_work",
            "start_state",
            "finish_state",
            "start_next_attempt_at",
            "finish_next_attempt_at",
            "lease_until",
        ),
    )


__all__ = ["GenerationTelegramReport"]
