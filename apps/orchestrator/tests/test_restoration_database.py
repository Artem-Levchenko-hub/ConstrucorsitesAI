"""Real PostgreSQL compatibility proof, only in an explicitly disposable database.

No API conftest, no fallback DSN, no generation. Never point this fixture at a
shared/production cluster: it drops and recreates the public schema.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from urllib.parse import urlparse
from uuid import UUID

import asyncpg
import pytest
import pytest_asyncio

from tests._versioning_pg import RESET_DISPOSABLE_DATABASE_SQL

A = UUID("00000000-0000-0000-0000-000000000001")
NEW = UUID("00000000-0000-0000-0000-000000000002")
B = UUID("00000000-0000-0000-0000-000000000003")


@dataclass
class Database:
    admin: asyncpg.Connection


@pytest_asyncio.fixture
async def database():
    dsn = os.environ.get("RESTORATION_TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("requires explicit disposable RESTORATION_TEST_DATABASE_URL")
    parsed = urlparse(dsn)
    if parsed.scheme not in {"postgresql", "postgres"} or parsed.path != "/restoration_policy_test":
        pytest.fail("RESTORATION_TEST_DATABASE_URL must name restoration_policy_test")
    admin = await asyncpg.connect(dsn, timeout=10, command_timeout=30)
    reset_allowed = False
    try:
        if await admin.fetchval("SELECT current_database()") != "restoration_policy_test":
            pytest.fail("connected database must be restoration_policy_test")
        reset_allowed = True
        await admin.execute(RESET_DISPOSABLE_DATABASE_SQL)
        await admin.execute("""
            CREATE TABLE public.contacts (
                id uuid PRIMARY KEY, owner_id text NOT NULL, name text, surname text
            );
            CREATE TABLE public.tasks (
                id uuid PRIMARY KEY,
                contact_id uuid NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
                title text
            );
            CREATE TABLE public.invoices (
                id uuid PRIMARY KEY,
                contact_id uuid NOT NULL REFERENCES contacts(id) ON DELETE CASCADE
            );
            CREATE TABLE public.price_list (id uuid PRIMARY KEY, title text);
            CREATE TABLE public.categories (
                id uuid PRIMARY KEY, parent_id uuid REFERENCES categories(id), title text
            );
        """)
        await admin.executemany(
            "INSERT INTO contacts VALUES($1,$2,$3,$4)",
            [
                (A, "user_a", "Before", "Preserved surname"),
                (NEW, "user_a", "Newer record", "New surname"),
                (B, "user_b", "Foreign", "Foreign surname"),
            ],
        )
        await admin.executemany(
            "INSERT INTO tasks VALUES($1,$2,$3)", [(A, A, "Owned task"), (B, B, "Foreign task")],
        )
        await admin.execute("INSERT INTO invoices VALUES($1,$2)", A, A)
        await admin.execute("INSERT INTO price_list VALUES($1,'Coffee')", A)
        yield Database(admin)
    finally:
        try:
            if reset_allowed:
                await admin.execute(RESET_DISPOSABLE_DATABASE_SQL)
        finally:
            await admin.close()


async def test_ordinary_tables_without_owner_columns_are_compatible(database):
    from yleum_orchestrator.services.restoration_catalog import CATALOG_SQL, contract_from_catalog
    from yleum_orchestrator.services.restoration_data_contract import DataContract, assess_contract

    live, blockers = contract_from_catalog(
        json.loads(await database.admin.fetchval(CATALOG_SQL))
    )
    assert blockers == []
    tables = {table.name: table for table in live.tables}
    assert set(tables) == {"categories", "contacts", "invoices", "price_list", "tasks"}
    assert tables["price_list"].owner_column is None
    assert tables["categories"].owner_reference is None
    historical = DataContract(version=1, tables=[
        tables["price_list"], tables["categories"],
        tables["contacts"].model_copy(update={"columns": [
            column for column in tables["contacts"].columns if column.name != "surname"
        ]}),
    ])
    assessment = assess_contract(historical, live)
    assert assessment.blockers == []
    assert assessment.retained_columns == ["contacts.surname"]


@pytest.mark.parametrize(
    "behavior", ["default", "generated", "domain", "domain_array", "partition", "rule", "event"]
)
async def test_actual_catalog_blocks_unmodeled_execution_and_relations(database, behavior):
    from yleum_orchestrator.services.restoration_catalog import CATALOG_SQL, contract_from_catalog

    statements = {
        "default": "CREATE FUNCTION public.dangerous_default() RETURNS text LANGUAGE plpgsql "
        "SECURITY DEFINER AS $$BEGIN DELETE FROM contacts; RETURN 'unsafe'; END$$; "
        "ALTER TABLE contacts ALTER COLUMN name SET DEFAULT public.dangerous_default()",
        "generated": "ALTER TABLE contacts ADD COLUMN generated_name text "
        "GENERATED ALWAYS AS (upper(name)) STORED",
        "domain": "CREATE DOMAIN public.custom_text AS text; "
        "ALTER TABLE contacts ADD COLUMN special public.custom_text",
        "domain_array": "CREATE DOMAIN public.custom_text AS text; "
        "ALTER TABLE contacts ADD COLUMN special public.custom_text[]",
        "partition": "CREATE TABLE partitioned(id uuid, owner_id text) PARTITION BY HASH(id)",
        "rule": "CREATE RULE no_delete AS ON DELETE TO contacts DO INSTEAD NOTHING",
        "event": "CREATE FUNCTION public.event_hook() RETURNS event_trigger LANGUAGE plpgsql "
        "AS $$BEGIN RETURN; END$$; CREATE EVENT TRIGGER fixture_event "
        "ON ddl_command_start EXECUTE FUNCTION public.event_hook()",
    }
    await database.admin.execute(statements[behavior])
    _, blockers = contract_from_catalog(json.loads(await database.admin.fetchval(CATALOG_SQL)))
    assert blockers
    assert await database.admin.fetchval("SELECT count(*) FROM contacts") == 3


