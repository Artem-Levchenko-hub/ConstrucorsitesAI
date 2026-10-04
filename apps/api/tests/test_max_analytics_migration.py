"""Exercise the real incremental migration on disposable local PostgreSQL."""

import importlib.util
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import inspect

from yleum_api.models.base import Base
from yleum_api.models.max_analytics import MaxAnalyticsEvent

MIGRATION_PATH = Path(__file__).resolve().parents[1] / "migrations/versions/0076_max_analytics.py"


async def test_analytics_migration_up_down_up_preserves_existing_tables(test_engine):
    spec = importlib.util.spec_from_file_location("analytics_migration", MIGRATION_PATH)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)

    def exercise(connection):
        MaxAnalyticsEvent.__table__.drop(connection)
        context = MigrationContext.configure(connection, opts={"target_metadata": Base.metadata})
        migration.op = Operations(context)
        migration.upgrade()
        schema = inspect(connection)
        assert {column["name"] for column in schema.get_columns("max_analytics_events")} == {
            "id",
            "project_id",
            "actor_key",
            "event_id",
            "kind",
            "occurred_at",
        }
        assert schema.get_unique_constraints("max_analytics_events")[0]["name"] == (
            "uq_max_analytics_receipt"
        )
        assert any(
            index["name"] == "ix_max_analytics_project_time"
            for index in schema.get_indexes("max_analytics_events")
        )
        migration.downgrade()
        assert not inspect(connection).has_table("max_analytics_events")
        assert inspect(connection).has_table("projects")
        migration.upgrade()
        assert inspect(connection).has_table("max_analytics_events")

    async with test_engine.begin() as connection:
        await connection.run_sync(exercise)
