"""add users.free_generations_used (first-N free generations counter)

Counts how many free "wow-effect" generations a user has consumed. While the
value is below FREE_GENERATION_LIMIT (core/config.py), POST /prompt skips the
wallet floor check and the gateway skips the wallet debit (metadata.free=true),
so the first N generations cost the user nothing. Incremented after each
successful free generation.

Add is fast (integer column with server-default 0, no backfill needed —
existing rows pick up the default).

Revision ID: 0009_users_free_generations_used
Revises: 0008_projects_image_gen_enabled
Create Date: 2026-05-28
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0009_users_free_generations_used"
down_revision = "0008_projects_image_gen_enabled"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "free_generations_used",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )


def downgrade() -> None:
    op.drop_column("users", "free_generations_used")
