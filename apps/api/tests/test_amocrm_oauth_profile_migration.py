"""Exercise additive OAuth migration without altering encrypted grants or bindings."""

import importlib.util
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text

from tests.test_max_bot_binding import projects
from yleum_api.models.app_integration import (
    AccountIntegration,
    IntegrationOAuthState,
    ProjectIntegrationBinding,
)


def migration():
    path = Path(__file__).resolve().parents[1] / "migrations/versions/0082_amocrm_oauth_profiles.py"
    spec = importlib.util.spec_from_file_location("qa_amocrm_profiles_0082", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def apply(connection, direction):
    with Operations.context(MigrationContext.configure(connection)):
        getattr(migration(), direction)()


def test_profile_migration_follows_current_head():
    assert migration().down_revision == "0081_max_bot_binding"


async def preserved(engine):
    async with engine.connect() as connection:
        return [
            list((await connection.execute(text(query))).all())
            for query in (
                "SELECT id,user_id,credentials_enc,public_config,token_expires_at,status "
                "FROM app_integrations ORDER BY id",
                "SELECT id,project_id,integration_id,config,enabled "
                "FROM project_integration_bindings ORDER BY id",
                "SELECT id,state_hash,user_id,project_id,provider,expires_at,used_at "
                "FROM integration_oauth_states ORDER BY id",
            )
        ]


async def seed(session):
    owners, values = await projects(session)
    integration = AccountIntegration(
        user_id=owners[0].id, created_by_user_id=owners[0].id, provider="amocrm",
        auth_mode="oauth", credentials_enc="synthetic-encrypted-legacy-grant",
        public_config={"base_url": "https://synthetic.amocrm.ru"},
    )
    session.add(integration)
    await session.flush()
    session.add(ProjectIntegrationBinding(
        project_id=values[0].id, integration_id=integration.id, provider="amocrm",
        config={"pipeline_id": 123, "status_id": 456},
    ))
    session.add(IntegrationOAuthState(
        state_hash="synthetic-state-digest", user_id=owners[0].id,
        project_id=values[0].id, provider="amocrm",
        expires_at=datetime.now(UTC) + timedelta(minutes=15),
    ))
    await session.commit()
    await session.rollback()


async def test_upgrade_preserves_legacy_ciphertext_states_and_bindings(test_engine, db_session):
    await seed(db_session)
    before = await preserved(test_engine)
    async with test_engine.begin() as connection:
        for table, column in (
            ("app_integrations", "oauth_client_key"),
            ("app_integrations", "grant_revision"),
            ("integration_oauth_states", "oauth_client_key"),
            ("integration_oauth_states", "connection_snapshot"),
        ):
            await connection.execute(text(f"ALTER TABLE {table} DROP COLUMN {column}"))
        await connection.run_sync(apply, "upgrade")
        assert (await connection.execute(text(
            "SELECT oauth_client_key,grant_revision FROM app_integrations"
        ))).one() == (None, 0)
        assert (await connection.execute(text(
            "SELECT oauth_client_key,connection_snapshot FROM integration_oauth_states"
        ))).one() == (None, None)
    assert await preserved(test_engine) == before


@pytest.mark.parametrize("table", ["app_integrations", "integration_oauth_states"])
async def test_downgrade_refuses_public_profiles_without_changing_grants(
    test_engine, db_session, table,
):
    await seed(db_session)
    async with test_engine.begin() as connection:
        await connection.execute(text(
            f"UPDATE {table} SET oauth_client_key='amocrm-public-v1'"
        ))
    before = await preserved(test_engine)
    with pytest.raises(RuntimeError, match="oauth_profiles_in_use"):
        async with test_engine.begin() as connection:
            await connection.run_sync(apply, "downgrade")
    assert await preserved(test_engine) == before
    async with test_engine.connect() as connection:
        assert await connection.scalar(text(
            "SELECT COUNT(*) FROM information_schema.columns "
            "WHERE table_schema=current_schema() AND column_name='oauth_client_key' "
            "AND table_name IN ('app_integrations','integration_oauth_states')"
        )) == 2
