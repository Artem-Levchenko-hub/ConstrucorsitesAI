"""Account mail and payment-test schema must preserve accepted commercial state."""

import asyncio
from uuid import uuid4

import asyncpg  # type: ignore[import-untyped]
import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine

from tests.test_billing_catalog_migration import _rows, _seed_subscription
from tests.test_migrations_single_head import _execute, project_cell_migration_database
from yleum_api.models import Base

assert project_cell_migration_database
OLD = "0097_public_prices"
NEW = "0098_account_security_offers"
NEW_TABLES = {
    "email_change_challenges",
    "account_security_emails",
    "billing_purchase_offers",
    "billing_offer_payments",
}


async def _schema_differences(dsn):
    engine = create_async_engine(dsn.replace("postgresql://", "postgresql+asyncpg://", 1))
    included = NEW_TABLES | {"payments", "email_notifications"}
    try:
        async with engine.connect() as connection:
            return await connection.run_sync(
                lambda sync: compare_metadata(
                    MigrationContext.configure(
                        sync,
                        opts={
                            "include_object": lambda obj, name, kind, reflected, compared: (
                                name in included if kind == "table" else obj.table.name in included
                            ),
                            "compare_server_default": True,
                        },
                    ),
                    Base.metadata,
                )
            )
    finally:
        await engine.dispose()


def _seed_normal_contract(db):
    subscription = _seed_subscription(db, 9, "active")
    db.fetchval(
        "UPDATE subscriptions SET plan_id='00000000-0000-4000-8000-000000000011', "
        "auto_renew=true, current_period_start='2026-10-27T00:00:00Z', "
        "current_period_end='2026-11-27T00:00:00Z' "
        f"WHERE id='{subscription}' RETURNING id"
    )
    payment = str(uuid4())
    db.fetchval(f"""
        INSERT INTO payments
          (id,user_id,billing_account_id,subscription_id,purpose,idempotency_key,
           package_code,amount_rub,credit_rub,status,paid_at)
        SELECT '{payment}',user_id,billing_account_id,id,'subscription_initial',
               '{uuid4()}','subscription:pro:v4',2990.00,0,'succeeded',now()
        FROM subscriptions WHERE id='{subscription}' RETURNING id
    """)
    db.fetchval(f"""
        INSERT INTO email_notifications
          (id,event_key,event_type,user_id,billing_account_id,subscription_id,payment_id,
           period_end,plan_name,amount_rub)
        SELECT '{uuid4()}','{uuid4()}','paid',user_id,billing_account_id,id,
               '{payment}',current_period_end,'Pro',2990.00
        FROM subscriptions WHERE id='{subscription}' RETURNING id
    """)
    return subscription


def _insert_test_payment(db, subscription):
    payment = str(uuid4())
    db.fetchval(f"""
        INSERT INTO payments
          (id,user_id,billing_account_id,purpose,idempotency_key,package_code,
           amount_rub,credit_rub,status)
        SELECT '{payment}',user_id,billing_account_id,'payment_test','{uuid4()}',
               'payment_test',1.00,0,'succeeded'
        FROM subscriptions WHERE id='{subscription}' RETURNING id
    """)
    return payment


def test_upgrade_adds_schema_and_empty_rollback_preserves_contracts(
    project_cell_migration_database,
):
    db = project_cell_migration_database
    db.upgrade(OLD)
    _seed_normal_contract(db)
    protected = (
        "users",
        "billing_accounts",
        "billing_plans",
        "subscriptions",
        "payments",
        "email_notifications",
    )
    before = {table: _rows(db, table) for table in protected}
    db.upgrade("head")
    names = {
        row[0] for row in db.fetch("SELECT tablename FROM pg_tables WHERE schemaname='public'")
    }
    assert NEW_TABLES <= names
    assert {table: _rows(db, table) for table in protected} == before
    assert asyncio.run(_schema_differences(db.dsn)) == []
    command.downgrade(db.config, OLD)
    assert {table: _rows(db, table) for table in protected} == before
    assert db.fetchval("SELECT to_regclass('public.account_security_emails')") is None
    db.upgrade(NEW)
    assert asyncio.run(_schema_differences(db.dsn)) == []


def test_payment_test_shape_does_not_weaken_ordinary_notices(project_cell_migration_database):
    db = project_cell_migration_database
    db.upgrade(NEW)
    subscription = _seed_normal_contract(db)
    payment = _insert_test_payment(db, subscription)
    values = f"""
        SELECT '{uuid4()}','{uuid4()}','{{event}}',user_id,billing_account_id,
               '{{payment}}', 'Pro',1.00
        FROM subscriptions WHERE id='{subscription}'
    """
    columns = "id,event_key,event_type,user_id,billing_account_id,payment_id,plan_name,amount_rub"
    db.fetchval(
        f"INSERT INTO email_notifications({columns}) "
        + values.format(event="payment_test", payment=payment)
        + " RETURNING id"
    )
    with pytest.raises(asyncpg.CheckViolationError, match="subscription_shape"):
        db.fetchval(
            f"INSERT INTO email_notifications({columns}) "
            + values.format(event="paid", payment=payment)
            + " RETURNING id"
        )
    with pytest.raises(asyncpg.CheckViolationError, match="subscription_link"):
        db.fetchval(
            f"UPDATE payments SET subscription_id='{subscription}' "
            f"WHERE id='{payment}' RETURNING id"
        )
    with pytest.raises(DBAPIError, match="account_security_offer_evidence_present"):
        command.downgrade(db.config, OLD)
    assert db.fetchval(f"SELECT amount_rub FROM payments WHERE id='{payment}'") == 1
    assert db.fetchval("SELECT version_num FROM alembic_version") == NEW


@pytest.mark.parametrize("kind", ["challenge", "notice"])
def test_downgrade_refuses_account_security_evidence(project_cell_migration_database, kind):
    db = project_cell_migration_database
    db.upgrade(NEW)
    user = str(uuid4())
    asyncio.run(
        _execute(db.dsn, f"INSERT INTO users(id,email) VALUES ('{user}','{user}@example.invalid')")
    )
    if kind == "challenge":
        db.fetchval(f"""
            INSERT INTO email_change_challenges
              (id,user_id,old_email,new_email,session_version,old_token_hash,new_token_hash,expires_at)
            VALUES ('{uuid4()}','{user}','old@example.invalid','new@example.invalid',0,
                    '{"a" * 64}','{"b" * 64}',now()+interval '30 minutes') RETURNING id
        """)
    else:
        db.fetchval(f"""
            INSERT INTO account_security_emails(id,user_id,event_key,kind,recipient_email)
            VALUES ('{uuid4()}','{user}','{uuid4()}','password_changed','old@example.invalid')
            RETURNING id
        """)
    with pytest.raises(DBAPIError, match="account_security_offer_evidence_present"):
        command.downgrade(db.config, OLD)
    table = "email_change_challenges" if kind == "challenge" else "account_security_emails"
    assert db.fetchval(f"SELECT count(*) FROM {table}") == 1
    assert db.fetchval("SELECT version_num FROM alembic_version") == NEW


def test_worker_gets_only_payment_test_settlement_permissions(project_cell_migration_database):
    db = project_cell_migration_database
    db.upgrade(OLD)
    db.fetchval("""DO $$ BEGIN
        IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='max_billing') THEN
            CREATE ROLE max_billing NOLOGIN;
        END IF;
    END $$""")
    db.upgrade(NEW)
    assert db.fetchval(
        "SELECT has_table_privilege('max_billing','billing_purchase_offers','SELECT')"
    )
    for column in ("consumed_payment_id", "consumed_at"):
        assert db.fetchval(
            "SELECT has_column_privilege('max_billing',"
            f"'billing_purchase_offers','{column}','UPDATE')"
        )
    for column in ("user_id", "issued_by_user_id", "reserved_payment_id", "expires_at"):
        assert not db.fetchval(
            "SELECT has_column_privilege('max_billing',"
            f"'billing_purchase_offers','{column}','UPDATE')"
        )
    assert db.fetchval(
        "SELECT has_table_privilege('max_billing','billing_offer_payments','SELECT')"
    )
    assert not db.fetchval(
        "SELECT has_table_privilege('max_billing','billing_purchase_offers','INSERT')"
    )
    assert not db.fetchval(
        "SELECT has_table_privilege('max_billing','account_security_emails','SELECT')"
    )
    assert not db.fetchval(
        "SELECT has_table_privilege('max_billing','email_change_challenges','SELECT')"
    )

    async def exercise_worker_access():
        connection = await asyncpg.connect(db.dsn)
        try:
            async with connection.transaction():
                await connection.execute("SET LOCAL ROLE max_billing")
                assert await connection.fetch(
                    "SELECT * FROM billing_purchase_offers FOR UPDATE"
                ) == []
                assert await connection.execute(
                    "UPDATE billing_purchase_offers "
                    "SET consumed_payment_id=NULL, consumed_at=NULL WHERE false"
                ) == "UPDATE 0"
                with pytest.raises(asyncpg.InsufficientPrivilegeError):
                    async with connection.transaction():
                        await connection.execute(
                            "UPDATE billing_purchase_offers SET expires_at=expires_at WHERE false"
                        )
        finally:
            await connection.close()

    asyncio.run(exercise_worker_access())
