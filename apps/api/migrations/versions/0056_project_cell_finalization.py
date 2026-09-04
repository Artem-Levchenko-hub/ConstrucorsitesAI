"""Add durable Project Cell finalization records.

Revision ID: 0056_project_cell_finalization
Revises: 0055_project_cell_capacity_queue
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0056_project_cell_finalization"
down_revision: str | None = "0055_project_cell_capacity_queue"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "project_cell_proofs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("generation_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("fencing_epoch", sa.Integer(), nullable=False),
        sa.Column("proof_key", sa.CHAR(length=64), nullable=False),
        sa.Column("workspace_revision", sa.CHAR(length=64), nullable=False),
        sa.Column("dependency_digest", sa.CHAR(length=64), nullable=False),
        sa.Column("schema_data_digest", sa.CHAR(length=64), nullable=False),
        sa.Column("cell_manifest_digest", sa.CHAR(length=64), nullable=False),
        sa.Column("base_image_digest", sa.CHAR(length=64), nullable=False),
        sa.Column("toolchain_digest", sa.CHAR(length=64), nullable=False),
        sa.Column("resource_profile_version", sa.Text(), nullable=False),
        sa.Column("build_config_digest", sa.CHAR(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("fencing_epoch > 0", name=op.f("ck_project_cell_proofs_fencing_epoch_positive")),
        sa.CheckConstraint("proof_key ~ '^[0-9a-f]{64}$'", name=op.f("ck_project_cell_proofs_proof_key_hex")),
        sa.CheckConstraint(
            "workspace_revision ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_project_cell_proofs_workspace_revision_hex"),
        ),
        sa.CheckConstraint(
            "dependency_digest ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_project_cell_proofs_dependency_digest_hex"),
        ),
        sa.CheckConstraint(
            "schema_data_digest ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_project_cell_proofs_schema_data_digest_hex"),
        ),
        sa.CheckConstraint(
            "cell_manifest_digest ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_project_cell_proofs_cell_manifest_digest_hex"),
        ),
        sa.CheckConstraint(
            "base_image_digest ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_project_cell_proofs_base_image_digest_hex"),
        ),
        sa.CheckConstraint(
            "toolchain_digest ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_project_cell_proofs_toolchain_digest_hex"),
        ),
        sa.CheckConstraint(
            "build_config_digest ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_project_cell_proofs_build_config_digest_hex"),
        ),
        sa.ForeignKeyConstraint(
            ["generation_run_id"],
            ["generation_runs.id"],
            name=op.f("fk_project_cell_proofs_generation_run_id_generation_runs"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["project_cell_workspaces.id"],
            name=op.f("fk_project_cell_proofs_workspace_id_project_cell_workspaces"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_project_cell_proofs")),
        sa.UniqueConstraint(
            "workspace_id",
            "fencing_epoch",
            "proof_key",
            name="uq_project_cell_proofs_workspace_epoch_key",
        ),
    )

    op.create_table(
        "project_cell_proof_results",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("proof_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("dimension", sa.Text(), nullable=False),
        sa.Column("dimension_key", sa.CHAR(length=64), nullable=False),
        sa.Column("outcome", sa.Text(), nullable=False),
        sa.Column("operation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("artifact_ref", sa.Text(), nullable=True),
        sa.Column("detail_digest", sa.CHAR(length=64), nullable=False),
        sa.Column("redacted_detail", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "dimension IN ('bootstrap', 'fast_check', 'full_build', 'runtime', 'release')",
            name=op.f("ck_project_cell_proof_results_dimension_allowed"),
        ),
        sa.CheckConstraint(
            "dimension_key ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_project_cell_proof_results_dimension_key_hex"),
        ),
        sa.CheckConstraint(
            "outcome IN ('green', 'red')",
            name=op.f("ck_project_cell_proof_results_outcome_allowed"),
        ),
        sa.CheckConstraint(
            "detail_digest ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_project_cell_proof_results_detail_digest_hex"),
        ),
        sa.ForeignKeyConstraint(
            ["proof_id"],
            ["project_cell_proofs.id"],
            name=op.f("fk_project_cell_proof_results_proof_id_project_cell_proofs"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["project_cell_workspaces.id"],
            name=op.f("fk_project_cell_proof_results_workspace_id_project_cell_workspaces"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_project_cell_proof_results")),
        sa.UniqueConstraint(
            "workspace_id",
            "dimension",
            "dimension_key",
            name="uq_project_cell_proof_results_workspace_dimension_key",
        ),
    )

    op.create_table(
        "project_cell_activity_leases",
        sa.Column("operation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("generation_run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("state", sa.Text(), server_default="active", nullable=False),
        sa.Column("fencing_epoch", sa.Integer(), nullable=False),
        sa.Column("proof_key", sa.CHAR(length=64), nullable=True),
        sa.Column("phase", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deadline_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("log_bytes", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("redacted_diagnostic", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "kind IN ('command', 'tool', 'finalization', 'snapshot', 'promotion')",
            name=op.f("ck_project_cell_activity_leases_kind_allowed"),
        ),
        sa.CheckConstraint(
            "state IN ('active', 'completed', 'failed', 'timed_out', 'cancelled')",
            name=op.f("ck_project_cell_activity_leases_state_allowed"),
        ),
        sa.CheckConstraint(
            "fencing_epoch > 0",
            name=op.f("ck_project_cell_activity_leases_fencing_epoch_positive"),
        ),
        sa.CheckConstraint(
            "log_bytes >= 0",
            name=op.f("ck_project_cell_activity_leases_log_bytes_nonnegative"),
        ),
        sa.CheckConstraint(
            "proof_key IS NULL OR proof_key ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_project_cell_activity_leases_proof_key_hex"),
        ),
        sa.ForeignKeyConstraint(
            ["generation_run_id"],
            ["generation_runs.id"],
            name=op.f("fk_project_cell_activity_leases_generation_run_id_generation_runs"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["project_cell_workspaces.id"],
            name=op.f("fk_project_cell_activity_leases_workspace_id_project_cell_workspaces"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("operation_id", name=op.f("pk_project_cell_activity_leases")),
    )
    op.create_index(
        "uq_project_cell_activity_leases_one_active_per_workspace",
        "project_cell_activity_leases",
        ["workspace_id"],
        unique=True,
        postgresql_where=sa.text("state = 'active'"),
    )

    op.create_table(
        "generation_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("generation_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("message_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("seq", sa.BigInteger(), nullable=False),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), server_default="{}", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("seq > 0", name=op.f("ck_generation_events_seq_positive")),
        sa.ForeignKeyConstraint(
            ["generation_run_id"],
            ["generation_runs.id"],
            name=op.f("fk_generation_events_generation_run_id_generation_runs"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["message_id"],
            ["messages.id"],
            name=op.f("fk_generation_events_message_id_messages"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name=op.f("fk_generation_events_project_id_projects"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_generation_events")),
    )
    op.create_index(
        "uq_generation_events_generation_run_id_seq",
        "generation_events",
        ["generation_run_id", "seq"],
        unique=True,
    )
    op.create_index(
        "ix_generation_events_project_id_generation_run_id_seq",
        "generation_events",
        ["project_id", "generation_run_id", "seq"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_generation_events_project_id_generation_run_id_seq",
        table_name="generation_events",
    )
    op.drop_index("uq_generation_events_generation_run_id_seq", table_name="generation_events")
    op.drop_table("generation_events")
    op.drop_index(
        "uq_project_cell_activity_leases_one_active_per_workspace",
        table_name="project_cell_activity_leases",
    )
    op.drop_table("project_cell_activity_leases")
    op.drop_table("project_cell_proof_results")
    op.drop_table("project_cell_proofs")
