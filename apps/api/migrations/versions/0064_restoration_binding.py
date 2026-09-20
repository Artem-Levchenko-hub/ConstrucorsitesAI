"""Persist a versioned, controller-observed restoration source binding."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0064_restoration_binding"
down_revision: str | None = "0063_restoration_reconcile"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Nullable by design: historical rows are not assigned invented observations.
    op.add_column(
        "restorations", sa.Column("source_binding", postgresql.JSONB(), nullable=True)
    )
    op.add_column(
        "restorations", sa.Column("source_binding_digest", sa.Text(), nullable=True)
    )
    op.create_check_constraint(
        "ck_restorations_source_binding_pair",
        "restorations",
        "(source_binding IS NULL) = (source_binding_digest IS NULL)",
    )
    op.create_check_constraint(
        "ck_restorations_source_binding_digest",
        "restorations",
        "source_binding_digest IS NULL OR source_binding_digest ~ '^[0-9a-f]{64}$'",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_restorations_source_binding_digest", "restorations", type_="check"
    )
    op.drop_constraint(
        "ck_restorations_source_binding_pair", "restorations", type_="check"
    )
    op.drop_column("restorations", "source_binding_digest")
    op.drop_column("restorations", "source_binding")
