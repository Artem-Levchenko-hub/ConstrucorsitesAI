import pytest
from alembic import command

from tests.test_billing_catalog_migration import _seed_subscription
from tests.test_migrations_single_head import project_cell_migration_database

"""Actual Alembic catalog and immutable quote boundary."""


assert project_cell_migration_database
OLD = "0090_aitunnel_text_fallback"
NEW = "0091_generation_credit_units"


def test_real_upgrade_switches_active_catalog_before_inserts(project_cell_migration_database):
    db = project_cell_migration_database
    db.upgrade(OLD)
    free = _seed_subscription(db, 5, "active")
    paid = _seed_subscription(db, 6, "active")
    import asyncio

    from tests.test_migrations_single_head import _execute

    projects = []
    for mode in ("edit", "text"):
        project = str(
            db.fetchval(
                "INSERT INTO projects(id,owner_id,name,slug,template) "
                "SELECT uuid_generate_v4(),user_id,'synthetic',uuid_generate_v4()::text,'blank' "
                "FROM subscriptions WHERE id='" + free + "' RETURNING id"
            )
        )
        asyncio.run(
            _execute(
                db.dsn,
                "INSERT INTO generation_runs(id,project_id,user_id,idempotency_key,prompt_hash,"
                "status,response_mode,agent_state) SELECT uuid_generate_v4(),'" + project + "',"
                "user_id,uuid_generate_v4()::text,'synthetic','completed','" + mode + "',"
                '\'{"billing_accepted_candidate":{"snapshot_id":"deleted"}}\'::jsonb '
                "FROM subscriptions WHERE id='" + free + "'",
            )
        )
        projects.append((project, mode == "edit"))
    db.upgrade(NEW)
    for project, accepted in projects:
        assert (
            db.fetchval(
                "SELECT generation_initial_accepted FROM projects WHERE id='" + project + "'"
            )
            is accepted
        )
    assert (
        str(db.fetchval("SELECT plan_id FROM subscriptions WHERE id='" + free + "'"))
        == "00000000-0000-4000-8000-000000000008"
    )
    assert (
        str(db.fetchval("SELECT plan_id FROM subscriptions WHERE id='" + paid + "'"))
        == "00000000-0000-4000-8000-000000000006"
    )
    assert (
        db.fetchval(
            "SELECT count(*) FROM billing_plans WHERE is_active"
            " AND code IN ('free','pro','business')"
        )
        == 3
    )
    command.downgrade(db.config, OLD)
    assert (
        str(db.fetchval("SELECT plan_id FROM subscriptions WHERE id='" + free + "'"))
        == "00000000-0000-4000-8000-000000000005"
    )
    assert (
        db.fetchval(
            "SELECT count(*) FROM billing_plans WHERE is_active"
            " AND code IN ('free','pro','business')"
        )
        == 3
    )


@pytest.mark.parametrize("contract", ["pending_pack", "paid_subscription"])
def test_downgrade_preserves_immutable_pending_and_paid_quotes(
    project_cell_migration_database, contract
):
    db = project_cell_migration_database
    db.upgrade(NEW)
    sub = _seed_subscription(db, 9 if contract == "paid_subscription" else 8, "pending_payment")
    if contract == "pending_pack":
        import asyncio

        from tests.test_migrations_single_head import _execute

        asyncio.run(
            _execute(
                db.dsn,
                (
                    "INSERT INTO payments(id,billing_account_id,user_id"
                    ",idempotency_key,package_code,amount_rub,credit_ru"
                    "b,edit_units,edits_only,package_version,status) SE"
                    "LECT uuid_generate_v4(),billing_account_id,user_id"
                    ",uuid_generate_v4()::text,'start',1,0,5,true,2,'pe"
                    "nding' FROM subscriptions WHERE id='"
                )
                + sub
                + "'",
            )
        )
    with pytest.raises(RuntimeError, match="Financial unit history"):
        command.downgrade(db.config, OLD)
