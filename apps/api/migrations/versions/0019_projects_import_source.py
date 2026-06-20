"""add source / external_repo fields to projects for GitHub import

Revision ID: 0019_projects_import_source
Revises: 0018_projects_user_language
Create Date: 2026-06-20
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0019_projects_import_source"
down_revision = "0018_projects_user_language"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "projects",
        sa.Column("source", sa.Text(), nullable=False, server_default="native"),
    )
    op.add_column(
        "projects",
        sa.Column("external_repo_url", sa.Text(), nullable=True),
    )
    op.add_column(
        "projects",
        sa.Column("external_repo_ref", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("projects", "external_repo_ref")
    op.drop_column("projects", "external_repo_url")
    op.drop_column("projects", "source")
