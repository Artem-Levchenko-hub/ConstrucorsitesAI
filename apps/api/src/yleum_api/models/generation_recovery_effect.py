"""Logical effect references, never a second financial or tool authority."""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from yleum_api.models.base import Base


class GenerationRecoveryEffect(Base):
    __tablename__ = "generation_recovery_effects"
    __table_args__ = (
        UniqueConstraint(
            "run_id", "logical_step_seq", "effect_ordinal", name="uq_recovery_effect_cursor"
        ),
        CheckConstraint("payload_version=1", name="ck_recovery_effect_version"),
        CheckConstraint(
            "logical_step_seq>=0 AND effect_ordinal>=0 AND revision>=0 AND attempt_seq>=0",
            name="ck_recovery_effect_counters",
        ),
        CheckConstraint(
            "kind IN ('bootstrap','provider','tool','proof','progress')",
            name="ck_recovery_effect_kind",
        ),
        CheckConstraint(
            "state IN ('prepared','inflight','confirmed','unknown','consumed')",
            name="ck_recovery_effect_state",
        ),
        CheckConstraint("input_digest ~ '^[0-9a-f]{64}$'", name="ck_recovery_effect_digest"),
    )

    effect_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    # Deliberately no run/project FK: intent writers use their own short session
    # while legacy callers can hold those rows. Current ownership is revalidated.
    run_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    project_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    user_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    logical_step_seq: Mapped[int] = mapped_column(BigInteger, nullable=False)
    effect_ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    payload_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    input_digest: Mapped[str] = mapped_column(Text, nullable=False)
    state: Mapped[str] = mapped_column(Text, nullable=False, default="prepared")
    revision: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    attempt_seq: Mapped[int] = mapped_column(BigInteger, nullable=False)
    claim_token: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    provider_attempt_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("generation_provider_attempts.attempt_id", ondelete="RESTRICT"),
        unique=True,
    )
    parent_effect_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("generation_recovery_effects.effect_id", ondelete="RESTRICT"),
    )
    operation_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    tool_call_id: Mapped[str | None] = mapped_column(Text)
    pre_binding: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    post_binding: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    receipt_ref: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    receipt_sha256: Mapped[str | None] = mapped_column(Text)
    checkpoint_ref: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
