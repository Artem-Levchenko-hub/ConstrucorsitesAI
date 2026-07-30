"""Structured MAX Mini App configuration.

Revision ID: 0029_max_project_configs
Revises: 0028_max_miniapps
Create Date: 2026-07-30
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0029_max_project_configs"
down_revision = "0028_max_miniapps"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "max_project_configs",
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("config_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("config", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("synced_snapshot_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["synced_snapshot_id"], ["snapshots.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("project_id", name=op.f("pk_max_project_configs")),
    )
    op.create_index(
        "ix_max_project_configs_owner_id",
        "max_project_configs",
        ["owner_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_max_project_configs_owner_id", table_name="max_project_configs")
    op.drop_table("max_project_configs")
