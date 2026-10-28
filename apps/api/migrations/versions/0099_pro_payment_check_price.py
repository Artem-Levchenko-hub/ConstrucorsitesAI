"""Publish one-ruble Pro for payment checks without repricing contracts."""

from alembic import op

revision = "0099_pro_payment_check_price"
down_revision = "0098_account_security_offers"
branch_labels = None
depends_on = None

PRO_V4 = "00000000-0000-4000-8000-000000000011"
PRO_V5 = "00000000-0000-4000-8000-000000000012"


def upgrade() -> None:
    # Frozen catalog terms: do not import runtime seeds or update accepted rows.
    op.execute(f"""
        INSERT INTO billing_plans
            (id, code, version, name, price_rub, billing_interval, included_credit_rub,
             entitlements, sort_order, is_active)
        VALUES
            ('{PRO_V5}'::uuid, 'pro', 5, 'Pro', 1.00, 'month', 0.0000,
             '{{"custom_domains": 0, "initial_generations_monthly": 2,
                "edits_monthly": 25, "max_projects": 3, "static_publish_slots": 3,
                "always_on_slots": 1, "team_seats": 1, "integrations": true,
                "preview_idle_minutes": 60}}'::jsonb, 10, false)
    """)
    op.execute("UPDATE billing_plans SET is_active = false WHERE code = 'pro' AND is_active")
    op.execute(f"UPDATE billing_plans SET is_active = true WHERE id = '{PRO_V5}'::uuid")


def downgrade() -> None:
    op.execute("LOCK TABLE subscriptions, payments IN SHARE ROW EXCLUSIVE MODE")
    op.execute(f"""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM subscriptions WHERE plan_id = '{PRO_V5}'::uuid)
               OR EXISTS (
                   SELECT 1 FROM payments WHERE package_code = 'subscription:pro:v5'
               ) THEN
                RAISE EXCEPTION
                    'cannot downgrade Pro payment-check price: Pro v5 has contract or payment references';
            END IF;
        END $$
    """)
    op.execute(f"DELETE FROM billing_plans WHERE id = '{PRO_V5}'::uuid")
    op.execute(f"UPDATE billing_plans SET is_active = true WHERE id = '{PRO_V4}'::uuid")
