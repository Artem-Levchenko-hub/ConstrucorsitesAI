"""Actual PostgreSQL 0077 backfill and isolated downgrade/upgrade checks."""

import importlib.util
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from tests.test_free_chat_allowance import owner_projects
from yleum_api.models.billing import BUSINESS_PLAN_ID, FREE_PLAN_ID, Subscription
from yleum_api.models.billing_usage_event import BillingUsageEvent
from yleum_api.models.generation_run import GenerationRun
from yleum_api.models.message import Message


def migration():
    path = Path(__file__).resolve().parents[1] / "migrations/versions/0077_free_owner_allowance.py"
    spec = importlib.util.spec_from_file_location("qa_free_owner_0077", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def test_actual_0077_backfills_current_free_only_and_preserves_data(test_engine, db_session):
    expected = {}
    for history in ["new", "running_run", "old_message", "deleted_success", "paid_latest"]:
        owner, projects, account = await owner_projects(
            db_session, BUSINESS_PLAN_ID if history == "paid_latest" else FREE_PLAN_ID, count=1
        )
        if history in {"running_run", "paid_latest"}:
            db_session.add(
                GenerationRun(
                    project_id=projects[0].id,
                    user_id=owner.id,
                    status="running",
                    prompt_hash="qa-only",
                    idempotency_key="QA_HISTORY_" + history,
                )
            )
        if history == "old_message":
            db_session.add(Message(project_id=projects[0].id, role="user", content="QA old human"))
        if history == "deleted_success":
            owner.free_generations_used = 2
        if history in {"running_run", "deleted_success", "paid_latest"}:
            db_session.add(
                BillingUsageEvent(
                    billing_account_id=account.id,
                    user_id=owner.id,
                    kind="publication",
                    quantity=1,
                    project_id=None if history == "deleted_success" else projects[0].id,
                )
            )
        if history == "paid_latest":
            # An ended Free subscription must not override the current paid one.
            db_session.add(
                Subscription(
                    billing_account_id=account.id,
                    user_id=owner.id,
                    plan_id=FREE_PLAN_ID,
                    status="expired",
                    created_at=datetime.now(UTC) - timedelta(days=1),
                )
            )
        await db_session.commit()
        expected[owner.id] = (
            int(history not in {"new", "paid_latest"}),
            projects[0].id
            if history == "running_run"
            else UUID(int=0)
            if history == "deleted_success"
            else None,
        )
    await db_session.rollback()
    module = migration()

    def apply(connection, direction):
        with Operations.context(MigrationContext.configure(connection)):
            getattr(module, direction)()

    async with test_engine.begin() as connection:
        plans = list(
            (
                await connection.execute(
                    text(
                        "SELECT id,code,version,entitlements,is_active "
                        "FROM billing_plans ORDER BY id"
                    )
                )
            ).all()
        )
        projects = list(
            (
                await connection.execute(
                    text("SELECT id,owner_id,name,current_snapshot_id FROM projects ORDER BY id")
                )
            ).all()
        )
        runs = list(
            (
                await connection.execute(
                    text("SELECT id,user_id,project_id,status FROM generation_runs ORDER BY id")
                )
            ).all()
        )
        # Recreate the exact predecessor's users shape; all other native tables
        # are unchanged by 0077. Execute the real migration, not synthetic SQL.
        await connection.execute(
            text("ALTER TABLE users DROP CONSTRAINT ck_users_free_chat_messages_used")
        )
        await connection.execute(text("ALTER TABLE users DROP COLUMN free_chat_messages_used"))
        await connection.execute(text("ALTER TABLE users DROP COLUMN free_publication_project_id"))
        await connection.run_sync(lambda sync: apply(sync, "upgrade"))
        values = {
            row[0]: (row[1], row[2])
            for row in (
                await connection.execute(
                    text("SELECT id,free_chat_messages_used,free_publication_project_id FROM users")
                )
            ).all()
        }
        assert values == expected
        assert (
            list(
                (
                    await connection.execute(
                        text(
                            "SELECT id,code,version,entitlements,is_active "
                            "FROM billing_plans ORDER BY id"
                        )
                    )
                ).all()
            )
            == plans
        )
        assert (
            list(
                (
                    await connection.execute(
                        text(
                            "SELECT id,owner_id,name,current_snapshot_id FROM projects ORDER BY id"
                        )
                    )
                ).all()
            )
            == projects
        )
        assert (
            list(
                (
                    await connection.execute(
                        text("SELECT id,user_id,project_id,status FROM generation_runs ORDER BY id")
                    )
                ).all()
            )
            == runs
        )
        foreign_keys = await connection.scalar(
            text(
                "SELECT count(*) FROM pg_constraint WHERE conrelid='users'::regclass "
                "AND contype='f' AND pg_get_constraintdef(oid) LIKE '%free_publication_project_id%'"
            )
        )
        assert foreign_keys == 0
        with pytest.raises(IntegrityError):
            async with connection.begin_nested():
                await connection.execute(text("UPDATE users SET free_chat_messages_used=2"))
        await connection.run_sync(lambda sync: apply(sync, "downgrade"))
        assert (
            await connection.scalar(
                text(
                    "SELECT count(*) FROM information_schema.columns WHERE table_name='users' "
                    "AND column_name IN ('free_chat_messages_used','free_publication_project_id')"
                )
            )
            == 0
        )
        await connection.run_sync(lambda sync: apply(sync, "upgrade"))
        assert {
            row[0]: (row[1], row[2])
            for row in (
                await connection.execute(
                    text("SELECT id,free_chat_messages_used,free_publication_project_id FROM users")
                )
            ).all()
        } == expected
        assert module.down_revision == "0076_max_analytics"
