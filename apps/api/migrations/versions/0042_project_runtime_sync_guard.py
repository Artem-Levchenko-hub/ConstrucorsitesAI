"""Add durable development-runtime synchronization guard.

Revision ID: 0042_project_runtime_sync_guard
Revises: 0041_generation_agent_state
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0042_project_runtime_sync_guard"
down_revision = "0041_generation_agent_state"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "projects",
        sa.Column(
            "runtime_sync_required",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
    )
    op.add_column(
        "projects",
        sa.Column(
            "runtime_sync_paths",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("projects", "runtime_sync_paths")
    op.drop_column("projects", "runtime_sync_required")
