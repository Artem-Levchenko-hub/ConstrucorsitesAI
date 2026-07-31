"""Add the pending subscription checkout state and concurrency guard.

Revision ID: 0037_subscription_checkout
Revises: 0036_business_billing_accounts
Create Date: 2026-07-31
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0037_subscription_checkout"
down_revision = "0036_business_billing_accounts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint(
        "ck_subscriptions_status",
        "subscriptions",
        type_="check",
    )
    op.create_check_constraint(
        "ck_subscriptions_status",
        "subscriptions",
        "status IN ('pending_payment', 'trialing', 'active', 'past_due', "
        "'paused', 'canceled', 'expired')",
    )
    op.create_index(
        "uq_payments_account_pending_subscription",
        "payments",
        ["billing_account_id"],
        unique=True,
        postgresql_where=sa.text(
            "purpose = 'subscription_initial' "
            "AND status IN ('pending', 'waiting_for_capture')"
        ),
    )


def downgrade() -> None:
    op.execute(
        """
        UPDATE subscriptions
           SET status = 'canceled',
               canceled_at = COALESCE(canceled_at, now()),
               ended_at = COALESCE(ended_at, now())
         WHERE status = 'pending_payment'
        """
    )
    op.drop_index(
        "uq_payments_account_pending_subscription",
        table_name="payments",
    )
    op.drop_constraint(
        "ck_subscriptions_status",
        "subscriptions",
        type_="check",
    )
    op.create_check_constraint(
        "ck_subscriptions_status",
        "subscriptions",
        "status IN ('trialing', 'active', 'past_due', 'paused', 'canceled', 'expired')",
    )
