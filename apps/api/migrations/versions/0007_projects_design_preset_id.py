"""add projects.design_preset_id (Awwwards-tier design preset, auto-classified)

Nullable text column: stores one of the keys from
``omnia_api.services.design_presets.PRESETS`` (e.g. ``saas-product``,
``festival-brutalist``). Set by auto-classifier on project create (heuristic
keyword match) or on first prompt (Haiku LLM fallback). NULL = not yet
classified — classifier will fill on next prompt.

Add is fast (nullable column, no default backfill, no constraint), safe on
large tables. Indexed for cheap analytics ("how many projects use preset X").

Revision ID: 0007_projects_design_preset_id
Revises: 0006_users_github_oauth
Create Date: 2026-05-24
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0007_projects_design_preset_id"
down_revision = "0006_users_github_oauth"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "projects",
        sa.Column("design_preset_id", sa.Text(), nullable=True),
    )
    op.create_index(
        "ix_projects_design_preset_id",
        "projects",
        ["design_preset_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_projects_design_preset_id", table_name="projects")
    op.drop_column("projects", "design_preset_id")
