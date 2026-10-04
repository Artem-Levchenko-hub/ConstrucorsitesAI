"""Defer prospective generation wallet settlement until a proven terminal build."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0075_generation_billing"
down_revision = "0074_usage_settlements"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "generation_billing_policies",
        sa.Column(
            "run_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("generation_runs.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("is_free", sa.Boolean(), nullable=False),
        sa.Column("version", sa.Text(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_table(
        "generation_billing_intents",
        sa.Column(
            "run_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("generation_runs.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "user_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "billing_account_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("billing_accounts.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("outcome", sa.Text(), nullable=False),
        sa.Column("amount_rub", sa.Numeric(12, 4), nullable=False),
        sa.Column("usage_ids", pg.JSONB(), nullable=False),
        sa.Column("accepted_snapshot_id", pg.UUID(as_uuid=True)),
        sa.Column("receipt", pg.JSONB(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("outcome IN ('billable','waived')", name="outcome"),
        sa.CheckConstraint(
            "amount_rub >= 0 AND (outcome = 'billable' OR amount_rub = 0)", name="amount"
        ),
        sa.CheckConstraint(
            "outcome != 'billable' OR (billing_account_id IS NOT NULL AND accepted_snapshot_id IS NOT NULL)",
            name="billable_binding",
        ),
    )
    op.create_table(
        "generation_billing_outbox",
        sa.Column(
            "run_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("generation_billing_intents.run_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "next_attempt_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("settled_at", sa.DateTime(timezone=True)),
    )
    op.create_index(
        "ix_generation_billing_outbox_next_attempt_at",
        "generation_billing_outbox",
        ["next_attempt_at"],
    )
    op.drop_constraint(
        op.f("ck_usage_settlements_ck_usage_settlements_status"), "usage_settlements", type_="check"
    )
    op.create_check_constraint(
        "ck_usage_settlements_status",
        "usage_settlements",
        "status IN ('settled','free','unpaid','deferred')",
    )


def downgrade() -> None:
    # Never erase deferred financial receipts to satisfy an older schema.
    op.execute(
        "DO $$ BEGIN IF EXISTS(SELECT 1 FROM usage_settlements WHERE status='deferred') OR EXISTS(SELECT 1 FROM generation_billing_intents) THEN RAISE EXCEPTION 'deferred receipts require reconciliation before downgrade'; END IF; END $$"
    )
    op.drop_constraint(
        op.f("ck_usage_settlements_ck_usage_settlements_status"), "usage_settlements", type_="check"
    )
    op.create_check_constraint(
        "ck_usage_settlements_status", "usage_settlements", "status IN ('settled','free','unpaid')"
    )
    op.drop_table("generation_billing_outbox")
    op.drop_table("generation_billing_intents")
    op.drop_table("generation_billing_policies")
