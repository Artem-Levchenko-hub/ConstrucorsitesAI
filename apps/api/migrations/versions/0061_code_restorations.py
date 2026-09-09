"""Durable owner-only code restoration requests and fenced activation evidence."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0061_code_restorations"
down_revision: str | None = "0060_project_versions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "restorations",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column(
            "project_id",
            sa.UUID(),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "owner_id", sa.UUID(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "workspace_id",
            sa.UUID(),
            sa.ForeignKey("project_cell_workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("source_version_id", sa.UUID(), nullable=False),
        sa.Column("source_snapshot_id", sa.UUID(), nullable=False),
        sa.Column("base_draft_snapshot_id", sa.UUID(), nullable=False),
        sa.Column("target_commit_sha", sa.Text(), nullable=False),
        sa.Column("base_commit_sha", sa.Text(), nullable=False),
        sa.Column("idempotency_key", sa.Text(), nullable=False),
        sa.Column("request_digest", sa.Text(), nullable=False),
        sa.Column("state", sa.Text(), nullable=False),
        sa.Column("phase", sa.Text(), nullable=False),
        sa.Column("planned_commit_sha", sa.Text(), nullable=True),
        sa.Column("apply_idempotency_key", sa.Text(), nullable=True),
        sa.Column("apply_digest", sa.Text(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("runtime_revision", sa.Integer(), nullable=False),
        sa.Column("fencing_epoch", sa.Integer(), nullable=False),
        sa.Column("prior_fencing_epoch", sa.Integer(), nullable=True),
        sa.Column("candidate_id", sa.UUID(), nullable=True),
        sa.Column("applied_version_id", sa.UUID(), nullable=True),
        sa.Column("applied_snapshot_id", sa.UUID(), nullable=True),
        sa.Column("request_payload", postgresql.JSONB(), nullable=False),
        sa.Column("runtime_result", postgresql.JSONB(), nullable=True),
        sa.Column("report", postgresql.JSONB(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("project_id", "idempotency_key", name="uq_restorations_request"),
    )
    op.create_index(
        "uq_restorations_active_project",
        "restorations",
        ["project_id"],
        unique=True,
        postgresql_where=sa.text(
            "state IN ('preparing','checking','ready','needs_changes','applying','reconciling')"
        ),
    )


def downgrade() -> None:
    op.drop_table("restorations")
