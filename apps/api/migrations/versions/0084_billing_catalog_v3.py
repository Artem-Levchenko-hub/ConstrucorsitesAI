"""Publish new immutable plan terms, preserving existing paid contracts."""

from alembic import op

revision = "0084_billing_catalog_v3"
down_revision = "0083_email_verification_codes"
branch_labels = None
depends_on = None

FREE_V1 = "00000000-0000-4000-8000-000000000001"
PRO_V1 = "00000000-0000-4000-8000-000000000002"
BUSINESS_V1 = "00000000-0000-4000-8000-000000000003"
FREE_V2 = "00000000-0000-4000-8000-000000000004"
FREE_V3 = "00000000-0000-4000-8000-000000000005"
PRO_V2 = "00000000-0000-4000-8000-000000000006"
BUSINESS_V2 = "00000000-0000-4000-8000-000000000007"
NEW_IDS = f"('{FREE_V3}'::uuid, '{PRO_V2}'::uuid, '{BUSINESS_V2}'::uuid)"


def upgrade() -> None:
    # Insert inactive first: the partial unique index allows only one active
    # version per code. Never import mutable application seeds into migrations.
    op.execute(f"""
        INSERT INTO billing_plans
            (id, code, version, name, price_rub, billing_interval, included_credit_rub,
             entitlements, sort_order, is_active)
        VALUES
            ('{FREE_V3}'::uuid, 'free', 3, 'Free', 0.00, 'month', 0.0000,
             '{{"max_projects": 1, "static_publish_slots": 1, "always_on_slots": 0,
               "team_seats": 1, "integrations": true, "preview_idle_minutes": 15,
               "generation_messages_lifetime": 1, "custom_domains": 0}}'::jsonb, 0, false),
            ('{PRO_V2}'::uuid, 'pro', 2, 'Pro', 2990.00, 'month', 500.0000,
             '{{"max_projects": 3, "static_publish_slots": 3, "always_on_slots": 0,
               "team_seats": 1, "integrations": true, "preview_idle_minutes": 60,
               "custom_domains": 0}}'::jsonb, 10, false),
            ('{BUSINESS_V2}'::uuid, 'business', 2, 'Business', 6990.00, 'month', 1500.0000,
             '{{"max_projects": 10, "static_publish_slots": 10, "always_on_slots": 1,
               "team_seats": 5, "integrations": true, "preview_idle_minutes": 0,
               "custom_domains": 0}}'::jsonb, 20, false)
    """)
    op.execute("""
        UPDATE billing_plans SET is_active = false
        WHERE is_active AND code IN ('free', 'pro', 'business')
    """)
    op.execute(f"UPDATE billing_plans SET is_active = true WHERE id IN {NEW_IDS}")
    op.execute(f"""
        UPDATE subscriptions SET plan_id = '{FREE_V3}'::uuid, updated_at = now()
        WHERE plan_id IN ('{FREE_V1}'::uuid, '{FREE_V2}'::uuid)
          AND status IN ('pending_payment', 'trialing', 'active', 'past_due', 'paused')
    """)


def downgrade() -> None:
    # A downgrade must never silently rewrite an accepted contract or remove
    # the terms of a payment snapshot. Block writes during this check/delete.
    # Free references also block rollback: their original v1/v2 is not recorded.
    op.execute("LOCK TABLE subscriptions, payments IN SHARE ROW EXCLUSIVE MODE")
    op.execute(f"""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM subscriptions WHERE plan_id IN {NEW_IDS})
               OR EXISTS (
                   SELECT 1 FROM payments
                   WHERE package_code IN (
                       'subscription:free:v3', 'subscription:pro:v2', 'subscription:business:v2'
                   )
               ) THEN
                RAISE EXCEPTION
                    'cannot downgrade billing catalog: new plan terms are referenced by subscriptions or payment snapshots; retain migration 0084';
            END IF;
        END $$
    """)
    op.execute(f"UPDATE billing_plans SET is_active = false WHERE id IN {NEW_IDS}")
    op.execute(f"DELETE FROM billing_plans WHERE id IN {NEW_IDS}")
    op.execute(f"""
        UPDATE billing_plans SET is_active = true
        WHERE id IN ('{FREE_V2}'::uuid, '{PRO_V1}'::uuid, '{BUSINESS_V1}'::uuid)
    """)
