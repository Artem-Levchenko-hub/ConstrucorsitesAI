from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    CHAR,
    BigInteger,
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
    capacity_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    attempt_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
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
                "'status', 'restore', 'reconcile', 'release')"
            ),
            name="kind_allowed",
        ),
        CheckConstraint(
            "status IN ('pending', 'waiting_capacity', 'running', 'completed', 'failed', "
            "'cancelled', 'indeterminate')",
            name="status_allowed",
        ),
        CheckConstraint("attempt_count >= 0", name="attempt_count_nonnegative"),
        UniqueConstraint(
            "workspace_id",
            "idempotency_key",
            name="uq_project_cell_operations_workspace_id_idempotency_key",
        ),
        Index(
            "uq_project_cell_operations_one_active_per_workspace",
            "workspace_id",
            unique=True,
            postgresql_where=text("status IN ('pending', 'waiting_capacity', 'running')"),
        ),
    )

    def __init__(self, **kwargs: object) -> None:
        kwargs.setdefault("request_payload", {})
        super().__init__(**kwargs)


class ProjectCellProof(Base):
    __tablename__ = "project_cell_proofs"

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
    proof_key: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    workspace_revision: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    dependency_digest: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    schema_data_digest: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    cell_manifest_digest: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    base_image_digest: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    toolchain_digest: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    resource_profile_version: Mapped[str] = mapped_column(Text, nullable=False)
    build_config_digest: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    __table_args__ = (
        CheckConstraint("fencing_epoch > 0", name="fencing_epoch_positive"),
        CheckConstraint("proof_key ~ '^[0-9a-f]{64}$'", name="proof_key_hex"),
        CheckConstraint(
            "workspace_revision ~ '^[0-9a-f]{64}$'",
            name="workspace_revision_hex",
        ),
        CheckConstraint(
            "dependency_digest ~ '^[0-9a-f]{64}$'",
            name="dependency_digest_hex",
        ),
        CheckConstraint(
            "schema_data_digest ~ '^[0-9a-f]{64}$'",
            name="schema_data_digest_hex",
        ),
        CheckConstraint(
            "cell_manifest_digest ~ '^[0-9a-f]{64}$'",
            name="cell_manifest_digest_hex",
        ),
        CheckConstraint(
            "base_image_digest ~ '^[0-9a-f]{64}$'",
            name="base_image_digest_hex",
        ),
        CheckConstraint(
            "toolchain_digest ~ '^[0-9a-f]{64}$'",
            name="toolchain_digest_hex",
        ),
        CheckConstraint(
            "build_config_digest ~ '^[0-9a-f]{64}$'",
            name="build_config_digest_hex",
        ),
        UniqueConstraint(
            "workspace_id",
            "fencing_epoch",
            "proof_key",
            name="uq_project_cell_proofs_workspace_epoch_key",
        ),
    )


class ProjectCellProofResult(Base):
    __tablename__ = "project_cell_proof_results"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    proof_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("project_cell_proofs.id", ondelete="CASCADE"),
        nullable=False,
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("project_cell_workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    dimension: Mapped[str] = mapped_column(Text, nullable=False)
    dimension_key: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    outcome: Mapped[str] = mapped_column(Text, nullable=False)
    operation_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    artifact_ref: Mapped[str | None] = mapped_column(Text, nullable=True)
    detail_digest: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    redacted_detail: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    __table_args__ = (
        CheckConstraint(
            "dimension IN ('bootstrap', 'fast_check', 'full_build', 'runtime', 'release')",
            name="dimension_allowed",
        ),
        CheckConstraint("dimension_key ~ '^[0-9a-f]{64}$'", name="dimension_key_hex"),
        CheckConstraint("outcome IN ('green', 'red')", name="outcome_allowed"),
        CheckConstraint("detail_digest ~ '^[0-9a-f]{64}$'", name="detail_digest_hex"),
        UniqueConstraint(
            "workspace_id",
            "dimension",
            "dimension_key",
            name="uq_project_cell_proof_results_workspace_dimension_key",
        ),
    )


class ProjectCellActivityLease(Base):
    __tablename__ = "project_cell_activity_leases"

    operation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
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
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    state: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default="active",
        server_default="active",
    )
    fencing_epoch: Mapped[int] = mapped_column(Integer, nullable=False)
    proof_key: Mapped[str | None] = mapped_column(CHAR(64), nullable=True)
    phase: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    deadline_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    heartbeat_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    log_bytes: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        default=0,
        server_default="0",
    )
    redacted_diagnostic: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        CheckConstraint(
            "kind IN ('command', 'tool', 'finalization', 'snapshot', 'promotion')",
            name="kind_allowed",
        ),
        CheckConstraint(
            "state IN ('active', 'completed', 'failed', 'timed_out', 'cancelled')",
            name="state_allowed",
        ),
        CheckConstraint("fencing_epoch > 0", name="fencing_epoch_positive"),
        CheckConstraint("log_bytes >= 0", name="log_bytes_nonnegative"),
        CheckConstraint(
            "proof_key IS NULL OR proof_key ~ '^[0-9a-f]{64}$'",
            name="proof_key_hex",
        ),
        Index(
            "uq_project_cell_activity_leases_one_active_per_workspace",
            "workspace_id",
            unique=True,
            postgresql_where=text("state = 'active'"),
        ),
    )


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
