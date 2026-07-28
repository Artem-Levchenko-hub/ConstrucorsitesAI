"""hero media MVP tables

Revision ID: 0027_hero_media_mvp
Revises: 0026_secure_deploy_targets
Create Date: 2026-07-28
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0027_hero_media_mvp"
down_revision = "0026_secure_deploy_targets"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "hero_media_assets",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("asset_role", sa.Text(), nullable=False),
        sa.Column("media_kind", sa.Text(), nullable=False),
        sa.Column("storage_url", sa.Text(), nullable=False),
        sa.Column("storage_key", sa.Text(), nullable=True),
        sa.Column("mime_type", sa.Text(), nullable=False),
        sa.Column("original_filename", sa.Text(), nullable=True),
        sa.Column("width", sa.Integer(), nullable=True),
        sa.Column("height", sa.Integer(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("bytes_size", sa.Integer(), nullable=True),
        sa.Column("consent_confirmed", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("moderation_status", sa.Text(), nullable=False, server_default="unreviewed"),
        sa.Column("details", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.CheckConstraint(
            "asset_role IN ('source', 'poster', 'clip')",
            name="ck_hero_media_assets_asset_role_allowed",
        ),
        sa.CheckConstraint(
            "media_kind IN ('image', 'video')",
            name="ck_hero_media_assets_media_kind_allowed",
        ),
        sa.CheckConstraint(
            "moderation_status IN ('unreviewed', 'approved', 'rejected', 'needs_review')",
            name="ck_hero_media_assets_moderation_status_allowed",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_hero_media_assets")),
    )
    op.create_index(
        op.f("ix_hero_media_assets_project_id"),
        "hero_media_assets",
        ["project_id"],
        unique=False,
    )

    op.create_table(
        "hero_media_briefs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="draft"),
        sa.Column("input_prompt", sa.Text(), nullable=False),
        sa.Column("business_type", sa.Text(), nullable=True),
        sa.Column("style_preference", sa.Text(), nullable=True),
        sa.Column("focus_preference", sa.Text(), nullable=True),
        sa.Column("motion_preference", sa.Text(), nullable=True),
        sa.Column("asset_ids", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("recommended_plan_kind", sa.Text(), nullable=False),
        sa.Column("selected_plan_kind", sa.Text(), nullable=True),
        sa.Column("plan", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.CheckConstraint(
            "status IN ('draft', 'approved', 'rejected')",
            name="ck_hero_media_briefs_status_allowed",
        ),
        sa.CheckConstraint(
            "recommended_plan_kind IN ('static', 'product-demo', 'motion', 'video', 'cinematic')",
            name="ck_hero_media_briefs_recommended_plan_kind_allowed",
        ),
        sa.CheckConstraint(
            "selected_plan_kind IS NULL OR selected_plan_kind IN ('static', 'product-demo', 'motion', 'video', 'cinematic')",
            name="ck_hero_media_briefs_selected_plan_kind_allowed",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_hero_media_briefs")),
    )
    op.create_index(
        op.f("ix_hero_media_briefs_project_id"),
        "hero_media_briefs",
        ["project_id"],
        unique=False,
    )

    op.create_table(
        "hero_media_renders",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("brief_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="queued"),
        sa.Column("media_plan", sa.Text(), nullable=False),
        sa.Column("status_detail", sa.Text(), nullable=True),
        sa.Column("provider_summary", sa.Text(), nullable=True),
        sa.Column("poster_asset_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("video_asset_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("applied_snapshot_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("bundle", postgresql.JSONB(), nullable=True),
        sa.Column("progress_log", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["brief_id"], ["hero_media_briefs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["applied_snapshot_id"], ["snapshots.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["poster_asset_id"], ["hero_media_assets.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["video_asset_id"], ["hero_media_assets.id"], ondelete="SET NULL"),
        sa.CheckConstraint(
            "status IN ('queued', 'rendering', 'assembling', 'completed', 'failed')",
            name="ck_hero_media_renders_status_allowed",
        ),
        sa.CheckConstraint(
            "media_plan IN ('static', 'product-demo', 'motion', 'video', 'cinematic')",
            name="ck_hero_media_renders_media_plan_allowed",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_hero_media_renders")),
    )
    op.create_index(
        op.f("ix_hero_media_renders_project_id"),
        "hero_media_renders",
        ["project_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_hero_media_renders_project_id"), table_name="hero_media_renders")
    op.drop_table("hero_media_renders")
    op.drop_index(op.f("ix_hero_media_briefs_project_id"), table_name="hero_media_briefs")
    op.drop_table("hero_media_briefs")
    op.drop_index(op.f("ix_hero_media_assets_project_id"), table_name="hero_media_assets")
    op.drop_table("hero_media_assets")
