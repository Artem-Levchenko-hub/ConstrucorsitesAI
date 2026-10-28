"""Administrative debit evidence is append-only and never rewrites financial facts."""

from uuid import uuid4

import asyncpg  # type: ignore[import-untyped]
import pytest
from alembic import command
from sqlalchemy.exc import DBAPIError

from tests.test_account_security_offer_migration import _seed_normal_contract
from tests.test_migrations_single_head import (
    ProjectCellMigrationDatabase,
    project_cell_migration_database,
)

assert project_cell_migration_database

PREVIOUS = "0099_pro_payment_check_price"
CURRENT = "0100_credit_adjustments"
PROTECTED = {
    "users": "id",
    "billing_accounts": "id",
    "billing_plans": "id",
    "subscriptions": "id",
    "payments": "id",
    "email_notifications": "id",
    "generation_credit_grants": "id",
    "generation_credit_reservations": "run_id",
}


def _protected_rows(database: ProjectCellMigrationDatabase) -> dict[str, list[tuple[object, ...]]]:
    # Frozen table/key pairs: reservations intentionally have no generic id column.
    return {
        table: [tuple(row) for row in database.fetch(f"SELECT * FROM {table} ORDER BY {key}")]
        for table, key in PROTECTED.items()
    }


def test_credit_adjustment_upgrade_and_empty_roundtrip_preserve_contracts(
    project_cell_migration_database: ProjectCellMigrationDatabase,
) -> None:
    database = project_cell_migration_database
    database.upgrade(PREVIOUS)
    _seed_normal_contract(database)
    before = _protected_rows(database)

    database.upgrade(CURRENT)

    assert _protected_rows(database) == before
    assert database.fetchval("SELECT count(*) FROM generation_credit_adjustments") == 0
    assert database.fetchval("""
        SELECT count(*) FROM pg_trigger
        WHERE tgrelid='generation_credit_adjustments'::regclass
          AND tgname='generation_credit_adjustments_append_only' AND NOT tgisinternal
    """) == 1
    command.downgrade(database.config, PREVIOUS)
    assert database.fetchval("SELECT to_regclass('generation_credit_adjustments')") is None
    assert _protected_rows(database) == before
    database.upgrade(CURRENT)
    assert database.fetchval("SELECT count(*) FROM generation_credit_adjustments") == 0


def test_credit_adjustment_evidence_refuses_mutation_and_downgrade(
    project_cell_migration_database: ProjectCellMigrationDatabase,
) -> None:
    database = project_cell_migration_database
    database.upgrade(CURRENT)
    subscription_id = _seed_normal_contract(database)
    bindings = database.fetch(
        "SELECT user_id,billing_account_id FROM subscriptions "
        f"WHERE id='{subscription_id}'::uuid"
    )[0]
    before = _protected_rows(database)
    debit_id, reversal_id = uuid4(), uuid4()
    for operation_id, reversed_id in ((debit_id, None), (reversal_id, debit_id)):
        reference = "NULL" if reversed_id is None else f"'{reversed_id}'::uuid"
        database.fetchval(f"""
            INSERT INTO generation_credit_adjustments
                (id,billing_account_id,user_id,actor_user_id,reason,allocations,
                 reverses_adjustment_id)
            VALUES ('{operation_id}','{bindings['billing_account_id']}',
                    '{bindings['user_id']}','{bindings['user_id']}',
                    'synthetic owner-authorized quota test','[]'::jsonb,{reference})
            RETURNING id
        """)

    assert database.fetchval("SELECT count(*) FROM generation_credit_adjustments") == 2
    for statement in (
        "UPDATE generation_credit_adjustments SET reason='rewritten' RETURNING id",
        "DELETE FROM generation_credit_adjustments RETURNING id",
    ):
        with pytest.raises(
            asyncpg.PostgresError, match="generation_credit_adjustments_append_only",
        ):
            database.fetchval(statement)
    with pytest.raises(DBAPIError, match="generation_credit_adjustment_evidence_present"):
        command.downgrade(database.config, PREVIOUS)
    assert database.fetchval("SELECT version_num FROM alembic_version") == CURRENT
    assert database.fetchval("SELECT count(*) FROM generation_credit_adjustments") == 2
    assert _protected_rows(database) == before
