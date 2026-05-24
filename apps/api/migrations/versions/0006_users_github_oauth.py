"""add users.github_* (GitHub OAuth connection for "Push to GitHub")

Stores a per-user GitHub OAuth token (Fernet-encrypted in github_token_enc) plus
the github login + granted scope, so a user can push generated projects to their
own GitHub. All columns nullable — existing users hold NULL until they connect, so
the add is non-blocking on a large table.

Revision ID: 0006_users_github_oauth
Revises: 0005_messages_selected_elements
Create Date: 2026-05-23
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0006_users_github_oauth"
down_revision = "0005_messages_selected_elements"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("github_login", sa.Text(), nullable=True))
    op.add_column("users", sa.Column("github_token_enc", sa.Text(), nullable=True))
    op.add_column("users", sa.Column("github_scope", sa.Text(), nullable=True))
    op.add_column(
        "users",
        sa.Column("github_connected_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("users", "github_connected_at")
    op.drop_column("users", "github_scope")
    op.drop_column("users", "github_token_enc")
    op.drop_column("users", "github_login")
