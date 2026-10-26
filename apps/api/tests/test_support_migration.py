"""Support's additive schema preserves existing accounts and conversation evidence."""

import asyncio
from uuid import uuid4

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy.ext.asyncio import create_async_engine

from tests.test_migrations_single_head import _execute, project_cell_migration_database
from yleum_api.models import Base

assert project_cell_migration_database
OLD = "0095_phone_verification"
NEW = "0096_support_tickets"


async def _support_schema_differences(dsn):
    engine = create_async_engine(dsn.replace("postgresql://", "postgresql+asyncpg://", 1))
    try:
        async with engine.connect() as connection:
            return await connection.run_sync(
                lambda sync: compare_metadata(
                    MigrationContext.configure(sync, opts={
                        "include_object": lambda obj, name, kind, reflected, compared: (
                            name.startswith("support_") if kind == "table"
                            else obj.table.name.startswith("support_")
                        ),
                        "compare_server_default": True,
                    }),
                    Base.metadata,
                )
            )
    finally:
        await engine.dispose()


def test_support_upgrade_matches_models_and_empty_rollback_preserves_accounts(
    project_cell_migration_database,
):
    db = project_cell_migration_database
    db.upgrade(OLD)
    user = str(uuid4())
    asyncio.run(_execute(
        db.dsn, f"INSERT INTO users(id,email) VALUES ('{user}','{user}@example.invalid')",
    ))
    db.upgrade(NEW)
    assert asyncio.run(_support_schema_differences(db.dsn)) == []
    command.downgrade(db.config, OLD)
    assert db.fetchval(f"SELECT count(*) FROM users WHERE id='{user}'") == 1
    assert db.fetchval("SELECT to_regclass('public.support_tickets')") is None
    db.upgrade(NEW)
    assert asyncio.run(_support_schema_differences(db.dsn)) == []


def test_support_rollback_refuses_to_erase_conversations(project_cell_migration_database):
    db = project_cell_migration_database
    db.upgrade(NEW)
    ticket = str(uuid4())
    asyncio.run(_execute(
        db.dsn,
        "INSERT INTO support_tickets(id,guest_token_hash,requester_email,subject,"
        "create_request_key,create_payload_hash) "
        f"VALUES ('{ticket}','{'a' * 64}','qa@example.invalid','support evidence',"
        f"'{uuid4()}','{'b' * 64}')",
    ))
    with pytest.raises(RuntimeError, match="support_evidence_present"):
        command.downgrade(db.config, OLD)
    assert db.fetchval("SELECT version_num FROM alembic_version") == NEW
    assert db.fetchval(f"SELECT count(*) FROM support_tickets WHERE id='{ticket}'") == 1
