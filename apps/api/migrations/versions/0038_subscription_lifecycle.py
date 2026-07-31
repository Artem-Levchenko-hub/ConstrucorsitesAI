"""Persist renewal consent and canonical runtime keep-alive state.

Revision ID: 0038_subscription_lifecycle
Revises: 0037_subscription_checkout
Create Date: 2026-07-31
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0038_subscription_lifecycle"
down_revision = "0037_subscription_checkout"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "subscriptions",
        sa.Column("renewal_consent_version", sa.Text(), nullable=True),
    )
    op.add_column(
        "subscriptions",
        sa.Column("renewal_consented_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "projects",
        sa.Column(
            "keep_alive_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.create_index(
        "uq_payments_subscription_pending_renewal",
        "payments",
        ["subscription_id"],
        unique=True,
        postgresql_where=sa.text(
            "purpose = 'subscription_renewal' "
            "AND status IN ('pending', 'waiting_for_capture')"
        ),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_payments_subscription_pending_renewal",
        table_name="payments",
    )
    op.drop_column("projects", "keep_alive_enabled")
    op.drop_column("subscriptions", "renewal_consented_at")
    op.drop_column("subscriptions", "renewal_consent_version")
