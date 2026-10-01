"""Durable settlement of completed provider receipts without changing historical usage."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0074_usage_settlements"
down_revision = "0073_generation_deployment_drain"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "usage_settlements",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "billing_account_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("billing_accounts.id", ondelete="RESTRICT"),
        ),
        sa.Column("provider_scope", sa.Text(), nullable=False),
        sa.Column("provider_request_id", sa.Text()),
        sa.Column("receipt_hash", sa.Text(), nullable=False),
        sa.Column(
            "usage_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("usage.id", ondelete="RESTRICT"),
            nullable=False,
            unique=True,
        ),
        sa.Column(
            "wallet_charge_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("wallet_charges.id", ondelete="RESTRICT"),
            unique=True,
        ),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint(
            "user_id",
            "provider_scope",
            "provider_request_id",
            name="uq_usage_settlements_provider_receipt",
        ),
        sa.CheckConstraint(
            "status IN ('settled', 'free', 'unpaid')", name="ck_usage_settlements_status"
        ),
        sa.CheckConstraint(
            "(status = 'settled') = (wallet_charge_id IS NOT NULL)",
            name="ck_usage_settlements_charge",
        ),
    )


def downgrade() -> None:
    op.drop_table("usage_settlements")
