"""Background reconciliation schedule for restoration operations (AV19.1).

The controller finishes prepare/apply/cancel asynchronously. Until now the API
projection only advanced when a client polled the operation, so a cancel could sit
in ``reconciling`` forever and block every next restoration. These columns let the
worker re-observe due operations without any client GET.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0063_restoration_reconcile"
down_revision: str | None = "0062_project_cell_rollout"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "restorations",
        sa.Column("next_reconcile_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "restorations",
        sa.Column("reconcile_lease_until", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "restorations",
        sa.Column(
            "reconcile_attempts", sa.Integer(), nullable=False, server_default="0",
        ),
    )
    op.create_index(
        "ix_restorations_next_reconcile_at",
        "restorations",
        ["next_reconcile_at"],
        postgresql_where=sa.text("next_reconcile_at IS NOT NULL"),
    )
    # Operations already waiting on the controller are picked up by the first cycle.
    op.execute(
        sa.text(
            "UPDATE restorations SET next_reconcile_at = now() "
            "WHERE state IN ('preparing','checking','applying','reconciling')"
        )
    )


def downgrade() -> None:
    op.drop_index("ix_restorations_next_reconcile_at", table_name="restorations")
    op.drop_column("restorations", "reconcile_attempts")
    op.drop_column("restorations", "reconcile_lease_until")
    op.drop_column("restorations", "next_reconcile_at")
