"""Persist new-project enrollment without reclassifying legacy applications."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0062_project_cell_rollout"
down_revision: str | None = "0061_code_restorations"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("projects", sa.Column(
        "project_cell_enabled", sa.Boolean(), nullable=False, server_default=sa.false(),
    ))


def downgrade() -> None:
    connection = op.get_bind()
    # Serialize the decision with concurrent enrollment, not only the final DDL.
    if connection.dialect.name == "postgresql":
        connection.execute(sa.text("LOCK TABLE projects IN ACCESS EXCLUSIVE MODE"))
    if connection.execute(sa.text(
        "SELECT EXISTS (SELECT 1 FROM projects WHERE project_cell_enabled = true)"
    )).scalar():
        raise RuntimeError("Cannot remove runtime selection for enrolled Project Cells")
    op.drop_column("projects", "project_cell_enabled")
