"""Make business-aware billing accounts the owner of money and subscriptions.

Revision ID: 0036_business_billing_accounts
Revises: 0035_billing_foundation
Create Date: 2026-07-31
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0036_business_billing_accounts"
down_revision = "0035_billing_foundation"
branch_labels = None
depends_on = None


def _add_account_reference(table_name: str) -> None:
    op.add_column(
        table_name,
        sa.Column("billing_account_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.execute(
        f"""
        UPDATE {table_name} target
           SET billing_account_id = account.id
          FROM billing_accounts account
         WHERE target.user_id = COALESCE(
             account.personal_user_id,
             account.created_by_user_id
         )
        """
    )
    op.alter_column(
        table_name,
        "billing_account_id",
        existing_type=postgresql.UUID(as_uuid=True),
        nullable=False,
    )
    op.create_foreign_key(
        f"fk_{table_name}_billing_account_id_billing_accounts",
        table_name,
        "billing_accounts",
        ["billing_account_id"],
        ["id"],
        ondelete="RESTRICT",
    )


def upgrade() -> None:
    op.create_table(
        "billing_accounts",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("scope", sa.Text(), nullable=False, server_default="personal"),
        sa.Column("personal_user_id", postgresql.UUID(as_uuid=True)),
        sa.Column("business_id", postgresql.UUID(as_uuid=True)),
        sa.Column("created_by_user_id", postgresql.UUID(as_uuid=True)),
        sa.Column("currency", sa.Text(), nullable=False, server_default="RUB"),
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
            "scope IN ('personal', 'business')",
            name="ck_billing_accounts_scope",
        ),
        sa.CheckConstraint(
            "(scope = 'personal' AND personal_user_id IS NOT NULL AND business_id IS NULL) "
            "OR (scope = 'business' AND personal_user_id IS NULL AND business_id IS NOT NULL)",
            name="ck_billing_accounts_owner",
        ),
        sa.CheckConstraint("currency = 'RUB'", name="ck_billing_accounts_currency"),
        sa.ForeignKeyConstraint(
            ["personal_user_id"],
            ["users.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["business_id"],
            ["business_profiles.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_billing_accounts")),
    )
    op.create_index(
        "uq_billing_accounts_personal_user",
        "billing_accounts",
        ["personal_user_id"],
        unique=True,
        postgresql_where=sa.text("personal_user_id IS NOT NULL"),
    )
    op.create_index(
        "uq_billing_accounts_business",
        "billing_accounts",
        ["business_id"],
        unique=True,
        postgresql_where=sa.text("business_id IS NOT NULL"),
    )
    op.execute(
        """
        INSERT INTO billing_accounts
            (id, scope, personal_user_id, created_by_user_id, currency)
        SELECT uuid_generate_v4(), 'personal', id, id, 'RUB'
          FROM users
        """
    )
    op.execute(
        """
        WITH chosen_owner AS (
            SELECT business_id, user_id
              FROM (
                  SELECT
                      bm.business_id,
                      bm.user_id,
                      row_number() OVER (
                          PARTITION BY bm.business_id
                          ORDER BY
                              CASE WHEN bm.role = 'owner' THEN 0 ELSE 1 END,
                              bm.created_at,
                              bm.user_id
                      ) AS position
                    FROM business_members bm
              ) ranked
             WHERE position = 1
        )
        UPDATE billing_accounts account
           SET scope = 'business',
               business_id = owner.business_id,
               personal_user_id = NULL,
               updated_at = now()
          FROM chosen_owner owner
         WHERE account.personal_user_id = owner.user_id
        """
    )
    op.execute(
        """
        CREATE TRIGGER billing_accounts_set_updated_at
        BEFORE UPDATE ON billing_accounts
        FOR EACH ROW EXECUTE FUNCTION set_updated_at()
        """
    )

    for table_name in (
        "wallets",
        "billing_payment_methods",
        "subscriptions",
        "payments",
        "wallet_charges",
    ):
        _add_account_reference(table_name)

    op.create_unique_constraint(
        "uq_wallets_billing_account_id",
        "wallets",
        ["billing_account_id"],
    )
    op.create_index(
        "ix_billing_payment_methods_account_status",
        "billing_payment_methods",
        ["billing_account_id", "status"],
    )
    op.create_index(
        "ix_payments_account_created",
        "payments",
        ["billing_account_id", "created_at"],
    )
    op.create_index(
        "ix_wallet_charges_account_created_at",
        "wallet_charges",
        ["billing_account_id", "created_at"],
    )
    op.drop_index("uq_subscriptions_user_live", table_name="subscriptions")
    op.create_index(
        "uq_subscriptions_account_live",
        "subscriptions",
        ["billing_account_id"],
        unique=True,
        postgresql_where=sa.text(
            "status IN ('trialing', 'active', 'past_due', 'paused')"
        ),
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION fill_wallet_charge_balance_after() RETURNS TRIGGER AS $$
        BEGIN
            IF NEW.balance_after_rub IS NULL THEN
                SELECT balance_rub
                INTO NEW.balance_after_rub
                FROM wallets
                WHERE billing_account_id = NEW.billing_account_id;
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )


def downgrade() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION fill_wallet_charge_balance_after() RETURNS TRIGGER AS $$
        BEGIN
            IF NEW.balance_after_rub IS NULL THEN
                SELECT balance_rub
                INTO NEW.balance_after_rub
                FROM wallets
                WHERE user_id = NEW.user_id;
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.drop_index("uq_subscriptions_account_live", table_name="subscriptions")
    op.create_index(
        "uq_subscriptions_user_live",
        "subscriptions",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text(
            "status IN ('trialing', 'active', 'past_due', 'paused')"
        ),
    )
    op.drop_index(
        "ix_wallet_charges_account_created_at",
        table_name="wallet_charges",
    )
    op.drop_index("ix_payments_account_created", table_name="payments")
    op.drop_index(
        "ix_billing_payment_methods_account_status",
        table_name="billing_payment_methods",
    )
    op.drop_constraint(
        "uq_wallets_billing_account_id",
        "wallets",
        type_="unique",
    )

    for table_name in (
        "wallet_charges",
        "payments",
        "subscriptions",
        "billing_payment_methods",
        "wallets",
    ):
        op.drop_constraint(
            f"fk_{table_name}_billing_account_id_billing_accounts",
            table_name,
            type_="foreignkey",
        )
        op.drop_column(table_name, "billing_account_id")

    op.execute("DROP TRIGGER billing_accounts_set_updated_at ON billing_accounts")
    op.drop_index("uq_billing_accounts_business", table_name="billing_accounts")
    op.drop_index("uq_billing_accounts_personal_user", table_name="billing_accounts")
    op.drop_table("billing_accounts")
