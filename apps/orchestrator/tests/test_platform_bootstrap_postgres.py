"""Real SQL regression, opt-in only on an explicitly owned loopback test cluster."""

import hashlib
import os
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

import asyncpg
import pytest
import pytest_asyncio

from yleum_orchestrator.services.project_database_roles import bootstrap_project_roles_sql
from yleum_orchestrator.services.project_migrations import (
    ADOPTION_PREFLIGHT_SQL,
    PLATFORM_CORE_MIGRATION,
    migration_sql,
    select_platform_core_migration,
)

pytestmark = pytest.mark.skipif(
    not os.environ.get("YLEUM_PLATFORM_BOOTSTRAP_TEST_ADMIN_URL"),
    reason="requires a newly provisioned owned loopback-only PostgreSQL cluster",
)


@pytest_asyncio.fixture
async def database():
    parsed = urlsplit(os.environ["YLEUM_PLATFORM_BOOTSTRAP_TEST_ADMIN_URL"])
    assert parsed.hostname == "127.0.0.1" and parsed.port not in {None, 5432}
    assert parsed.path == "/postgres"
    name = "platform_bootstrap_test_" + uuid4().hex
    admin = await asyncpg.connect(urlunsplit(parsed))
    try:
        await admin.execute(f'CREATE DATABASE "{name}"')
        connection = await asyncpg.connect(urlunsplit(parsed._replace(path="/" + name)))
        try:
            await connection.execute(bootstrap_project_roles_sql("r" * 32, "m" * 32))
            migrator = await asyncpg.connect(
                host=parsed.hostname, port=parsed.port, database=name,
                user="omnia_project_migrator", password="m" * 32,
            )
            try:
                assert await migrator.fetchrow("SELECT current_user, session_user") == (
                    "omnia_project_migrator", "omnia_project_migrator",
                )
                yield SimpleNamespace(admin=connection, migrator=migrator)
            finally:
                await migrator.close()
        finally:
            await connection.close()
            await admin.execute(f'DROP DATABASE "{name}"')
    finally:
        await admin.close()


def platform_inventory():
    source = (Path(__file__).parents[1] / "templates/max-miniapp-nextjs"
              / PLATFORM_CORE_MIGRATION).read_text(encoding="utf-8")
    inventory = {PLATFORM_CORE_MIGRATION: source,
                 "drizzle/0004_candidate.sql": "CREATE TABLE candidate_must_not_run(id int);"}
    return inventory, select_platform_core_migration(inventory, source)


async def test_fresh_real_platform_sql_is_witnessed_once_and_restores_with_data(database):
    migrator, database = database.migrator, database.admin
    inventory, core = platform_inventory()
    identity = "a" * 64
    # Exact incident: restore cannot certify a pending core migration/no journal.
    with pytest.raises(asyncpg.UndefinedTableError):
        await migrator.execute(migration_sql(core, verify_only=False, verify_applied=True,
                                             database_identity=identity))
    await migrator.execute("ROLLBACK")
    assert await database.fetchval(
        "SELECT to_regclass('public.__omnia_project_migrations')",
    ) is None
    apply = migration_sql(core, verify_only=False, database_identity=identity)
    await database.execute(ADOPTION_PREFLIGHT_SQL)
    await migrator.execute(apply)
    first = await database.fetchrow("SELECT name,sha256,applied_at FROM __omnia_project_migrations")
    assert first["name"] == PLATFORM_CORE_MIGRATION
    assert first["sha256"] == hashlib.sha256(core[PLATFORM_CORE_MIGRATION].encode()).hexdigest()
    assert await database.fetchval("SELECT to_regclass('public.candidate_must_not_run')") is None
    await database.execute(
        "INSERT INTO max_business_actions(id,max_user_id,action_type,payload) "
        "VALUES($1,$2,'retained',$3::jsonb)", uuid4(), "retained-subject", '{"sentinel":"kept"}',
    )
    before = await database.fetch("SELECT * FROM max_business_actions ORDER BY id")
    # Re-executing the actual core SQL would fail on existing policies.
    await migrator.execute(apply)
    assert await database.fetchrow(
        "SELECT name,sha256,applied_at FROM __omnia_project_migrations",
    ) == first
    await migrator.execute(migration_sql(core, verify_only=False, verify_applied=True,
                                         database_identity=identity))
    assert await database.fetch("SELECT * FROM max_business_actions ORDER BY id") == before
    assert await database.fetchval("SELECT count(*) FROM __omnia_project_migrations") == 1
    # Generic restoration stays strict: pending product SQL is never blessed or applied.
    with pytest.raises(asyncpg.RaiseError, match="receipt missing or changed"):
        await migrator.execute(migration_sql(inventory, verify_only=False, verify_applied=True,
                                             database_identity=identity))
    await migrator.execute("ROLLBACK")
    assert await database.fetchval("SELECT to_regclass('public.candidate_must_not_run')") is None
    assert await database.fetch("SELECT * FROM max_business_actions ORDER BY id") == before


async def test_populated_untracked_database_is_never_adopted_by_platform_bootstrap(database):
    database = database.admin
    await database.execute("CREATE TABLE sentinel(id int); INSERT INTO sentinel VALUES(7)")
    with pytest.raises(asyncpg.RaiseError, match="reconcile explicitly"):
        await database.execute(ADOPTION_PREFLIGHT_SQL)
    await database.execute("ROLLBACK")
    assert await database.fetchval(
        "SELECT to_regclass('public.__omnia_project_migrations')",
    ) is None
    assert await database.fetchval("SELECT to_regclass('public.max_business_actions')") is None
    assert await database.fetchval("SELECT count(*) FROM sentinel WHERE id=7") == 1
