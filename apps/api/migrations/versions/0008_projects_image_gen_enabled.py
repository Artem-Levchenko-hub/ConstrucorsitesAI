"""add projects.image_gen_enabled (toggle for auto image generation)

Boolean flag controlling whether the post-generation image-resolver runs on
this project. When TRUE (default), the resolver scans assistant-generated
files for ``<img data-omnia-gen="...">`` tags, generates real images via the
gateway's /v1/images/generations endpoint, uploads them to MinIO and rewrites
the tags with public src URLs. When FALSE, tags are left untouched (UI shows
broken / placeholder image).

Add is fast (boolean column with server-default, no backfill needed on small
table; existing rows pick up the default value).

Revision ID: 0008_projects_image_gen_enabled
Revises: 0007_projects_design_preset_id
Create Date: 2026-05-24
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0008_projects_image_gen_enabled"
down_revision = "0007_projects_design_preset_id"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "projects",
        sa.Column(
            "image_gen_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
    )


def downgrade() -> None:
    op.drop_column("projects", "image_gen_enabled")
