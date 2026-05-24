"""add messages.selected_elements (select-mode picker context)

Select-mode lets a user pick elements in the live preview and attach a
per-element comment; we persist that structured context on the *user*
message so the chat history can re-render the chips after a reload. The
column is JSONB and nullable — existing rows and messages sent without a
selection simply hold NULL, so the add is non-blocking on a large table.

Revision ID: 0005_messages_selected_elements
Revises: 0004_template_fullstack
Create Date: 2026-05-23
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0005_messages_selected_elements"
down_revision = "0004_template_fullstack"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "messages",
        sa.Column(
            "selected_elements",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("messages", "selected_elements")
