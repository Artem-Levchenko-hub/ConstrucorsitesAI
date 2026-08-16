"""persist observable native-agent plans and checkpoints

Revision ID: 0041_generation_agent_state
Revises: 0040_generation_usage_trace
Create Date: 2026-08-02
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0041_generation_agent_state"
down_revision: str | None = "0040_generation_usage_trace"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "generation_runs",
        sa.Column(
            "agent_state",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )


def downgrade() -> None:
    op.drop_column("generation_runs", "agent_state")
