"""Project-scoped encrypted Integration Hub connections.

Revision ID: 0031_app_integrations
Revises: 0030_max_accounts_payments
Create Date: 2026-07-30
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0031_app_integrations"
down_revision = "0030_max_accounts_payments"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "app_integrations",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="active"),
        sa.Column(
            "public_config",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="{}",
        ),
        sa.Column("credentials_enc", sa.Text(), nullable=False),
        sa.Column(
            "capabilities",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="[]",
        ),
        sa.Column("account_label", sa.Text()),
        sa.Column("last_error", sa.Text()),
        sa.Column("verified_at", sa.DateTime(timezone=True)),
        sa.Column("last_checked_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "status IN ('active', 'error')",
            name="ck_app_integrations_status_allowed",
        ),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_app_integrations")),
        sa.UniqueConstraint(
            "project_id",
            "provider",
            name="uq_app_integrations_project_provider",
        ),
    )
    op.create_index(
        "ix_app_integrations_owner_id",
        "app_integrations",
        ["owner_id"],
        unique=False,
    )
    op.create_index(
        "ix_app_integrations_project_id",
        "app_integrations",
        ["project_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_app_integrations_project_id", table_name="app_integrations")
    op.drop_index("ix_app_integrations_owner_id", table_name="app_integrations")
    op.drop_table("app_integrations")
