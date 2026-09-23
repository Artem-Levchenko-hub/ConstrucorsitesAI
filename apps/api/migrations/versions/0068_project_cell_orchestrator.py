"""Bind every Project Cell to the orchestrator host it lives on (Phase 3, stage B).

Existing cells all live on the first host, named `core`; new cells are placed
on the least-loaded enabled host at creation and never move.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0068_project_cell_orchestrator"
down_revision: str | None = "0067_restoration_adaptation_activation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "project_cell_workspaces",
        sa.Column("orchestrator", sa.Text(), nullable=False, server_default="core"),
    )
    op.create_index(
        "ix_project_cell_workspaces_orchestrator_live",
        "project_cell_workspaces",
        ["orchestrator", "state"],
        postgresql_where=sa.text("deleted_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "ix_project_cell_workspaces_orchestrator_live", table_name="project_cell_workspaces"
    )
    op.drop_column("project_cell_workspaces", "orchestrator")
