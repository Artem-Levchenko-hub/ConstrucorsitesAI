"""add attestations table — saved runtime-gate verdict per build (fresh-plan Step 3)

Revision ID: 0022_attestations
Revises: 0021_template_realtime
Create Date: 2026-07-06
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0022_attestations"
down_revision = "0021_template_realtime"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "attestations",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("snapshot_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("commit_sha", sa.Text(), nullable=True),
        sa.Column("stack", sa.Text(), nullable=True),
        sa.Column(
            "overall_passed",
            sa.Boolean(),
            server_default=sa.false(),
            nullable=False,
        ),
        sa.Column("digest", sa.Text(), nullable=False),
        sa.Column("gates", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["snapshot_id"], ["snapshots.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_attestations_project_created", "attestations", ["project_id", "created_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_attestations_project_created", table_name="attestations")
    op.drop_table("attestations")
