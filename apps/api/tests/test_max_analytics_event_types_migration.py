"""The additive telemetry migration preserves legacy receipts and validates new types."""

import importlib.util
from pathlib import Path
from uuid import uuid4

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from yleum_api.models.base import Base
from yleum_api.models.project import Project
from yleum_api.models.user import User

MIGRATION_PATH = (
    Path(__file__).resolve().parents[1] / "migrations/versions/0101_max_analytics_event_types.py"
)


async def test_event_type_upgrade_preserves_legacy_and_checks_new_receipts(test_engine, db_session):
    owner = User(email=f"migration-{uuid4().hex}@example.test", password_hash="test")
    db_session.add(owner)
    await db_session.flush()
    project = Project(owner_id=owner.id, name="Migration", slug=uuid4().hex, template="max_miniapp")
    db_session.add(project)
    await db_session.commit()
    spec = importlib.util.spec_from_file_location("analytics_event_types_migration", MIGRATION_PATH)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)

    def exercise(connection):
        migration.op = Operations(MigrationContext.configure(
            connection, opts={"target_metadata": Base.metadata}
        ))
        migration.downgrade()
        identity = uuid4()
        bindings = {"id": identity, "project": project.id, "event": uuid4()}
        connection.execute(text("""
            INSERT INTO max_analytics_events (id, project_id, actor_key, event_id, kind)
            VALUES (:id, :project, 'synthetic', :event, 'open')
        """), bindings)
        migration.upgrade()
        assert connection.execute(text("""
            SELECT id, kind, event_type FROM max_analytics_events WHERE id=:id
        """), {"id": identity}).one() == (identity, "open", None)
        assert any(index["name"] == "ix_max_analytics_project_feed"
                   for index in inspect(connection).get_indexes("max_analytics_events"))
        connection.execute(text("""
            UPDATE max_analytics_events SET event_type='app_open' WHERE id=:id
        """), {"id": identity})
        with pytest.raises(IntegrityError), connection.begin_nested():
            connection.execute(text("""
                UPDATE max_analytics_events SET event_type='order_created' WHERE id=:id
            """), {"id": identity})
        with pytest.raises(IntegrityError), connection.begin_nested():
            connection.execute(text("""
                UPDATE max_analytics_events SET event_type='private arbitrary name' WHERE id=:id
            """), {"id": identity})
        migration.downgrade()
        assert connection.scalar(text("SELECT count(*) FROM max_analytics_events")) == 1
        migration.upgrade()
        assert connection.scalar(text("SELECT kind FROM max_analytics_events")) == "open"
        assert connection.scalar(text("SELECT event_type FROM max_analytics_events")) is None

    async with test_engine.begin() as connection:
        await connection.run_sync(exercise)
