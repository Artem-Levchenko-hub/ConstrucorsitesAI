"""Persist the owner-selected restoration execution policy and branch."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0065_restoration_execution_policy"
down_revision: str | None = "0064_restoration_binding"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Existing operations remain manual; a migration must never invent consent
    # to activate a candidate that previously waited for a second click.
    op.add_column(
        "restorations",
        sa.Column(
            "execution_policy", sa.Text(), nullable=False, server_default="manual"
        ),
    )
    op.add_column(
        "restorations", sa.Column("selected_branch", sa.Text(), nullable=True)
    )
    op.add_column(
        "restorations",
        sa.Column(
            "adaptation_run_id",
            sa.UUID(),
            nullable=True,
        ),
    )
    op.create_check_constraint(
        "ck_restorations_execution_policy",
        "restorations",
        "execution_policy IN ('manual','automatic_when_safe')",
    )
    op.create_check_constraint(
        "ck_restorations_selected_branch",
        "restorations",
        "selected_branch IS NULL OR selected_branch IN ('exact','adaptive')",
    )
    op.create_check_constraint(
        "ck_restorations_adaptation_branch",
        "restorations",
        "adaptation_run_id IS NULL OR selected_branch = 'adaptive'",
    )
    op.create_index(
        "uq_restorations_adaptation_run_id",
        "restorations",
        ["adaptation_run_id"],
        unique=True,
        postgresql_where=sa.text("adaptation_run_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_restorations_adaptation_run_id", table_name="restorations")
    op.drop_constraint(
        "ck_restorations_adaptation_branch", "restorations", type_="check"
    )
    op.drop_constraint(
        "ck_restorations_selected_branch", "restorations", type_="check"
    )
    op.drop_constraint(
        "ck_restorations_execution_policy", "restorations", type_="check"
    )
    op.drop_column("restorations", "adaptation_run_id")
    op.drop_column("restorations", "selected_branch")
    op.drop_column("restorations", "execution_policy")
