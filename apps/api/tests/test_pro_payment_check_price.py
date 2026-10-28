"""One-ruble public Pro must preserve accepted commercial contracts."""

from decimal import Decimal
from importlib import import_module
from unittest.mock import Mock

import pytest
from alembic import command
from sqlalchemy.exc import DBAPIError

from tests.test_billing_catalog_migration import _rows, _seed_subscription
from tests.test_migrations_single_head import (
    ProjectCellMigrationDatabase,
    project_cell_migration_database,
)
from tests.test_public_price_migration import _seed_pending_pro_checkout
from yleum_api.models.billing import DEFAULT_BILLING_PLANS

assert project_cell_migration_database

PREVIOUS = "0098_account_security_offers"
CURRENT = "0099_pro_payment_check_price"
PRO_V4 = "00000000-0000-4000-8000-000000000011"
PRO_V5 = "00000000-0000-4000-8000-000000000012"


def test_new_public_pro_is_one_ruble_with_existing_allowances() -> None:
    active = [p for p in DEFAULT_BILLING_PLANS if p["code"] == "pro" and p["is_active"]]
    assert len(active) == 1
    assert active[0]["price_rub"] == Decimal("1.00")
    assert active[0]["version"] == 5
    assert str(active[0]["id"]) == PRO_V5
    previous = next(p for p in DEFAULT_BILLING_PLANS if str(p["id"]) == PRO_V4)
    assert previous["price_rub"] == Decimal("2990.00")
    assert active[0]["entitlements"] == previous["entitlements"]


def test_pro_price_migration_only_switches_catalog() -> None:
    migration = import_module(f"migrations.versions.{CURRENT}")
    migration.op = Mock()
    migration.upgrade()
    statements = [str(call.args[0]) for call in migration.op.execute.call_args_list]
    assert "INSERT INTO billing_plans" in statements[0]
    assert "SET is_active = false" in statements[1]
    assert "SET is_active = true" in statements[2]
    assert all("subscriptions" not in sql and "payments" not in sql for sql in statements)


def test_pro_test_price_preserves_prior_terms_and_frozen_pending_payment(
    project_cell_migration_database: ProjectCellMigrationDatabase,
) -> None:
    database = project_cell_migration_database
    database.upgrade(PREVIOUS)
    for status in ("pending_payment", "active", "past_due", "paused", "canceled", "expired"):
        subscription_id = _seed_subscription(database, 9, status)
        database.fetchval(
            f"UPDATE subscriptions SET plan_id = '{PRO_V4}'::uuid "
            f"WHERE id = '{subscription_id}'::uuid RETURNING id"
        )
        if status == "pending_payment":
            _seed_pending_pro_checkout(
                database, subscription_id, version=4, amount=Decimal("2990.00"),
            )
    before = {table: _rows(database, table) for table in ("subscriptions", "payments")}
    terms_before = database.fetchval(
        "SELECT jsonb_agg(to_jsonb(p) - 'is_active' ORDER BY id) FROM billing_plans p"
    )

    database.upgrade(CURRENT)

    for table, rows in before.items():
        assert _rows(database, table) == rows
    assert database.fetchval(
        "SELECT jsonb_agg(to_jsonb(p) - 'is_active' ORDER BY id) FROM billing_plans p "
        f"WHERE id != '{PRO_V5}'::uuid"
    ) == terms_before
    assert [tuple(row) for row in database.fetch(
        "SELECT code, version, price_rub FROM billing_plans WHERE is_active ORDER BY sort_order"
    )] == [("free", 4, Decimal("0.00")), ("pro", 5, Decimal("1.00")),
           ("business", 3, Decimal("6990.00"))]
    assert database.fetchval(
        "SELECT count(DISTINCT entitlements) FROM billing_plans "
        f"WHERE id IN ('{PRO_V4}'::uuid, '{PRO_V5}'::uuid)"
    ) == 1


@pytest.mark.parametrize("reference", ["subscription", "payment_snapshot"])
def test_pro_test_price_downgrade_refuses_referenced_terms(
    project_cell_migration_database: ProjectCellMigrationDatabase, reference: str,
) -> None:
    database = project_cell_migration_database
    database.upgrade(CURRENT)
    subscription_id = _seed_subscription(database, 9, "expired")
    if reference == "subscription":
        database.fetchval(
            f"UPDATE subscriptions SET plan_id = '{PRO_V5}'::uuid "
            f"WHERE id = '{subscription_id}'::uuid RETURNING id"
        )
    else:
        _seed_pending_pro_checkout(database, subscription_id)
        database.fetchval(
            "UPDATE payments SET package_code = 'subscription:pro:v5' RETURNING id"
        )
    with pytest.raises(DBAPIError, match="cannot downgrade Pro payment-check price"):
        command.downgrade(database.config, PREVIOUS)
    assert database.fetchval(
        f"SELECT is_active FROM billing_plans WHERE id = '{PRO_V5}'::uuid"
    ) is True
