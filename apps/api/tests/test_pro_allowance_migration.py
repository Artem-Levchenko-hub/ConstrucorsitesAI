"""Forward cutover changes new sales without rewriting existing paid contracts."""

import json

import pytest
from alembic import command
from sqlalchemy.exc import DBAPIError

from tests.test_billing_catalog_migration import _rows, _seed_payment, _seed_subscription
from tests.test_migrations_single_head import project_cell_migration_database

assert project_cell_migration_database
PREVIOUS = "0088_generation_recovery_effects"


def test_pro_cutover_retains_old_paid_contracts_and_blocks_ledger_loss(
    project_cell_migration_database,
):
    db = project_cell_migration_database
    db.upgrade(PREVIOUS)
    old = _seed_subscription(db, 6, "active")
    _seed_payment(db, old, "subscription:pro:v2")
    before_subscriptions = _rows(db, "subscriptions")
    before_payments = _rows(db, "payments")
    db.upgrade("head")
    pro = db.fetch(
        "SELECT version, included_credit_rub, entitlements FROM billing_plans "
        "WHERE code='pro' AND is_active"
    )[0]
    assert pro["version"] == 3
    assert pro["included_credit_rub"] == 0
    assert json.loads(pro["entitlements"])["included_generations"] == 5
    assert _rows(db, "subscriptions") == before_subscriptions
    assert _rows(db, "payments") == before_payments
    # A durable allowance (including a released one) must never be dropped by
    # rollback. It survives the project/run it described.
    import asyncio

    from tests.test_migrations_single_head import _execute

    asyncio.run(
        _execute(
            db.dsn,
            f"""
        INSERT INTO generation_allowances
            (run_id, billing_account_id, user_id, subscription_id,
             period_start, period_end, status, settled_at)
        SELECT uuid_generate_v4(), billing_account_id, user_id, id,
               now(), now()+interval '1 month', 'released', now()
        FROM subscriptions WHERE id='{old}'
    """,
        )
    )
    with pytest.raises(DBAPIError, match=r"allowance|quota|referenced"):
        command.downgrade(db.config, PREVIOUS)
    assert db.fetchval("SELECT count(*) FROM generation_allowances") == 1
