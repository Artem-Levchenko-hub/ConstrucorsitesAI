from __future__ import annotations

import asyncio
import os
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

import asyncpg
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy.engine import make_url

from omnia_api.core.config import get_settings

API_ROOT = Path(__file__).resolve().parents[1]
REMOVED_TABLE = "generation_telegram_reports"


def _asyncpg_dsn(url: str) -> str:
    return url.replace("postgresql+asyncpg://", "postgresql://", 1)


async def _execute(dsn: str, statement: str) -> str:
    connection = await asyncpg.connect(dsn)
    try:
        return await connection.execute(statement)
    finally:
        await connection.close()


async def _fetchval(dsn: str, statement: str) -> object:
    connection = await asyncpg.connect(dsn)
    try:
        return await connection.fetchval(statement)
    finally:
        await connection.close()


async def _fetch_column(dsn: str, statement: str) -> set[str]:
    connection = await asyncpg.connect(dsn)
    try:
        return {str(row[0]) for row in await connection.fetch(statement)}
    finally:
        await connection.close()


@dataclass(frozen=True)
class MigrationDatabase:
    config: Config
    dsn: str

    def upgrade(self, revision: str) -> None:
        command.upgrade(self.config, revision)

    def downgrade(self, revision: str) -> None:
        command.downgrade(self.config, revision)

    def table_exists(self, table: str) -> bool:
        relation = asyncio.run(
            _fetchval(self.dsn, f"SELECT to_regclass('public.{table}')")
        )
        return relation == table


@pytest.fixture
def migration_database(monkeypatch: pytest.MonkeyPatch) -> Iterator[MigrationDatabase]:
    configured_url = os.environ.get("DATABASE_TEST_URL") or os.environ["DATABASE_URL"]
    parsed = make_url(configured_url)
    database_name = f"omnia_migration_{uuid4().hex}"
    target_url = parsed.set(database=database_name).render_as_string(
        hide_password=False
    )
    admin_url = parsed.set(database="postgres").render_as_string(hide_password=False)
    admin_dsn = _asyncpg_dsn(admin_url)
    target_dsn = _asyncpg_dsn(target_url)

    asyncio.run(_execute(admin_dsn, f'CREATE DATABASE "{database_name}"'))
    monkeypatch.setenv("DATABASE_URL", target_url)
    get_settings.cache_clear()
    config = Config(str(API_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(API_ROOT / "migrations"))
    try:
        yield MigrationDatabase(config=config, dsn=target_dsn)
    finally:
        get_settings.cache_clear()
        asyncio.run(
            _execute(admin_dsn, f'DROP DATABASE "{database_name}" WITH (FORCE)')
        )


def test_upgrade_to_head_removes_generation_telegram_reports(
    migration_database: MigrationDatabase,
) -> None:
    migration_database.upgrade("0047_generation_telegram_reports")
    assert migration_database.table_exists(REMOVED_TABLE)
    asyncio.run(
        _execute(
            migration_database.dsn,
            "SET session_replication_role = replica; "
            "INSERT INTO generation_telegram_reports (run_id) "
            "VALUES ('00000000-0000-4000-8000-000000000048'); "
            "SET session_replication_role = origin;",
        )
    )
    assert asyncio.run(
        _fetchval(migration_database.dsn, f"SELECT count(*) FROM {REMOVED_TABLE}")
    ) == 1

    migration_database.upgrade("head")

    assert not migration_database.table_exists(REMOVED_TABLE)


def test_downgrade_recreates_empty_observer_schema(
    migration_database: MigrationDatabase,
) -> None:
    migration_database.upgrade("head")
    assert not migration_database.table_exists(REMOVED_TABLE)

    migration_database.downgrade("0047_generation_telegram_reports")

    assert migration_database.table_exists(REMOVED_TABLE)
    assert asyncio.run(
        _fetchval(migration_database.dsn, f"SELECT count(*) FROM {REMOVED_TABLE}")
    ) == 0
    indexes = asyncio.run(
        _fetch_column(
            migration_database.dsn,
            "SELECT indexname FROM pg_indexes "
            f"WHERE schemaname = 'public' AND tablename = '{REMOVED_TABLE}'",
        )
    )
    assert "ix_generation_telegram_reports_due_work" in indexes
    triggers = asyncio.run(
        _fetch_column(
            migration_database.dsn,
            "SELECT tgname FROM pg_trigger "
            f"WHERE tgrelid = '{REMOVED_TABLE}'::regclass AND NOT tgisinternal",
        )
    )
    assert "generation_telegram_reports_set_updated_at" in triggers
    assert asyncio.run(
        _fetchval(
            migration_database.dsn,
            "SELECT count(*) FROM pg_constraint "
            f"WHERE conrelid = '{REMOVED_TABLE}'::regclass AND contype = 'p'",
        )
    ) == 1
    assert asyncio.run(
        _fetchval(
            migration_database.dsn,
            "SELECT count(*) FROM pg_constraint "
            f"WHERE conrelid = '{REMOVED_TABLE}'::regclass "
            "AND contype = 'f' AND confdeltype = 'c'",
        )
    ) == 1
    assert asyncio.run(
        _fetchval(
            migration_database.dsn,
            "SELECT count(*) FROM pg_constraint "
            f"WHERE conrelid = '{REMOVED_TABLE}'::regclass AND contype = 'c'",
        )
    ) == 6
