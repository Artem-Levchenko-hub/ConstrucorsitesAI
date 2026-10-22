"""Add durable included generations and new immutable Pro terms."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0089_pro_generation_allowance"
down_revision = "0088_generation_recovery_effects"
branch_labels = None
depends_on = None

PRO_V2 = "00000000-0000-4000-8000-000000000006"
PRO_V3 = "00000000-0000-4000-8000-000000000008"


def upgrade() -> None:
    op.create_table(
        "generation_allowances",
        # No FK to runs/projects: deleting an app must not restore paid quota.
        sa.Column("run_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "billing_account_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("billing_accounts.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "subscription_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("subscriptions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("period_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("period_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("settled_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("status IN ('reserved', 'consumed', 'released')", name="status"),
        sa.CheckConstraint("period_end > period_start", name="period_order"),
        sa.CheckConstraint("(status = 'reserved') = (settled_at IS NULL)", name="settlement"),
    )
    op.create_index(
        "ix_generation_allowances_account_subscription_period",
        "generation_allowances",
        ["billing_account_id", "subscription_id", "period_start", "period_end", "status"],
    )
    # Add only the existing restricted worker's settlement rights. On a fresh
    # installation, core-access applies the same rights when it creates the role.
    op.execute("""
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'max_billing') THEN
                GRANT SELECT, UPDATE ON public.generation_allowances TO max_billing;
            END IF;
        END $$
    """)
    op.execute(f"""
        INSERT INTO billing_plans
            (id, code, version, name, price_rub, billing_interval,
             included_credit_rub, entitlements, sort_order, is_active)
        VALUES ('{PRO_V3}'::uuid, 'pro', 3, 'Pro', 2990.00, 'month', 0.0000,
                '{{"included_generations": 5,"custom_domains": 0,"max_projects": 3,
                   "static_publish_slots": 3,"always_on_slots": 0,"team_seats": 1,
                   "integrations": true,"preview_idle_minutes": 60}}'::jsonb, 10, false)
    """)
    op.execute("UPDATE billing_plans SET is_active=false WHERE code='pro' AND is_active")
    op.execute(f"UPDATE billing_plans SET is_active=true WHERE id='{PRO_V3}'::uuid")
    # Existing subscriptions/payment snapshots intentionally retain original terms.


def downgrade() -> None:
    op.execute(
        "LOCK TABLE subscriptions, payments, generation_allowances IN SHARE ROW EXCLUSIVE MODE"
    )
    op.execute(f"""
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM generation_allowances)
               OR EXISTS (SELECT 1 FROM subscriptions WHERE plan_id='{PRO_V3}'::uuid)
               OR EXISTS (SELECT 1 FROM payments WHERE package_code='subscription:pro:v3') THEN
                RAISE EXCEPTION 'Pro allowance or purchased terms are referenced; retain migration 0089';
            END IF;
        END $$
    """)
    op.execute(f"DELETE FROM billing_plans WHERE id='{PRO_V3}'::uuid")
    op.execute(f"UPDATE billing_plans SET is_active=true WHERE id='{PRO_V2}'::uuid")
    op.drop_table("generation_allowances")
