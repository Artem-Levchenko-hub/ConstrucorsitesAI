"""Existing projects must not be enrolled or lose their runtime during rollout."""

import asyncio
import importlib.util
import os
from pathlib import Path
from uuid import uuid4

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy.ext.asyncio import create_async_engine


def _migration():
    path = Path(__file__).parents[1] / "migrations/versions/0062_project_cell_rollout.py"
    assert path.is_file(), "durable Project Cell admission migration is missing"
    spec = importlib.util.spec_from_file_location("rollout_migration", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_rollout_preserves_legacy_rows_and_requires_explicit_enrollment():
    migration = _migration()
    with sa.create_engine("sqlite://").begin() as connection:
        connection.execute(sa.text("CREATE TABLE projects (id INTEGER PRIMARY KEY, name TEXT)"))
        connection.execute(sa.text("INSERT INTO projects VALUES (1, 'existing business')"))
        migration.op = Operations(MigrationContext.configure(connection))
        migration.upgrade()
        connection.execute(sa.text("INSERT INTO projects (id, name) VALUES (2, 'ordinary')"))
        connection.execute(sa.text(
            "INSERT INTO projects (id, name, project_cell_enabled) VALUES (3, 'cell', true)"
        ))
        rows = connection.execute(sa.text(
            "SELECT id, name, project_cell_enabled FROM projects ORDER BY id"
        )).all()
        assert rows == [(1, "existing business", False), (2, "ordinary", False), (3, "cell", True)]
        with pytest.raises(RuntimeError, match="enrolled Project Cells"):
            migration.downgrade()
        original = connection.execute(sa.text("SELECT name FROM projects WHERE id=1")).scalar_one()
        assert original == "existing business"


def test_rollout_can_be_removed_before_any_project_is_enrolled():
    migration = _migration()
    with sa.create_engine("sqlite://").begin() as connection:
        connection.execute(sa.text("CREATE TABLE projects (id INTEGER PRIMARY KEY, name TEXT)"))
        connection.execute(sa.text("INSERT INTO projects VALUES (1, 'untouched')"))
        migration.op = Operations(MigrationContext.configure(connection))
        migration.upgrade()
        migration.downgrade()
        assert connection.execute(sa.text("SELECT * FROM projects")).all() == [(1, "untouched")]


def test_rollout_on_disposable_postgresql_keeps_existing_data():
    url = os.environ.get("DATABASE_TEST_URL", "")
    if not url:
        pytest.skip("disposable PostgreSQL not configured")
    database = sa.engine.make_url(url).database or ""
    assert "test" in database, "rollout test refuses a non-test database"
    migration = _migration()
    schema = "qa_rollout_" + uuid4().hex

    async def check():
        engine = create_async_engine(url)
        try:
            async with engine.begin() as connection:
                await connection.execute(sa.text(f'CREATE SCHEMA "{schema}"'))
                await connection.execute(sa.text(f'SET LOCAL search_path TO "{schema}"'))
                await connection.execute(sa.text(
                    "CREATE TABLE projects (id integer PRIMARY KEY, name text NOT NULL)"
                ))
                await connection.execute(sa.text("INSERT INTO projects VALUES (1, 'legacy')"))

                def upgrade(sync_connection):
                    migration.op = Operations(MigrationContext.configure(sync_connection))
                    migration.upgrade()

                await connection.run_sync(upgrade)
                await connection.execute(sa.text("INSERT INTO projects (id,name) VALUES (2,'new')"))
                result = await connection.execute(sa.text(
                    "SELECT id,name,project_cell_enabled FROM projects ORDER BY id"
                ))
                assert result.all() == [(1, "legacy", False), (2, "new", False)]
                await connection.execute(sa.text(
                    "UPDATE projects SET project_cell_enabled=true WHERE id=2"
                ))

                def refuse_downgrade(sync_connection):
                    migration.op = Operations(MigrationContext.configure(sync_connection))
                    with pytest.raises(RuntimeError, match="enrolled Project Cells"):
                        migration.downgrade()

                await connection.run_sync(refuse_downgrade)
                await connection.execute(sa.text(f'DROP SCHEMA "{schema}" CASCADE'))
        finally:
            await engine.dispose()

    asyncio.run(check())


def test_downgrade_cannot_race_with_project_enrollment():
    url = os.environ.get("DATABASE_TEST_URL", "")
    if not url:
        pytest.skip("disposable PostgreSQL not configured")
    assert "test" in (sa.engine.make_url(url).database or "")
    schema = "qa_rollout_" + uuid4().hex
    migration = _migration()

    async def check():
        engine = create_async_engine(url)
        pending = None
        try:
            async with engine.begin() as setup:
                await setup.execute(sa.text(f'CREATE SCHEMA "{schema}"'))
                await setup.execute(sa.text(
                    f'CREATE TABLE "{schema}".projects '
                    '(id integer PRIMARY KEY, project_cell_enabled boolean NOT NULL DEFAULT false)'
                ))
            async with engine.connect() as writer, engine.connect() as reader:
                await writer.begin()
                await writer.execute(sa.text(
                    f'INSERT INTO "{schema}".projects VALUES (1, true)'
                ))
                await reader.begin()
                await reader.execute(sa.text(f'SET LOCAL search_path TO "{schema}"'))
                pid = (await reader.execute(sa.text("SELECT pg_backend_pid()"))).scalar_one()

                def downgrade(connection):
                    migration.op = Operations(MigrationContext.configure(connection))
                    migration.downgrade()

                pending = asyncio.create_task(reader.run_sync(downgrade))
                async with engine.connect() as observer:
                    for _ in range(100):
                        blocked = await observer.scalar(sa.text(
                            "SELECT wait_event_type='Lock' FROM pg_stat_activity WHERE pid=:pid"
                        ), {"pid": pid})
                        await observer.commit()
                        if blocked:
                            break
                        await asyncio.sleep(0.02)
                    else:
                        pytest.fail("downgrade never waited for the concurrent writer")
                await writer.commit()
                with pytest.raises(RuntimeError, match="enrolled Project Cells"):
                    await asyncio.wait_for(pending, timeout=5)
                await reader.rollback()
            async with engine.begin() as verify:
                assert await verify.scalar(sa.text(
                    f'SELECT project_cell_enabled FROM "{schema}".projects WHERE id=1'
                )) is True
        finally:
            if pending is not None and not pending.done():
                pending.cancel()
                await asyncio.gather(pending, return_exceptions=True)
            async with engine.begin() as cleanup:
                await cleanup.execute(sa.text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
            await engine.dispose()

    asyncio.run(check())
