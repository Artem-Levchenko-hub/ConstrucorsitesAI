from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
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

from omnia_api.models.base import Base


class ProjectCellWorkspace(Base):
    __tablename__ = "project_cell_workspaces"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    owner_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    provider: Mapped[str] = mapped_column(Text, nullable=False)
    provider_ref: Mapped[str | None] = mapped_column(Text, nullable=True)
    state: Mapped[str] = mapped_column(Text, nullable=False)
    generation_run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("generation_runs.id", ondelete="SET NULL"),
        nullable=True,
    )
    provider_metadata: Mapped[dict[str, object]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default="{}",
    )
    fencing_epoch: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )
    version: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
        server_default="1",
    )
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
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
    ready_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        CheckConstraint(
            "state IN ('provisioning', 'ready', 'stopped', 'failed', 'deleting', 'deleted')",
            name="state_allowed",
        ),
        UniqueConstraint("project_id", name="uq_project_cell_workspaces_project_id"),
    )

    def __init__(self, **kwargs: object) -> None:
        kwargs.setdefault("provider_metadata", {})
        super().__init__(**kwargs)


class ProjectCellOperation(Base):
    __tablename__ = "project_cell_operations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("project_cell_workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    generation_run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("generation_runs.id", ondelete="SET NULL"),
        nullable=True,
    )
    idempotency_key: Mapped[str] = mapped_column(Text, nullable=False)
    request_digest: Mapped[str] = mapped_column(Text, nullable=False)
    fencing_epoch: Mapped[int | None] = mapped_column(Integer, nullable=True)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default="pending",
        server_default="pending",
    )
    request_payload: Mapped[dict[str, object]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default="{}",
    )
    result_payload: Mapped[dict[str, object] | None] = mapped_column(JSONB, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        CheckConstraint(
            (
                "kind IN ('ensure', 'wake', 'pause', 'stop', 'destroy', "
                "'status', 'restore', 'reconcile')"
            ),
            name="kind_allowed",
        ),
        CheckConstraint(
            "status IN ('pending', 'running', 'completed', 'failed', 'cancelled', 'indeterminate')",
            name="status_allowed",
        ),
        UniqueConstraint(
            "workspace_id",
            "idempotency_key",
            name="uq_project_cell_operations_workspace_id_idempotency_key",
        ),
        Index(
            "uq_project_cell_operations_one_active_per_workspace",
            "workspace_id",
            unique=True,
            postgresql_where=text("status IN ('pending', 'running')"),
        ),
    )

    def __init__(self, **kwargs: object) -> None:
        kwargs.setdefault("request_payload", {})
        super().__init__(**kwargs)


class ProjectCellCandidate(Base):
    """Immutable release evidence assembled by a fenced cell generation."""

    __tablename__ = "project_cell_candidates"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("project_cell_workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    generation_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("generation_runs.id", ondelete="RESTRICT"),
        nullable=False,
    )
    fencing_epoch: Mapped[int] = mapped_column(Integer, nullable=False)
    source_revision: Mapped[str] = mapped_column(Text, nullable=False)
    migration_digest: Mapped[str] = mapped_column(Text, nullable=False)
    database_backup_ref: Mapped[str] = mapped_column(Text, nullable=False)
    build_ref: Mapped[str] = mapped_column(Text, nullable=False)
    verification_ref: Mapped[str] = mapped_column(Text, nullable=False)
    expected_accepted_candidate_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("project_cell_candidates.id", ondelete="RESTRICT"),
        nullable=True,
    )
    status: Mapped[str] = mapped_column(Text, nullable=False, default="prepared")
    cancelled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    promoted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint("fencing_epoch > 0", name="fencing_epoch_positive"),
        CheckConstraint(
            "status IN ('prepared', 'accepted', 'rejected', 'cancelled')",
            name="status_allowed",
        ),
        CheckConstraint(
            "source_revision ~ '^[0-9a-f]{40}([0-9a-f]{24})?$'",
            name="source_revision_hex",
        ),
        CheckConstraint(
            "migration_digest ~ '^[0-9a-f]{64}$'", name="migration_digest_hex"
        ),
        CheckConstraint(
            "database_backup_ref ~ '^database-backup/sha256/[0-9a-f]{64}$'",
            name="database_backup_ref_content_addressed",
        ),
        CheckConstraint(
            "build_ref ~ '^build/sha256/[0-9a-f]{64}$'",
            name="build_ref_content_addressed",
        ),
        CheckConstraint(
            "verification_ref ~ '^verification/sha256/[0-9a-f]{64}$'",
            name="verification_ref_content_addressed",
        ),
        CheckConstraint(
            "(status = 'cancelled') = cancelled", name="cancelled_status_consistent"
        ),
        Index(
            "uq_project_cell_candidates_one_accepted",
            "workspace_id",
            unique=True,
            postgresql_where=text("status = 'accepted'"),
        ),
    )
