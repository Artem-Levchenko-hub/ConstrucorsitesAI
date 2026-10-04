"""Add nullable per-call cost selection evidence, without historical backfill."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0078_usage_cost_provenance"
down_revision = "0077_free_owner_allowance"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("usage", sa.Column("cost_provenance", postgresql.JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column("usage", "cost_provenance")
