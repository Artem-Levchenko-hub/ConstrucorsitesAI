"""Durable code-only restoration claim; business data is never a restore payload."""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, Integer, Text, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from omnia_api.models.base import Base

ACTIVE_RESTORATION_STATES = (
    "preparing",
    "checking",
    "ready",
    "needs_changes",
    "applying",
    "reconciling",
)


class Restoration(Base):
    __tablename__ = "restorations"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    owner_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("project_cell_workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    # Historical identity survives pruning of technical snapshots and version rows.
    source_version_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    source_snapshot_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    base_draft_snapshot_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    target_commit_sha: Mapped[str] = mapped_column(Text, nullable=False)
    base_commit_sha: Mapped[str] = mapped_column(Text, nullable=False)
    planned_commit_sha: Mapped[str | None] = mapped_column(Text)
    idempotency_key: Mapped[str] = mapped_column(Text, nullable=False)
    request_digest: Mapped[str] = mapped_column(Text, nullable=False)
    apply_idempotency_key: Mapped[str | None] = mapped_column(Text)
    apply_digest: Mapped[str | None] = mapped_column(Text)
    state: Mapped[str] = mapped_column(Text, nullable=False, default="preparing")
    phase: Mapped[str] = mapped_column(Text, nullable=False, default="prepare")
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    runtime_revision: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    candidate_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    fencing_epoch: Mapped[int] = mapped_column(Integer, nullable=False)
    prior_fencing_epoch: Mapped[int | None] = mapped_column(Integer)
    request_payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    runtime_result: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    report: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    applied_version_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    applied_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    __table_args__ = (
        UniqueConstraint("project_id", "idempotency_key", name="uq_restorations_request"),
        Index(
            "uq_restorations_active_project",
            "project_id",
            unique=True,
            postgresql_where=text(
                "state IN ('preparing','checking','ready','needs_changes','applying','reconciling')"
            ),
        ),
    )
