"""MAX Mini Apps template and encrypted bot integrations.

Revision ID: 0028_max_miniapps
Revises: 0027_hero_media_mvp
Create Date: 2026-07-30
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0028_max_miniapps"
down_revision = "0027_hero_media_mvp"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("ck_projects_template_allowed", "projects", type_="check")
    op.create_check_constraint(
        "ck_projects_template_allowed",
        "projects",
        "template IN ('blank', 'landing', 'portfolio', 'blog', 'fullstack', "
        "'nextjs_entities', 'spa', 'tgbot', 'api', 'code', 'realtime', 'max_miniapp')",
    )
    op.create_table(
        "max_integrations",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("bot_token_enc", sa.Text(), nullable=False),
        sa.Column("webhook_secret_enc", sa.Text(), nullable=False),
        sa.Column("bot_id", sa.Text(), nullable=True),
        sa.Column("bot_name", sa.Text(), nullable=True),
        sa.Column("bot_username", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False, server_default="verified"),
        sa.Column("app_url", sa.Text(), nullable=True),
        sa.Column("webhook_url", sa.Text(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint(
            "status IN ('verified', 'active', 'error')",
            name="ck_max_integrations_status_allowed",
        ),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_max_integrations")),
        sa.UniqueConstraint("project_id", name=op.f("uq_max_integrations_project_id")),
    )
    op.create_index("ix_max_integrations_owner_id", "max_integrations", ["owner_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_max_integrations_owner_id", table_name="max_integrations")
    op.drop_table("max_integrations")
    op.drop_constraint("ck_projects_template_allowed", "projects", type_="check")
    op.create_check_constraint(
        "ck_projects_template_allowed",
        "projects",
        "template IN ('blank', 'landing', 'portfolio', 'blog', 'fullstack', "
        "'nextjs_entities', 'spa', 'tgbot', 'api', 'code', 'realtime')",
    )
