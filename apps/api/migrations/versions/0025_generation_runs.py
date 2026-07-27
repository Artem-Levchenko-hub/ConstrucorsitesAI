"""Durable idempotency, single-flight generation state and cancellation.

Revision ID: 0025_generation_runs
Revises: 0024_deploy_targets_domains
Create Date: 2026-07-27
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0025_generation_runs"
down_revision = "0024_deploy_targets_domains"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "generation_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("assistant_message_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("idempotency_key", sa.Text(), nullable=False),
        sa.Column("prompt_hash", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), server_default="pending", nullable=False),
        sa.Column("response_mode", sa.Text(), nullable=True),
        sa.Column("response_payload", postgresql.JSONB(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('pending', 'running', 'cancel_requested', "
            "'cancelled', 'completed', 'failed')",
            name="ck_generation_runs_status_allowed",
        ),
        sa.ForeignKeyConstraint(
            ["assistant_message_id"],
            ["messages.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "project_id",
            "idempotency_key",
            name="uq_generation_runs_project_id_idempotency_key",
        ),
    )
    op.create_index(
        "uq_generation_runs_one_active_per_project",
        "generation_runs",
        ["project_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('pending', 'running', 'cancel_requested')"),
    )
    op.create_index(
        "ix_generation_runs_project_id_created_at",
        "generation_runs",
        ["project_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_generation_runs_project_id_created_at", table_name="generation_runs")
    op.drop_index("uq_generation_runs_one_active_per_project", table_name="generation_runs")
    op.drop_table("generation_runs")
