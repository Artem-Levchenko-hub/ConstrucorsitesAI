"""Treat an accepted adaptive restoration as active until its run finishes."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0066_restoration_adapting_state"
down_revision: str | None = "0065_restoration_execution_policy"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_index("uq_restorations_active_project", table_name="restorations")
    op.create_index(
        "uq_restorations_active_project",
        "restorations",
        ["project_id"],
        unique=True,
        postgresql_where=sa.text(
            "state IN ('preparing','checking','ready','needs_changes','adapting',"
            "'applying','reconciling')"
        ),
    )


def downgrade() -> None:
    op.drop_index("uq_restorations_active_project", table_name="restorations")
    op.create_index(
        "uq_restorations_active_project",
        "restorations",
        ["project_id"],
        unique=True,
        postgresql_where=sa.text(
            "state IN ('preparing','checking','ready','needs_changes','applying','reconciling')"
        ),
    )
