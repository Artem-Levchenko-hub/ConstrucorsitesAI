"""Actual additive controller SQL, only against the disposable restoration fixture."""

import asyncio
import json
from types import SimpleNamespace
from uuid import UUID

from omnia_orchestrator.services import restoration_catalog, restoration_migrations
from omnia_orchestrator.services.restoration_data_contract import DataContract
from omnia_orchestrator.services.restoration_database import stage_policy
from tests.test_restoration_database import contracts, database  # noqa: F401


async def test_additive_controller_replays_without_losing_rows(
    database,  # noqa: F811 -- imported isolated database fixture
    tmp_path,
    monkeypatch,
):
    old, _ = contracts()
    raw = json.loads(await database.admin.fetchval(restoration_catalog.CATALOG_SQL))
    current, blockers = restoration_catalog.contract_from_catalog(raw, old)
    assert not blockers
    desired = current.model_dump(mode="json")
    desired["tables"][0]["columns"].append({"name": "nickname", "type": "text"})
    desired["tables"].append(
        {
            "name": "projects",
            "owner_column": "owner_id",
            "primary_key": ["id"],
            "columns": [
                {"name": "id", "type": "uuid", "nullable": False},
                {"name": "owner_id", "type": "text", "nullable": False},
                {"name": "title", "type": "text"},
                {"name": "clock", "type": "time without time zone"},
            ],
        }
    )
    target = DataContract.model_validate(desired)
    backend = SimpleNamespace(
        root=tmp_path,
        workspace_id=UUID(int=20),
        project_id=UUID(int=21),
        owner_id=UUID(int=22),
        _container=lambda: None,
    )
    stage_policy(backend, current, 7, blocked_deletes=["contacts"])
    loop = asyncio.get_running_loop()
    statements = []

    async def execute(sql):
        statements.append(sql)
        if sql.startswith("BEGIN;"):
            await database.admin.execute(sql)
            return b""
        value = await database.admin.fetchval(sql)
        if isinstance(value, bool):
            return b"t" if value else b"f"
        return str(value).encode()

    def admin_sql(_backend, sql):
        return asyncio.run_coroutine_threadsafe(execute(sql), loop).result(timeout=40)

    monkeypatch.setattr(restoration_migrations, "admin_sql", admin_sql)
    monkeypatch.setattr(restoration_catalog, "admin_sql", admin_sql)
    before = await database.admin.fetch(
        "SELECT id,owner_id,name,surname,metadata FROM contacts ORDER BY id"
    )
    first = await asyncio.to_thread(
        restoration_migrations.apply_additive_contract, backend, target, expected_epoch=8
    )
    assert first["applied_actions"] == ["add_column:contacts.nickname", "create_table:projects"]
    second = await asyncio.to_thread(
        restoration_migrations.apply_additive_contract, backend, target, expected_epoch=8
    )
    assert second["applied_actions"] == []
    assert first["schema_digest"] == second["schema_digest"]
    assert (
        await database.admin.fetch(
            "SELECT id,owner_id,name,surname,metadata FROM contacts ORDER BY id"
        )
        == before
    )
    assert (
        await database.admin.fetchval("SELECT count(*) FROM contacts WHERE nickname IS NOT NULL")
        == 0
    )
    assert await database.admin.fetchval("SELECT count(*) FROM projects") == 0
    assert len([sql for sql in statements if sql.startswith("BEGIN;")]) == 2
