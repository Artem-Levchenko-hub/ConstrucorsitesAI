"""Add durable split generation quotas and immutable balance-free plan terms."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0089_pro_generation_allowance"
down_revision = "0088_generation_recovery_effects"
branch_labels = None
depends_on = None

PRO_V2 = "00000000-0000-4000-8000-000000000006"
PRO_V3 = "00000000-0000-4000-8000-000000000008"
BUSINESS_V2 = "00000000-0000-4000-8000-000000000007"
BUSINESS_V3 = "00000000-0000-4000-8000-000000000009"
FREE_V3 = "00000000-0000-4000-8000-000000000005"
FREE_V4 = "00000000-0000-4000-8000-000000000010"


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("free_creations_carryover", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_check_constraint(
        "free_creations_carryover", "users", "free_creations_carryover IN (0, 1)"
    )
    # Freeze the legacy successful-free counter once. It also included old
    # promotions: this is conservative carried usage, not fabricated run proof.
    op.execute(
        "UPDATE users SET free_creations_carryover=LEAST(GREATEST(free_generations_used,0),1)"
    )
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
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("period_start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("period_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("settled_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("status IN ('reserved', 'consumed', 'released')", name="status"),
        sa.CheckConstraint("kind IN ('creation', 'edit')", name="kind"),
        sa.CheckConstraint(
            "(period_start IS NULL AND period_end IS NULL) OR "
            "(period_start IS NOT NULL AND period_end IS NOT NULL AND period_end > period_start)",
            name="period_order",
        ),
        sa.CheckConstraint("(status = 'reserved') = (settled_at IS NULL)", name="settlement"),
    )
    op.create_index(
        "ix_generation_allowances_account_subscription_period",
        "generation_allowances",
        ["billing_account_id", "subscription_id", "kind", "period_start", "period_end", "status"],
    )
    op.create_index(
        "ix_generation_allowances_account_lifetime_kind",
        "generation_allowances",
        ["billing_account_id", "user_id", "kind", "status"],
        postgresql_where=sa.text("period_start IS NULL AND period_end IS NULL"),
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
                '{{"included_creations": 2,"included_edits": 25,"generation_allowance_period": "paid_month",
                   "custom_domains": 0,"max_projects": 3,
                   "static_publish_slots": 3,"always_on_slots": 0,"team_seats": 1,
                   "integrations": true,"preview_idle_minutes": 60}}'::jsonb, 10, false),
               ('{BUSINESS_V3}'::uuid, 'business', 3, 'Business', 6990.00, 'month', 0.0000,
                '{{"included_creations": 5,"included_edits": 50,"generation_allowance_period": "paid_month",
                   "custom_domains": 0,"max_projects": 10,"static_publish_slots": 10,
                   "always_on_slots": 1,"team_seats": 5,"integrations": true,
                   "preview_idle_minutes": 0}}'::jsonb, 20, false),
               ('{FREE_V4}'::uuid, 'free', 4, 'Free', 0.00, 'month', 0.0000,
                '{{"included_creations": 1,"included_edits": 1,"generation_allowance_period": "lifetime",
                   "custom_domains": 0,"max_projects": 1,"static_publish_slots": 1,
                   "always_on_slots": 0,"team_seats": 1,"integrations": true,
                   "preview_idle_minutes": 15}}'::jsonb, 0, false)
    """)
    op.execute(
        "UPDATE billing_plans SET is_active=false WHERE code IN ('free','pro','business') AND is_active"
    )
    op.execute(
        f"UPDATE billing_plans SET is_active=true WHERE id IN ('{PRO_V3}'::uuid,'{BUSINESS_V3}'::uuid,'{FREE_V4}'::uuid)"
    )
    # Free gains its edit and no longer spends one human-message allowance.
    # Historical ended Free rows, all paid subscriptions and payments stay intact.
    op.execute(f"""
        UPDATE subscriptions s SET plan_id='{FREE_V4}'::uuid
        FROM billing_plans p WHERE p.id=s.plan_id AND p.code='free'
        AND s.status IN ('pending_payment','trialing','active','past_due','paused')
    """)


def downgrade() -> None:
    op.execute(
        "LOCK TABLE subscriptions, payments, generation_allowances, users IN SHARE ROW EXCLUSIVE MODE"
    )
    op.execute(f"""
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM generation_allowances)
               OR EXISTS (SELECT 1 FROM users WHERE free_creations_carryover > 0)
               OR EXISTS (SELECT 1 FROM subscriptions WHERE plan_id IN ('{PRO_V3}'::uuid,'{BUSINESS_V3}'::uuid,'{FREE_V4}'::uuid))
               OR EXISTS (SELECT 1 FROM payments WHERE package_code IN ('subscription:pro:v3','subscription:business:v3')) THEN
                RAISE EXCEPTION 'Generation allowance or plan terms are referenced; retain migration 0089';
            END IF;
        END $$
    """)
    op.execute(
        f"DELETE FROM billing_plans WHERE id IN ('{PRO_V3}'::uuid,'{BUSINESS_V3}'::uuid,'{FREE_V4}'::uuid)"
    )
    op.execute(
        f"UPDATE billing_plans SET is_active=true WHERE id IN ('{PRO_V2}'::uuid,'{BUSINESS_V2}'::uuid,'{FREE_V3}'::uuid)"
    )
    op.drop_table("generation_allowances")
    op.drop_constraint(op.f("ck_users_free_creations_carryover"), "users", type_="check")
    op.drop_column("users", "free_creations_carryover")
