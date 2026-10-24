"""The public catalog cutover preserves existing monetary contracts."""

from __future__ import annotations

import asyncio
import importlib.util
import json
from decimal import Decimal
from types import ModuleType
from unittest.mock import Mock

import pytest
from alembic import command
from sqlalchemy.exc import DBAPIError

from tests.test_migrations_single_head import (
    _VERSIONS,
    ProjectCellMigrationDatabase,
    _execute,
    project_cell_migration_database,
)

# Expose the shared isolated real-PostgreSQL fixture to this test module.
assert project_cell_migration_database

PREVIOUS = "0083_email_verification_codes"
CURRENT = "0084_billing_catalog_v3"
PREFIX = "00000000-0000-4000-8000-00000000000"


def _migration() -> ModuleType:
    spec = importlib.util.spec_from_file_location(CURRENT, _VERSIONS / f"{CURRENT}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_catalog_cutover_inserts_inactive_before_switching_active_rows() -> None:
    migration = _migration()
    migration.op = Mock()
    migration.upgrade()
    statements = [str(call.args[0]) for call in migration.op.execute.call_args_list]
    assert "INSERT INTO billing_plans" in statements[0]
    assert statements[0].count("false)") == 3
    assert "SET is_active = false" in statements[1]
    assert "code IN ('free', 'pro', 'business')" in statements[1]
    assert "SET is_active = true" in statements[2]
    assert "UPDATE subscriptions" in statements[3]
    assert "status IN" in statements[3]


def _seed_subscription(database: ProjectCellMigrationDatabase, plan: int, status: str) -> str:
    subscription_id = str(
        database.fetchval(f"""
        WITH new_user AS (
            INSERT INTO users (id, email, password_hash)
            VALUES (uuid_generate_v4(), uuid_generate_v4()::text || '@example.invalid', 'synthetic')
            RETURNING id
        ), account AS (
            INSERT INTO billing_accounts (id, scope, personal_user_id)
            SELECT uuid_generate_v4(), 'personal', id FROM new_user
            RETURNING id, personal_user_id
        )
        INSERT INTO subscriptions (id, user_id, billing_account_id, plan_id, status)
        SELECT uuid_generate_v4(), personal_user_id, id, '{PREFIX}{plan}', '{status}'
        FROM account RETURNING id
    """)
    )
    return subscription_id


def _seed_payment(
    database: ProjectCellMigrationDatabase, subscription_id: str, package: str
) -> None:
    asyncio.run(
        _execute(
            database.dsn,
            f"""
        INSERT INTO payments
            (id, user_id, billing_account_id, subscription_id, purpose,
             idempotency_key, package_code, amount_rub, credit_rub, status, provider_payload)
        SELECT uuid_generate_v4(), user_id, billing_account_id, id, 'subscription_initial',
               uuid_generate_v4()::text, '{package}', 1490, 500, 'succeeded',
               '{{"id": "synthetic-provider-payment", "amount": {{"value": "1490.00"}}}}'
        FROM subscriptions WHERE id = '{subscription_id}'
    """,
        )
    )


def _rows(database: ProjectCellMigrationDatabase, table: str) -> list[tuple[object, ...]]:
    return [tuple(row) for row in database.fetch(f"SELECT * FROM {table} ORDER BY id")]


def test_catalog_upgrade_preserves_history_and_paid_contracts(
    project_cell_migration_database: ProjectCellMigrationDatabase,
) -> None:
    database = project_cell_migration_database
    database.upgrade(PREVIOUS)
    moving = [
        _seed_subscription(database, plan, status)
        for plan in (1, 4)
        for status in ("pending_payment", "trialing", "active", "past_due", "paused")
    ]
    for plan in (1, 4):
        for status in ("canceled", "expired"):
            _seed_subscription(database, plan, status)
    for plan in (2, 3):
        for status in ("pending_payment", "active", "canceled", "expired"):
            subscription_id = _seed_subscription(database, plan, status)
            code = "pro" if plan == 2 else "business"
            _seed_payment(database, subscription_id, f"subscription:{code}:v1")
    terms_before = database.fetchval(
        "SELECT jsonb_agg(to_jsonb(p) - 'is_active' ORDER BY id) FROM billing_plans p"
    )
    subscriptions_before = _rows(database, "subscriptions")
    payments_before = _rows(database, "payments")

    database.upgrade(CURRENT)

    assert (
        database.fetchval(
            f"SELECT jsonb_agg(to_jsonb(p) - 'is_active' ORDER BY id) FROM billing_plans p "
            f"WHERE id < '{PREFIX}5'"
        )
        == terms_before
    )
    preserved_before = [row for row in subscriptions_before if str(row[0]) not in moving]
    preserved_after = [row for row in _rows(database, "subscriptions") if str(row[0]) not in moving]
    assert preserved_after == preserved_before
    assert _rows(database, "payments") == payments_before
    assert database.fetchval(
        f"SELECT count(*) FROM subscriptions WHERE plan_id = '{PREFIX}5'"
    ) == len(moving)
    active = database.fetch(
        "SELECT id, code, version, price_rub, included_credit_rub, entitlements "
        "FROM billing_plans WHERE is_active ORDER BY sort_order"
    )
    assert [tuple(row[:5]) for row in active] == [
        (active[0][0], "free", 3, Decimal("0.00"), Decimal("0.0000")),
        (active[1][0], "pro", 2, Decimal("2990.00"), Decimal("500.0000")),
        (active[2][0], "business", 2, Decimal("6990.00"), Decimal("1500.0000")),
    ]
    assert [str(row["id"]) for row in active] == [f"{PREFIX}{n}" for n in (5, 6, 7)]
    from yleum_api.models.billing import DEFAULT_BILLING_PLANS

    # This fixture upgrades 0084, not today's catalog head. Compare the exact
    # historical rows that 0084 introduced, even after newer versions activate.
    historical = {str(plan["id"]): plan for plan in DEFAULT_BILLING_PLANS}
    expected = [historical[str(row["id"])] for row in active]
    assert [json.loads(row["entitlements"]) for row in active] == [
        plan["entitlements"] for plan in expected
    ]


@pytest.mark.parametrize("reference", ["subscription", "payment_snapshot"])
def test_catalog_downgrade_refuses_referenced_new_terms(
    project_cell_migration_database: ProjectCellMigrationDatabase,
    reference: str,
) -> None:
    database = project_cell_migration_database
    database.upgrade(CURRENT)
    # Even an ended contract is immutable history. Payment snapshots are checked
    # independently of the subscription's current plan.
    subscription_id = _seed_subscription(
        database, 6 if reference == "subscription" else 2, "expired"
    )
    if reference == "payment_snapshot":
        _seed_payment(database, subscription_id, "subscription:pro:v2")
    before = {
        table: _rows(database, table) for table in ("billing_plans", "subscriptions", "payments")
    }
    with pytest.raises(DBAPIError, match="cannot downgrade billing catalog"):
        command.downgrade(database.config, PREVIOUS)
    assert database.fetchval("SELECT version_num FROM alembic_version") == CURRENT
    assert {table: _rows(database, table) for table in before} == before


def test_unused_catalog_roundtrip(
    project_cell_migration_database: ProjectCellMigrationDatabase,
) -> None:
    database = project_cell_migration_database
    database.upgrade(PREVIOUS)
    before = _rows(database, "billing_plans")
    database.upgrade(CURRENT)
    command.downgrade(database.config, PREVIOUS)
    assert _rows(database, "billing_plans") == before
    database.upgrade(CURRENT)
    assert database.fetchval("SELECT count(*) FROM billing_plans WHERE is_active") == 3
