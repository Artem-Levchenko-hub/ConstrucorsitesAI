"""Execute the actual exclusive MAX bot migration on disposable PostgreSQL."""

import importlib.util
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from tests.test_max_bot_binding import binding, projects


def migration():
    path = Path(__file__).resolve().parents[1] / "migrations/versions/0081_max_bot_binding.py"
    spec = importlib.util.spec_from_file_location("qa_max_bot_binding_0081", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def apply(connection, direction):
    with Operations.context(MigrationContext.configure(connection)):
        getattr(migration(), direction)()


async def snapshot(engine):
    async with engine.connect() as connection:
        return list(
            (
                await connection.execute(
                    text(
                        "SELECT id,project_id,owner_id,bot_id,bot_token_enc,"
                        "webhook_secret_enc,status,"
                        "app_url,webhook_url FROM max_integrations ORDER BY id"
                    )
                )
            ).all()
        )


async def predecessor(engine):
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "ALTER TABLE max_integrations DROP CONSTRAINT IF EXISTS uq_max_integrations_bot_id"
            )
        )


async def test_migration_stops_on_duplicates_without_changing_any_binding(test_engine, db_session):
    await predecessor(test_engine)
    _, values = await projects(db_session)
    duplicate_a, duplicate_b = binding(values[0]), binding(values[1], status="error")
    db_session.add_all([duplicate_a, duplicate_b])
    await db_session.commit()
    before = await snapshot(test_engine)
    await db_session.rollback()
    with pytest.raises(RuntimeError, match="max_bot_binding_duplicates") as error:
        async with test_engine.begin() as connection:
            await connection.run_sync(apply, "upgrade")
    assert "fixture-bot" not in str(error.value)
    assert await snapshot(test_engine) == before
    async with test_engine.connect() as connection:
        assert not await connection.scalar(
            text(
                "SELECT EXISTS(SELECT 1 FROM pg_constraint "
                "WHERE conrelid='max_integrations'::regclass "
                "AND conname='uq_max_integrations_bot_id')"
            )
        )


async def test_real_upgrade_enforces_uniqueness_and_preserves_credentials(test_engine, db_session):
    await predecessor(test_engine)
    _, values = await projects(db_session)
    db_session.add(binding(values[0]))
    await db_session.commit()
    before = await snapshot(test_engine)
    await db_session.rollback()
    async with test_engine.begin() as connection:
        await connection.run_sync(apply, "upgrade")
    assert await snapshot(test_engine) == before
    with pytest.raises(IntegrityError):
        async with db_session.begin_nested():
            target = (
                await db_session.execute(
                    text(
                        "SELECT id,owner_id FROM projects WHERE id NOT IN "
                        "(SELECT project_id FROM max_integrations)"
                    )
                )
            ).one()
            await db_session.execute(
                text(
                    "INSERT INTO max_integrations(id,project_id,owner_id,bot_id,bot_token_enc,"
                    "webhook_secret_enc,status) VALUES(gen_random_uuid(),:p,:o,'fixture-bot',"
                    "'fake-ciphertext','fake-webhook','verified')"
                ),
                {"p": target.id, "o": target.owner_id},
            )
    assert await snapshot(test_engine) == before


async def test_downgrade_changes_only_constraint_and_legacy_null_is_preserved(
    test_engine, db_session
):
    await predecessor(test_engine)
    _, values = await projects(db_session)
    db_session.add_all([binding(values[0]), binding(values[1], None)])
    await db_session.commit()
    before = await snapshot(test_engine)
    await db_session.rollback()
    async with test_engine.begin() as connection:
        await connection.run_sync(apply, "upgrade")
    async with test_engine.begin() as connection:
        await connection.run_sync(apply, "downgrade")
    assert await snapshot(test_engine) == before
