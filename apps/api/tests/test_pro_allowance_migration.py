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
    business = _seed_subscription(db, 7, "active")
    _seed_payment(db, old, "subscription:pro:v2")
    _seed_payment(db, business, "subscription:business:v2")
    before_subscriptions = _rows(db, "subscriptions")
    before_payments = _rows(db, "payments")
    db.upgrade("head")
    pro = db.fetch(
        "SELECT version, included_credit_rub, entitlements FROM billing_plans "
        "WHERE code='pro' AND is_active"
    )[0]
    assert pro["version"] == 3
    assert pro["included_credit_rub"] == 0
    assert json.loads(pro["entitlements"])["included_creations"] == 2
    assert json.loads(pro["entitlements"])["included_edits"] == 25
    business_terms = db.fetch(
        "SELECT version, included_credit_rub, entitlements FROM billing_plans "
        "WHERE code='business' AND is_active"
    )[0]
    assert business_terms["version"] == 3
    assert business_terms["included_credit_rub"] == 0
    assert json.loads(business_terms["entitlements"])["included_creations"] == 5
    assert json.loads(business_terms["entitlements"])["included_edits"] == 50
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
            (run_id, billing_account_id, user_id, subscription_id, kind,
             period_start, period_end, status, settled_at)
        SELECT uuid_generate_v4(), billing_account_id, user_id, id, 'creation',
               now(), now()+interval '1 month', 'released', now()
        FROM subscriptions WHERE id='{old}'
    """,
        )
    )
    with pytest.raises(DBAPIError, match=r"allowance|quota|referenced"):
        command.downgrade(db.config, PREVIOUS)
    assert db.fetchval("SELECT count(*) FROM generation_allowances") == 1


def test_free_cutover_freezes_legacy_usage_without_fabricating_generation_proof(
    project_cell_migration_database,
):
    import asyncio

    from tests.test_migrations_single_head import _execute

    db = project_cell_migration_database
    db.upgrade(PREVIOUS)
    free = _seed_subscription(db, 5, "active")
    expired = _seed_subscription(db, 5, "expired")
    asyncio.run(
        _execute(
            db.dsn,
            f"""
        UPDATE users SET free_generations_used=8, free_chat_messages_used=1
        WHERE id=(SELECT user_id FROM subscriptions WHERE id='{free}')
    """,
        )
    )
    db.upgrade("head")
    assert (
        db.fetchval(f"SELECT plan_id::text FROM subscriptions WHERE id='{free}'")
        == "00000000-0000-4000-8000-000000000010"
    )
    assert (
        db.fetchval(f"SELECT plan_id::text FROM subscriptions WHERE id='{expired}'")
        == "00000000-0000-4000-8000-000000000005"
    )
    assert (
        db.fetchval(f"""
        SELECT free_creations_carryover FROM users
        WHERE id=(SELECT user_id FROM subscriptions WHERE id='{free}')
    """)
        == 1
    )
    assert db.fetchval("SELECT count(*) FROM generation_allowances") == 0
    terms = json.loads(
        db.fetchval("SELECT entitlements FROM billing_plans WHERE code='free' AND is_active")
    )
    assert terms["included_creations"] == terms["included_edits"] == 1
    assert terms["generation_allowance_period"] == "lifetime"
    # Losing a frozen carryover would restore trial usage; rollback must refuse.
    with pytest.raises(DBAPIError, match=r"allowance|quota|referenced"):
        command.downgrade(db.config, PREVIOUS)
