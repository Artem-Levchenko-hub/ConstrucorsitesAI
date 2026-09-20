"""Persist proof-bound adaptive restoration activation intents and receipts."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0067_restoration_adaptation_activation"
down_revision: str | None = "0066_restoration_adapting_state"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("restorations", sa.Column("activation_id", sa.UUID(), nullable=True))
    op.add_column(
        "restorations", sa.Column("activation_offer", postgresql.JSONB(), nullable=True)
    )
    op.add_column(
        "restorations", sa.Column("activation_offer_digest", sa.Text(), nullable=True)
    )
    op.add_column(
        "restorations", sa.Column("activation_request", postgresql.JSONB(), nullable=True)
    )
    op.add_column(
        "restorations", sa.Column("activation_request_digest", sa.Text(), nullable=True)
    )
    op.add_column(
        "restorations", sa.Column("adaptation_planned_commit_sha", sa.Text(), nullable=True)
    )
    op.add_column(
        "restorations", sa.Column("activation_receipt", postgresql.JSONB(), nullable=True)
    )
    op.add_column(
        "restorations", sa.Column("activation_receipt_digest", sa.Text(), nullable=True)
    )
    op.add_column(
        "restorations",
        sa.Column(
            "activation_effects_admitted",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.add_column(
        "restorations",
        sa.Column("activation_cancel_requested_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "restorations",
        sa.Column("activation_settled_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "restorations", sa.Column("activation_notification_state", sa.Text(), nullable=True)
    )
    op.create_check_constraint(
        "ck_restorations_activation_notification_state",
        "restorations",
        "activation_notification_state IS NULL OR "
        "activation_notification_state IN ('pending','delivered')",
    )
    op.create_check_constraint(
        "ck_restorations_activation_branch",
        "restorations",
        "activation_id IS NULL OR selected_branch = 'adaptive'",
    )
    op.create_index(
        "uq_restorations_activation_id",
        "restorations",
        ["activation_id"],
        unique=True,
        postgresql_where=sa.text("activation_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_restorations_activation_id", table_name="restorations")
    op.drop_constraint(
        "ck_restorations_activation_branch", "restorations", type_="check"
    )
    op.drop_constraint(
        "ck_restorations_activation_notification_state", "restorations", type_="check"
    )
    for name in (
        "activation_notification_state",
        "activation_settled_at",
        "activation_cancel_requested_at",
        "activation_effects_admitted",
        "activation_receipt_digest",
        "activation_receipt",
        "activation_request_digest",
        "adaptation_planned_commit_sha",
        "activation_request",
        "activation_offer_digest",
        "activation_offer",
        "activation_id",
    ):
        op.drop_column("restorations", name)
