"""add messages.agent_steps (persisted agentic transcript)

The agentic builder pushes a live step list over WebSocket (`agent.step`), but
those events were client-only — after a reload the transcript vanished. We now
persist the accumulated steps on the *assistant* message so the chat history can
re-render "what the agent did" (drill-in detail included) after a reload. The
column is JSONB and nullable — existing rows and non-agent replies (edits, text
turns) simply hold NULL, so the add is non-blocking on a large table.

Revision ID: 0023_messages_agent_steps
Revises: 0022_attestations
Create Date: 2026-07-17
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0023_messages_agent_steps"
down_revision = "0022_attestations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "messages",
        sa.Column(
            "agent_steps",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("messages", "agent_steps")
