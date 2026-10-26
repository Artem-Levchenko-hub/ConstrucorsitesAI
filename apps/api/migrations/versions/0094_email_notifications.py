"""Durable subscription notification outbox, with no historical backfill."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0094_email_notifications"
down_revision = "0093_owner_fonts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "email_notifications",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("event_key", sa.Text(), nullable=False),
        sa.Column("event_type", sa.Text(), nullable=False),
        *(
            sa.Column(name, postgresql.UUID(as_uuid=True), nullable=False)
            for name in ("user_id", "billing_account_id", "subscription_id", "payment_id")
        ),
        sa.Column("period_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("plan_name", sa.Text(), nullable=False),
        sa.Column("amount_rub", sa.Numeric(12, 2), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="pending"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "available_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failure_category", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint(
            "event_type IN ('paid', 'ending_3', 'ending_1')",
            name=op.f("ck_email_notifications_event_type"),
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'sending', 'sent', 'skipped', 'uncertain')",
            name=op.f("ck_email_notifications_status"),
        ),
        sa.CheckConstraint("attempts >= 0", name=op.f("ck_email_notifications_attempts")),
        *(
            sa.ForeignKeyConstraint([column], [target + ".id"], ondelete="CASCADE")
            for column, target in (
                ("user_id", "users"),
                ("billing_account_id", "billing_accounts"),
                ("subscription_id", "subscriptions"),
                ("payment_id", "payments"),
            )
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("event_key"),
    )
    op.create_index(
        "ix_email_notifications_ready", "email_notifications", ["status", "available_at"]
    )
    # Frozen upgrade-time rights for an already provisioned commerce worker.
    # Current/repeatable bootstrap lives in billing-email-grants.sql; migrations
    # must remain self-contained when deployed without rerunning host bootstrap.
    op.execute("""
        DO $$
        BEGIN
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'max_billing') THEN
            GRANT SELECT, INSERT, UPDATE ON TABLE public.email_notifications TO max_billing;
            GRANT SELECT (id, user_id, purpose, used_at, expires_at)
              ON TABLE public.auth_tokens TO max_billing;
            GRANT SELECT (id, user_id, provider, provider_user_id, email)
              ON TABLE public.user_identities TO max_billing;
          END IF;
        END
        $$;
    """)


def downgrade() -> None:
    op.drop_table("email_notifications")
