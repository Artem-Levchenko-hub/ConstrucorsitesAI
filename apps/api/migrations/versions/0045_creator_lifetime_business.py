"""Grant the creator a durable lifetime Business subscription.

Revision ID: 0045_creator_lifetime_business
Revises: 0044_creator_account_privileges
"""

import sqlalchemy as sa
from alembic import op

revision = "0045_creator_lifetime_business"
down_revision = "0044_creator_account_privileges"
branch_labels = None
depends_on = None

CREATOR_EMAIL = "undj00x03@gmail.com"


def upgrade() -> None:
    op.add_column(
        "subscriptions",
        sa.Column(
            "is_lifetime",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.create_check_constraint(
        "ck_subscriptions_lifetime_shape",
        "subscriptions",
        "NOT is_lifetime OR ("
        "status = 'active' AND auto_renew = false "
        "AND cancel_at_period_end = false "
        "AND payment_method_id IS NULL "
        "AND current_period_end IS NULL "
        "AND next_charge_at IS NULL "
        "AND grace_period_ends_at IS NULL)",
    )

    # A disposable fresh install has no creator account or subscription yet, so
    # it only needs the schema. On every nonempty database, keep the complete
    # grant in the migration transaction and retain the original fail-closed
    # production invariants below.
    connection = op.get_bind()
    existing_user_count = connection.scalar(sa.text("SELECT count(*) FROM users"))
    if existing_user_count == 0:
        return
    op.execute(
        """
            DO $$
            DECLARE
                creator_user_id uuid;
                creator_account_id uuid;
                current_subscription_id uuid;
                business_plan_id uuid;
                previous_plan_code text;
                matched_count integer;
            BEGIN
                SELECT count(*) INTO matched_count
                FROM users
                WHERE email = 'undj00x03@gmail.com' AND is_anon = false;
                IF matched_count <> 1 THEN
                    RAISE EXCEPTION
                        'creator lifetime grant expected exactly one existing account for %, found %',
                        'undj00x03@gmail.com', matched_count;
                END IF;

                SELECT id INTO creator_user_id
                FROM users
                WHERE email = 'undj00x03@gmail.com' AND is_anon = false
                FOR UPDATE;

                WITH candidate_accounts AS (
                    SELECT id
                    FROM billing_accounts
                    WHERE personal_user_id = creator_user_id
                    UNION
                    SELECT billing_accounts.id
                    FROM business_members
                    JOIN billing_accounts
                      ON billing_accounts.business_id = business_members.business_id
                    WHERE business_members.user_id = creator_user_id
                )
                SELECT count(*) INTO matched_count FROM candidate_accounts;
                IF matched_count <> 1 THEN
                    RAISE EXCEPTION
                        'creator lifetime grant expected exactly one billing account, found %',
                        matched_count;
                END IF;

                WITH candidate_accounts AS (
                    SELECT id
                    FROM billing_accounts
                    WHERE personal_user_id = creator_user_id
                    UNION
                    SELECT billing_accounts.id
                    FROM business_members
                    JOIN billing_accounts
                      ON billing_accounts.business_id = business_members.business_id
                    WHERE business_members.user_id = creator_user_id
                )
                SELECT id INTO creator_account_id FROM candidate_accounts;
                PERFORM 1 FROM billing_accounts
                WHERE id = creator_account_id FOR UPDATE;

                SELECT count(*) INTO matched_count
                FROM subscriptions
                WHERE billing_account_id = creator_account_id
                  AND status IN ('trialing', 'active', 'past_due', 'paused');
                IF matched_count <> 1 THEN
                    RAISE EXCEPTION
                        'creator lifetime grant expected exactly one live subscription, found %',
                        matched_count;
                END IF;

                SELECT subscriptions.id, billing_plans.code
                INTO current_subscription_id, previous_plan_code
                FROM subscriptions
                JOIN billing_plans ON billing_plans.id = subscriptions.plan_id
                WHERE subscriptions.billing_account_id = creator_account_id
                  AND subscriptions.status IN ('trialing', 'active', 'past_due', 'paused')
                FOR UPDATE OF subscriptions;

                SELECT count(*) INTO matched_count
                FROM subscriptions
                WHERE billing_account_id = creator_account_id
                  AND status = 'pending_payment';
                IF matched_count <> 0 THEN
                    RAISE EXCEPTION
                        'creator lifetime grant refuses an in-flight subscription checkout';
                END IF;

                SELECT count(*) INTO matched_count
                FROM billing_plans
                WHERE code = 'business' AND is_active = true;
                IF matched_count <> 1 THEN
                    RAISE EXCEPTION
                        'creator lifetime grant expected exactly one active Business plan, found %',
                        matched_count;
                END IF;

                SELECT id INTO business_plan_id
                FROM billing_plans
                WHERE code = 'business' AND is_active = true;

                UPDATE subscriptions
                SET status = 'expired',
                    auto_renew = false,
                    cancel_at_period_end = false,
                    next_charge_at = NULL,
                    grace_period_ends_at = NULL,
                    ended_at = now()
                WHERE id = current_subscription_id;

                INSERT INTO subscriptions (
                    id,
                    billing_account_id,
                    user_id,
                    plan_id,
                    status,
                    is_lifetime,
                    auto_renew,
                    cancel_at_period_end,
                    current_period_start
                ) VALUES (
                    uuid_generate_v4(),
                    creator_account_id,
                    creator_user_id,
                    business_plan_id,
                    'active',
                    true,
                    false,
                    false,
                    now()
                );

                INSERT INTO admin_audit_events (
                    id, actor_user_id, target_user_id, action, details
                ) VALUES (
                    uuid_generate_v4(),
                    creator_user_id,
                    creator_user_id,
                    'creator.subscription.lifetime_business.bootstrap',
                    jsonb_build_object(
                        'source', 'migration:0045_creator_lifetime_business',
                        'billing_account_id', creator_account_id,
                        'before', jsonb_build_object(
                            'subscription_id', current_subscription_id,
                            'plan_code', previous_plan_code
                        ),
                        'after', jsonb_build_object(
                            'plan_code', 'business',
                            'is_lifetime', true,
                            'auto_renew', false,
                            'current_period_end', NULL
                        )
                    )
                );
            END $$;
        """
    )


def downgrade() -> None:
    # Keep Business live during an application rollback. Older code already
    # treats a subscription with no period end as non-renewing and will not
    # downgrade it; only the explicit marker and its stronger API guards vanish.
    op.execute(
        """
            UPDATE subscriptions
            SET is_lifetime = false
            WHERE is_lifetime = true
              AND user_id = (
                  SELECT id FROM users
                  WHERE email = 'undj00x03@gmail.com' AND is_anon = false
              )
        """
    )
    op.drop_constraint(
        "ck_subscriptions_lifetime_shape",
        "subscriptions",
        type_="check",
    )
    op.drop_column("subscriptions", "is_lifetime")
