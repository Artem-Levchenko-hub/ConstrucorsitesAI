"""Normal-price cutover must not reprice an accepted or pending contract."""

import json
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from alembic import command
from sqlalchemy.exc import DBAPIError

from tests.test_billing_catalog_migration import _rows, _seed_payment, _seed_subscription
from tests.test_migrations_single_head import (
    ProjectCellMigrationDatabase,
    project_cell_migration_database,
)
from yleum_api.models.account import Payment
from yleum_api.services.payment_submission import prepare_submission

assert project_cell_migration_database

PREVIOUS = "0096_support_tickets"
CURRENT = "0097_public_prices"
PRO_V3 = "00000000-0000-4000-8000-000000000009"
PRO_V4 = "00000000-0000-4000-8000-000000000011"


def _seed_pending_pro_checkout(
    database: ProjectCellMigrationDatabase, subscription_id: str,
    *, version: int = 3, amount: Decimal = Decimal("1.00"),
) -> None:
    subscription = database.fetch(
        "UPDATE subscriptions SET auto_renew = true, "
        "renewal_consent_version = 'qa-accepted-2026-10-26', "
        "renewal_consented_at = '2026-10-27T06:00:00+00:00' "
        f"WHERE id = '{subscription_id}'::uuid RETURNING user_id, billing_account_id"
    )[0]
    payment = Payment(
        id=uuid4(), user_id=subscription["user_id"],
        billing_account_id=subscription["billing_account_id"],
        subscription_id=UUID(subscription_id), purpose="subscription_initial",
        idempotency_key=str(uuid4()), package_code=f"subscription:pro:v{version}",
        amount_rub=amount, credit_rub=Decimal("0.00"),
        edit_units=0, edits_only=False, package_version=None, status="pending",
        provider_payment_id=None, provider_payload={},
    )
    prepare_submission(
        payment, customer_email="pending-pro@example.invalid", title="Pro subscription",
        return_url="https://example.invalid/billing/plan", save_payment_method=True,
        now=datetime(2026, 10, 27, 6, tzinfo=UTC),
    )
    assert payment.provider_payload["_submission"]["arguments"]["amount"] == f"{amount:.2f}"
    frozen = json.dumps(payment.provider_payload).replace("'", "''")
    database.fetchval(f"""
        INSERT INTO payments
            (id, user_id, billing_account_id, subscription_id, purpose,
             idempotency_key, package_code, amount_rub, credit_rub, status, provider_payload)
        SELECT '{payment.id}'::uuid, user_id, billing_account_id, id, 'subscription_initial',
               '{payment.idempotency_key}', '{payment.package_code}', {amount}, 0.00, 'pending',
               '{frozen}'::jsonb
        FROM subscriptions WHERE id = '{subscription_id}'::uuid RETURNING id
    """)


def test_price_cutover_preserves_paid_and_pending_contracts(
    project_cell_migration_database: ProjectCellMigrationDatabase,
) -> None:
    database = project_cell_migration_database
    database.upgrade(PREVIOUS)
    for status in ("pending_payment", "active", "past_due", "paused", "canceled", "expired"):
        subscription_id = _seed_subscription(database, 9, status)
        if status == "pending_payment":
            _seed_pending_pro_checkout(database, subscription_id)
        else:
            _seed_payment(database, subscription_id, "subscription:pro:v3")
    subscriptions_before = _rows(database, "subscriptions")
    payments_before = _rows(database, "payments")
    terms_before = database.fetchval(
        "SELECT jsonb_agg(to_jsonb(p) - 'is_active' ORDER BY id) FROM billing_plans p"
    )

    database.upgrade(CURRENT)

    assert _rows(database, "subscriptions") == subscriptions_before
    assert _rows(database, "payments") == payments_before
    assert database.fetchval(
        "SELECT jsonb_agg(to_jsonb(p) - 'is_active' ORDER BY id) FROM billing_plans p "
        f"WHERE id != '{PRO_V4}'::uuid"
    ) == terms_before
    active = database.fetch(
        "SELECT code, version, price_rub FROM billing_plans WHERE is_active ORDER BY sort_order"
    )
    assert [tuple(row) for row in active] == [
        ("free", 4, Decimal("0.00")),
        ("pro", 4, Decimal("2990.00")),
        ("business", 3, Decimal("6990.00")),
    ]
    assert database.fetchval(
        f"SELECT price_rub FROM billing_plans WHERE id = '{PRO_V3}'::uuid"
    ) == Decimal("1.00")
    assert database.fetchval(
        "SELECT count(DISTINCT entitlements) FROM billing_plans "
        f"WHERE id IN ('{PRO_V3}'::uuid, '{PRO_V4}'::uuid)"
    ) == 1


@pytest.mark.parametrize("reference", ["subscription", "payment_snapshot"])
def test_public_price_downgrade_refuses_referenced_terms(
    project_cell_migration_database: ProjectCellMigrationDatabase,
    reference: str,
) -> None:
    database = project_cell_migration_database
    database.upgrade(CURRENT)
    subscription_id = _seed_subscription(database, 9, "expired")
    if reference == "subscription":
        database.fetchval(
            f"UPDATE subscriptions SET plan_id = '{PRO_V4}'::uuid "
            f"WHERE id = '{subscription_id}'::uuid RETURNING id"
        )
    else:
        _seed_payment(database, subscription_id, "subscription:pro:v4")
    with pytest.raises(DBAPIError, match="cannot downgrade public prices"):
        command.downgrade(database.config, PREVIOUS)
    assert database.fetchval(
        f"SELECT is_active FROM billing_plans WHERE id = '{PRO_V4}'::uuid"
    ) is True
