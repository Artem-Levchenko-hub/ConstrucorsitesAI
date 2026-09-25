"""Durable code-only restoration claim; business data is never a restore payload."""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from yleum_api.models.base import Base

ACTIVE_RESTORATION_STATES = (
    "preparing",
    "checking",
    "ready",
    "needs_changes",
    "adapting",
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
    execution_policy: Mapped[str] = mapped_column(
        Text, nullable=False, default="manual", server_default="manual"
    )
    selected_branch: Mapped[str | None] = mapped_column(Text)
    adaptation_run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
    )
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
    source_binding: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    source_binding_digest: Mapped[str | None] = mapped_column(Text)
    activation_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    activation_offer: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    activation_offer_digest: Mapped[str | None] = mapped_column(Text)
    activation_request: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    activation_request_digest: Mapped[str | None] = mapped_column(Text)
    adaptation_planned_commit_sha: Mapped[str | None] = mapped_column(Text)
    activation_receipt: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    activation_receipt_digest: Mapped[str | None] = mapped_column(Text)
    activation_effects_admitted: Mapped[bool] = mapped_column(
        nullable=False, default=False, server_default="false"
    )
    activation_cancel_requested_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    activation_settled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    activation_notification_state: Mapped[str | None] = mapped_column(Text)
    report: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    applied_version_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    applied_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    error: Mapped[str | None] = mapped_column(Text)
    # Background reconciliation (AV19.1): when the worker must re-observe the
    # controller next; NULL while the operation waits for the owner or is terminal.
    next_reconcile_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reconcile_lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reconcile_attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0",
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
    )
    __table_args__ = (
        UniqueConstraint("project_id", "idempotency_key", name="uq_restorations_request"),
        CheckConstraint(
            "execution_policy IN ('manual','automatic_when_safe')",
            name="ck_restorations_execution_policy",
        ),
        CheckConstraint(
            "selected_branch IS NULL OR selected_branch IN ('exact','adaptive')",
            name="ck_restorations_selected_branch",
        ),
        CheckConstraint(
            "adaptation_run_id IS NULL OR selected_branch = 'adaptive'",
            name="ck_restorations_adaptation_branch",
        ),
        CheckConstraint(
            "activation_notification_state IS NULL OR "
            "activation_notification_state IN ('pending','delivered')",
            name="ck_restorations_activation_notification_state",
        ),
        CheckConstraint(
            "activation_id IS NULL OR selected_branch = 'adaptive'",
            name="ck_restorations_activation_branch",
        ),
        Index(
            "uq_restorations_adaptation_run_id",
            "adaptation_run_id",
            unique=True,
            postgresql_where=text("adaptation_run_id IS NOT NULL"),
        ),
        Index(
            "uq_restorations_activation_id",
            "activation_id",
            unique=True,
            postgresql_where=text("activation_id IS NOT NULL"),
        ),
        Index(
            "uq_restorations_active_project",
            "project_id",
            unique=True,
            postgresql_where=text(
                "state IN ('preparing','checking','ready','needs_changes','adapting',"
                "'applying','reconciling')"
            ),
        ),
        Index(
            "ix_restorations_next_reconcile_at",
            "next_reconcile_at",
            postgresql_where=text("next_reconcile_at IS NOT NULL"),
        ),
    )
