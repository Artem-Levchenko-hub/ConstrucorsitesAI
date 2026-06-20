"""add language field to projects and default_language to users

Revision ID: 0018_projects_user_language
Revises: 0017_template_code
Create Date: 2026-06-20
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0018_projects_user_language"
down_revision = "0017_template_code"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("projects", sa.Column("language", sa.Text(), nullable=False, server_default="ru"))
    op.add_column("users", sa.Column("default_language", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "default_language")
    op.drop_column("projects", "language")
