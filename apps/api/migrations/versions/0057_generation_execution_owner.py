"""Keep generation execution ownership independent of the API lifecycle."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0057_generation_execution_owner"
down_revision: str | None = "0056_project_cell_finalization"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "generation_runs",
        sa.Column("execution_backend", sa.Text(), nullable=False, server_default="api"),
    )
    op.add_column(
        "generation_runs",
        sa.Column("execution_started_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "project_cell_operations",
        sa.Column("execution_run_id", postgresql.UUID(as_uuid=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("project_cell_operations", "execution_run_id")
    op.drop_column("generation_runs", "execution_started_at")
    op.drop_column("generation_runs", "execution_backend")
