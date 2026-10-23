"""Execute migration 0089 with real disposable PostgreSQL, preserving bindings."""

import importlib.util
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text

from tests.test_max_bot_binding import binding, projects
from tests.test_max_bot_binding_migration import snapshot


def apply(connection, direction):
    path = Path(__file__).resolve().parents[1] / "migrations/versions/0089_shared_max_bots.py"
    spec = importlib.util.spec_from_file_location("qa_shared_max_bots_0089", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with Operations.context(MigrationContext.configure(connection)):
        getattr(module, direction)()


async def test_upgrade_removes_only_global_exclusivity_and_preserves_encrypted_bindings(
    test_engine, db_session,
):
    _, values = await projects(db_session)
    original = binding(values[0])
    legacy = binding(values[1], None)
    db_session.add_all([original, legacy])
    await db_session.commit()
    before = await snapshot(test_engine)
    await db_session.rollback()
    async with test_engine.begin() as connection:
        await connection.execute(text(
            "ALTER TABLE max_integrations ADD CONSTRAINT uq_max_integrations_bot_id UNIQUE(bot_id)"
        ))
        await connection.run_sync(apply, "upgrade")
    assert await snapshot(test_engine) == before
    async with test_engine.begin() as connection:
        await connection.execute(text(
            "UPDATE max_integrations SET bot_id='fixture-bot' WHERE bot_id IS NULL"
        ))
    after = await snapshot(test_engine)
    assert len(after) == 2 and all(row.bot_id == "fixture-bot" for row in after)
    assert [(row.id, row.bot_token_enc, row.webhook_secret_enc) for row in after] == [
        (row.id, row.bot_token_enc, row.webhook_secret_enc) for row in before
    ]


async def test_downgrade_refuses_shared_bots_without_deleting_or_reassigning_rows(
    test_engine, db_session,
):
    _, values = await projects(db_session)
    db_session.add_all([binding(values[0]), binding(values[1])])
    await db_session.commit()
    before = await snapshot(test_engine)
    await db_session.rollback()
    with pytest.raises(RuntimeError, match="shared_max_bots_present") as error:
        async with test_engine.begin() as connection:
            await connection.run_sync(apply, "downgrade")
    assert "fixture-bot" not in str(error.value)
    assert await snapshot(test_engine) == before


async def test_safe_downgrade_and_reupgrade_preserve_all_columns(test_engine, db_session):
    _, values = await projects(db_session)
    db_session.add_all([binding(values[0]), binding(values[1], None)])
    await db_session.commit()
    before = await snapshot(test_engine)
    await db_session.rollback()
    async with test_engine.begin() as connection:
        await connection.run_sync(apply, "downgrade")
    async with test_engine.begin() as connection:
        await connection.run_sync(apply, "upgrade")
    assert await snapshot(test_engine) == before
