"""Dispatch control only; provider_calls remains the sole financial journal."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, Integer, Text, func, text
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from yleum_api.models.base import Base


class GenerationProviderAttempt(Base):
    __tablename__ = "generation_provider_attempts"

    attempt_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    run_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    project_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    user_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    dispatch_token: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    prompt_hash: Mapped[str] = mapped_column(Text, nullable=False)
    request_sha256: Mapped[str] = mapped_column(Text, nullable=False)
    provider_call_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("provider_calls.id", ondelete="RESTRICT"),
        unique=True,
    )
    parent_attempt_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("generation_provider_attempts.attempt_id", ondelete="RESTRICT"),
        unique=True,
    )
    phase: Mapped[str] = mapped_column(Text, nullable=False, server_default="reserved")
    terminal_reason: Mapped[str | None] = mapped_column(Text)
    response_manifest_key: Mapped[str | None] = mapped_column(Text)
    response_sha256: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    retain_until: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=text("now() + interval '7 days'"),
    )
