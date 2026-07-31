"""Unify wallet ledger and add versioned subscription foundations.

Revision ID: 0035_billing_foundation
Revises: 0034_admin_accounts
Create Date: 2026-07-31
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0035_billing_foundation"
down_revision = "0034_admin_accounts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "billing_plans",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("code", sa.Text(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("price_rub", sa.Numeric(12, 2), nullable=False),
        sa.Column("billing_interval", sa.Text(), nullable=False),
        sa.Column("included_credit_rub", sa.Numeric(12, 4), nullable=False),
        sa.Column(
            "entitlements",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="{}",
        ),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint("price_rub >= 0", name="ck_billing_plans_price_non_negative"),
        sa.CheckConstraint(
            "included_credit_rub >= 0",
            name="ck_billing_plans_credit_non_negative",
        ),
        sa.CheckConstraint(
            "billing_interval IN ('month')",
            name="ck_billing_plans_interval",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_billing_plans")),
        sa.UniqueConstraint("code", "version", name="uq_billing_plans_code_version"),
    )
    op.create_index(
        "uq_billing_plans_active_code",
        "billing_plans",
        ["code"],
        unique=True,
        postgresql_where=sa.text("is_active"),
    )

    billing_plans = sa.table(
        "billing_plans",
        sa.column("id", postgresql.UUID(as_uuid=True)),
        sa.column("code", sa.Text()),
        sa.column("version", sa.Integer()),
        sa.column("name", sa.Text()),
        sa.column("price_rub", sa.Numeric(12, 2)),
        sa.column("billing_interval", sa.Text()),
        sa.column("included_credit_rub", sa.Numeric(12, 4)),
        sa.column("entitlements", postgresql.JSONB()),
        sa.column("sort_order", sa.Integer()),
        sa.column("is_active", sa.Boolean()),
    )
    op.bulk_insert(
        billing_plans,
        [
            {
                "id": uuid.UUID("00000000-0000-4000-8000-000000000001"),
                "code": "free",
                "version": 1,
                "name": "Free",
                "price_rub": Decimal("0.00"),
                "billing_interval": "month",
                "included_credit_rub": Decimal("0.0000"),
                "entitlements": {
                    "max_projects": 1,
                    "static_publish_slots": 0,
                    "always_on_slots": 0,
                    "team_seats": 1,
                    "custom_domains": 0,
                    "integrations": False,
                    "preview_idle_minutes": 15,
                },
                "sort_order": 0,
                "is_active": True,
            },
            {
                "id": uuid.UUID("00000000-0000-4000-8000-000000000002"),
                "code": "pro",
                "version": 1,
                "name": "Pro",
                "price_rub": Decimal("1490.00"),
                "billing_interval": "month",
                "included_credit_rub": Decimal("500.0000"),
                "entitlements": {
                    "max_projects": 3,
                    "static_publish_slots": 3,
                    "always_on_slots": 0,
                    "team_seats": 1,
                    "custom_domains": 1,
                    "integrations": True,
                    "preview_idle_minutes": 60,
                },
                "sort_order": 10,
                "is_active": True,
            },
            {
                "id": uuid.UUID("00000000-0000-4000-8000-000000000003"),
                "code": "business",
                "version": 1,
                "name": "Business",
                "price_rub": Decimal("4990.00"),
                "billing_interval": "month",
                "included_credit_rub": Decimal("1500.0000"),
                "entitlements": {
                    "max_projects": 10,
                    "static_publish_slots": 10,
                    "always_on_slots": 1,
                    "team_seats": 5,
                    "custom_domains": 10,
                    "integrations": True,
                    "preview_idle_minutes": 0,
                },
                "sort_order": 20,
                "is_active": True,
            },
        ],
    )
    op.execute(
        """
        CREATE FUNCTION protect_billing_plan_terms() RETURNS TRIGGER AS $$
        BEGIN
            IF (to_jsonb(NEW) - 'is_active')
                IS DISTINCT FROM (to_jsonb(OLD) - 'is_active') THEN
                RAISE EXCEPTION
                    'billing plan terms are immutable; create a new version'
                    USING ERRCODE = 'check_violation';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER billing_plans_protect_terms
        BEFORE UPDATE ON billing_plans
        FOR EACH ROW EXECUTE FUNCTION protect_billing_plan_terms()
        """
    )

    op.create_table(
        "billing_payment_methods",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("provider", sa.Text(), nullable=False, server_default="yookassa"),
        sa.Column("provider_payment_method_id", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="active"),
        sa.Column("title", sa.Text()),
        sa.Column("last4", sa.Text()),
        sa.Column("consent_version", sa.Text(), nullable=False),
        sa.Column("consented_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "status IN ('active', 'revoked', 'expired')",
            name="ck_billing_payment_methods_status",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_billing_payment_methods")),
        sa.UniqueConstraint(
            "provider",
            "provider_payment_method_id",
            name="uq_billing_payment_methods_provider_id",
        ),
    )
    op.create_index(
        "ix_billing_payment_methods_user_status",
        "billing_payment_methods",
        ["user_id", "status"],
    )

    op.create_table(
        "subscriptions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("plan_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("payment_method_id", postgresql.UUID(as_uuid=True)),
        sa.Column("status", sa.Text(), nullable=False, server_default="active"),
        sa.Column("auto_renew", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column(
            "cancel_at_period_end",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column("current_period_start", sa.DateTime(timezone=True)),
        sa.Column("current_period_end", sa.DateTime(timezone=True)),
        sa.Column("next_charge_at", sa.DateTime(timezone=True)),
        sa.Column("grace_period_ends_at", sa.DateTime(timezone=True)),
        sa.Column("canceled_at", sa.DateTime(timezone=True)),
        sa.Column("ended_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "status IN ('trialing', 'active', 'past_due', 'paused', 'canceled', 'expired')",
            name="ck_subscriptions_status",
        ),
        sa.CheckConstraint(
            "current_period_end IS NULL OR current_period_start IS NULL "
            "OR current_period_end > current_period_start",
            name="ck_subscriptions_period_order",
        ),
        sa.ForeignKeyConstraint(
            ["payment_method_id"],
            ["billing_payment_methods.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(["plan_id"], ["billing_plans.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_subscriptions")),
    )
    op.create_index(
        "uq_subscriptions_user_live",
        "subscriptions",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text(
            "status IN ('trialing', 'active', 'past_due', 'paused')"
        ),
    )
    op.create_index(
        "ix_subscriptions_plan_status",
        "subscriptions",
        ["plan_id", "status"],
    )
    op.create_index(
        "ix_subscriptions_next_charge",
        "subscriptions",
        ["next_charge_at", "status"],
    )
    op.execute(
        """
        INSERT INTO subscriptions (id, user_id, plan_id, status)
        SELECT uuid_generate_v4(), id,
               '00000000-0000-4000-8000-000000000001'::uuid, 'active'
        FROM users
        WHERE is_anon = false
        """
    )
    op.execute(
        """
        CREATE TRIGGER subscriptions_set_updated_at
        BEFORE UPDATE ON subscriptions
        FOR EACH ROW EXECUTE FUNCTION set_updated_at()
        """
    )
    op.execute(
        """
        CREATE TRIGGER billing_payment_methods_set_updated_at
        BEFORE UPDATE ON billing_payment_methods
        FOR EACH ROW EXECUTE FUNCTION set_updated_at()
        """
    )

    op.add_column(
        "payments",
        sa.Column(
            "purpose",
            sa.Text(),
            nullable=False,
            server_default="wallet_topup",
        ),
    )
    op.add_column(
        "payments",
        sa.Column("subscription_id", postgresql.UUID(as_uuid=True)),
    )
    op.create_check_constraint(
        "ck_payments_purpose",
        "payments",
        "purpose IN ('wallet_topup', 'subscription_initial', 'subscription_renewal')",
    )
    op.create_check_constraint(
        "ck_payments_subscription_link",
        "payments",
        "(purpose = 'wallet_topup' AND subscription_id IS NULL) OR "
        "(purpose IN ('subscription_initial', 'subscription_renewal') "
        "AND subscription_id IS NOT NULL)",
    )
    op.create_foreign_key(
        "fk_payments_subscription_id_subscriptions",
        "payments",
        "subscriptions",
        ["subscription_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_payments_subscription_created",
        "payments",
        ["subscription_id", "created_at"],
    )

    op.add_column(
        "wallet_charges",
        sa.Column(
            "entry_type",
            sa.Text(),
            nullable=False,
            server_default="usage",
        ),
    )
    op.add_column(
        "wallet_charges",
        sa.Column("balance_after_rub", sa.Numeric(12, 4)),
    )
    op.add_column("wallet_charges", sa.Column("external_ref", sa.Text()))
    op.add_column(
        "wallet_charges",
        sa.Column("subscription_id", postgresql.UUID(as_uuid=True)),
    )
    op.create_check_constraint(
        "ck_wallet_charges_entry_type",
        "wallet_charges",
        "entry_type IN "
        "('usage', 'topup', 'payment', 'refund', 'subscription_credit', 'adjustment')",
    )
    op.create_foreign_key(
        "fk_wallet_charges_subscription_id_subscriptions",
        "wallet_charges",
        "subscriptions",
        ["subscription_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.execute(
        """
        UPDATE wallet_charges
        SET entry_type = CASE
            WHEN amount_rub < 0 THEN 'usage'
            WHEN description ILIKE '%top-up%' THEN 'topup'
            ELSE 'adjustment'
        END
        """
    )
    op.execute(
        """
        INSERT INTO wallet_charges
            (id, user_id, message_id, subscription_id, entry_type, amount_rub,
             balance_after_rub, external_ref, description, created_at)
        SELECT
            CASE
                WHEN EXISTS (SELECT 1 FROM wallet_charges c WHERE c.id = l.id)
                    THEN uuid_generate_v4()
                ELSE l.id
            END,
            l.user_id, NULL, NULL, l.entry_type, l.amount_rub,
            l.balance_after_rub, l.external_ref, l.description, l.created_at
        FROM wallet_ledger_entries l
        WHERE l.external_ref IS NULL
           OR NOT EXISTS (
               SELECT 1
               FROM wallet_charges c
               WHERE c.external_ref = l.external_ref
           )
        """
    )
    op.execute(
        """
        WITH ledger_balances AS (
            SELECT
                c.id,
                w.balance_rub
                - COALESCE(
                    SUM(c.amount_rub) OVER (
                        PARTITION BY c.user_id
                        ORDER BY c.created_at DESC, c.id DESC
                        ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING
                    ),
                    0
                ) AS balance_after_rub
            FROM wallet_charges c
            JOIN wallets w ON w.user_id = c.user_id
        )
        UPDATE wallet_charges c
        SET balance_after_rub = b.balance_after_rub
        FROM ledger_balances b
        WHERE b.id = c.id
        """
    )
    op.alter_column(
        "wallet_charges",
        "balance_after_rub",
        existing_type=sa.Numeric(12, 4),
        nullable=False,
    )
    op.create_unique_constraint(
        "uq_wallet_charges_external_ref",
        "wallet_charges",
        ["external_ref"],
    )
    op.create_index(
        "ix_wallet_charges_subscription_id",
        "wallet_charges",
        ["subscription_id"],
    )
    op.drop_table("wallet_ledger_entries")


def downgrade() -> None:
    op.create_table(
        "wallet_ledger_entries",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("entry_type", sa.Text(), nullable=False),
        sa.Column("amount_rub", sa.Numeric(12, 4), nullable=False),
        sa.Column("balance_after_rub", sa.Numeric(12, 4), nullable=False),
        sa.Column("external_ref", sa.Text()),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "entry_type IN ('payment', 'refund', 'usage', 'adjustment')",
            name="ck_wallet_ledger_entry_type",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_wallet_ledger_entries")),
        sa.UniqueConstraint("external_ref", name=op.f("uq_wallet_ledger_entries_external_ref")),
    )
    op.create_index(
        "ix_wallet_ledger_user_created",
        "wallet_ledger_entries",
        ["user_id", "created_at"],
    )
    op.execute(
        """
        INSERT INTO wallet_ledger_entries
            (id, user_id, entry_type, amount_rub, balance_after_rub,
             external_ref, description, created_at)
        SELECT id, user_id,
               entry_type,
               amount_rub, COALESCE(balance_after_rub, 0),
               external_ref, description, created_at
        FROM wallet_charges
        WHERE entry_type IN ('payment', 'refund')
        """
    )
    op.execute(
        """
        DELETE FROM wallet_charges
        WHERE entry_type IN ('payment', 'refund')
        """
    )

    op.drop_index("ix_wallet_charges_subscription_id", table_name="wallet_charges")
    op.drop_constraint("uq_wallet_charges_external_ref", "wallet_charges", type_="unique")
    op.drop_constraint(
        "fk_wallet_charges_subscription_id_subscriptions",
        "wallet_charges",
        type_="foreignkey",
    )
    op.drop_constraint("ck_wallet_charges_entry_type", "wallet_charges", type_="check")
    op.drop_column("wallet_charges", "subscription_id")
    op.drop_column("wallet_charges", "external_ref")
    op.drop_column("wallet_charges", "balance_after_rub")
    op.drop_column("wallet_charges", "entry_type")

    op.drop_index("ix_payments_subscription_created", table_name="payments")
    op.drop_constraint(
        "fk_payments_subscription_id_subscriptions",
        "payments",
        type_="foreignkey",
    )
    op.drop_constraint(
        "ck_payments_subscription_link",
        "payments",
        type_="check",
    )
    op.drop_constraint("ck_payments_purpose", "payments", type_="check")
    op.drop_column("payments", "subscription_id")
    op.drop_column("payments", "purpose")

    op.execute("DROP TRIGGER subscriptions_set_updated_at ON subscriptions")
    op.execute(
        "DROP TRIGGER billing_payment_methods_set_updated_at ON billing_payment_methods"
    )
    op.execute("DROP TRIGGER billing_plans_protect_terms ON billing_plans")
    op.drop_table("subscriptions")
    op.drop_table("billing_payment_methods")
    op.drop_table("billing_plans")
    op.execute("DROP FUNCTION protect_billing_plan_terms()")
