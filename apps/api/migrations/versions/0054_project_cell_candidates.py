"""Add fenced Project Cell release candidates.

Revision ID: 0054_project_cell_candidates
Revises: 0053_project_cell_operation_fencing
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0054_project_cell_candidates"
down_revision: str | None = "0053_project_cell_operation_fencing"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "project_cell_candidates",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("generation_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("fencing_epoch", sa.Integer(), nullable=False),
        sa.Column("source_revision", sa.Text(), nullable=False),
        sa.Column("migration_digest", sa.Text(), nullable=False),
        sa.Column("database_backup_ref", sa.Text(), nullable=False),
        sa.Column("build_ref", sa.Text(), nullable=False),
        sa.Column("verification_ref", sa.Text(), nullable=False),
        sa.Column(
            "expected_accepted_candidate_id", postgresql.UUID(as_uuid=True), nullable=True
        ),
        sa.Column("status", sa.Text(), server_default="prepared", nullable=False),
        sa.Column("cancelled", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("promoted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("fencing_epoch > 0", name="ck_project_cell_candidates_fencing_epoch_positive"),
        sa.CheckConstraint(
            "status IN ('prepared', 'accepted', 'rejected', 'cancelled')",
            name="ck_project_cell_candidates_status_allowed",
        ),
        sa.CheckConstraint(
            "source_revision ~ '^[0-9a-f]{40}([0-9a-f]{24})?$'",
            name="ck_project_cell_candidates_source_revision_hex",
        ),
        sa.CheckConstraint(
            "migration_digest ~ '^[0-9a-f]{64}$'",
            name="ck_project_cell_candidates_migration_digest_hex",
        ),
        sa.CheckConstraint(
            "database_backup_ref ~ '^database-backup/sha256/[0-9a-f]{64}$'",
            name="ck_project_cell_candidates_database_backup_ref_content_addressed",
        ),
        sa.CheckConstraint(
            "build_ref ~ '^build/sha256/[0-9a-f]{64}$'",
            name="ck_project_cell_candidates_build_ref_content_addressed",
        ),
        sa.CheckConstraint(
            "verification_ref ~ '^verification/sha256/[0-9a-f]{64}$'",
            name="ck_project_cell_candidates_verification_ref_content_addressed",
        ),
        sa.CheckConstraint(
            "(status = 'cancelled') = cancelled",
            name="ck_project_cell_candidates_cancelled_status_consistent",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"], ["project_cell_workspaces.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["generation_run_id"], ["generation_runs.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["expected_accepted_candidate_id"],
            ["project_cell_candidates.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "uq_project_cell_candidates_one_accepted",
        "project_cell_candidates",
        ["workspace_id"],
        unique=True,
        postgresql_where=sa.text("status = 'accepted'"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_project_cell_candidates_one_accepted",
        table_name="project_cell_candidates",
    )
    op.drop_table("project_cell_candidates")
