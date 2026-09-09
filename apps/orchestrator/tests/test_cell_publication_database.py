"""Independent public policy rotation against an explicitly disposable PostgreSQL.

Uses real controller SQL and public policy staging; Docker writer removal is a
recorded seam. Does not claim a Docker deployment or HTTP publication proof.
"""

import json
import os
from dataclasses import replace
from uuid import UUID

import asyncpg

from omnia_orchestrator.services.restoration_catalog import CATALOG_SQL, contract_from_catalog
from omnia_orchestrator.services.restoration_data_contract import database_policy_sql
from omnia_orchestrator.services.restoration_database import actor_token, load_policy, stage_policy
from tests.test_published_machine_backend import published_backend
from tests.test_restoration_database import NEW, A, B, contracts, database  # noqa: F401


async def test_public_credentials_and_recovery_preserve_live_rows(
    database,  # noqa: F811 -- imported disposable fixture
    tmp_path,
    monkeypatch,
):
    old, current = contracts()
    draft = published_backend(tmp_path / "draft")
    public = replace(draft, root=tmp_path / "public", workspace_id=UUID(int=800))
    draft_policy = stage_policy(draft, old, 7, blocked_deletes=["contacts"])
    calls = []
    monkeypatch.setattr(public, "quiesce_current", lambda: calls.append("quiesce"))
    monkeypatch.setattr(public, "remove", lambda: calls.append("remove"))
    monkeypatch.setattr(public, "_container", lambda: None)
    monkeypatch.setattr(public, "_project_postgres", lambda: None)
    public.stage_public_policy(old, 1, blocked_deletes=["contacts"])
    policy = load_policy(public)
    assert calls == ["quiesce", "remove"]
    assert policy["password"] != draft_policy["password"]
    assert policy["token_secret"] != draft_policy["token_secret"]
    assert policy["workspace_id"] == str(public.workspace_id)
    before = await database.admin.fetch("SELECT * FROM contacts ORDER BY id")

    async def install(value, contract):
        await database.admin.execute(
            database_policy_sql(
                contract,
                epoch=value["epoch"],
                project_id=value["project_id"],
                password=value["password"],
                token_secret=value["token_secret"],
                blocked_deletes=value["blocked_deletes"],
            )
        )

    await install(policy, old)
    payload = json.loads(await database.admin.fetchval(CATALOG_SQL))
    _, blockers = contract_from_catalog(payload, old)
    assert not blockers, "controller JSON guards must remain recognized after publication"
    connection = await asyncpg.connect(
        os.environ["RESTORATION_TEST_DATABASE_URL"],
        user="omnia_runtime",
        password=policy["password"],
    )
    try:
        await connection.execute(
            "SELECT set_config('omnia.actor_token',$1,false)", actor_token(draft_policy, "user_a")
        )
        assert await connection.fetchval("SELECT count(*) FROM contacts") == 0
        await connection.execute(
            "SELECT set_config('omnia.actor_token',$1,false)", actor_token(policy, "user_a")
        )
        assert await connection.fetchval("SELECT count(*) FROM contacts") == 2
        assert (
            await connection.execute("UPDATE contacts SET name='hidden' WHERE id=$1", B)
            == "UPDATE 0"
        )
        await connection.execute("UPDATE contacts SET name='public edit' WHERE id=$1", A)
    finally:
        await connection.close()

    public.stage_public_policy(
        current, 1, blocked_deletes=[], recovery_operation_id="failed-release"
    )
    recovered = load_policy(public)
    assert recovered["epoch"] == policy["epoch"]
    assert recovered["password"] != policy["password"]
    assert recovered["token_secret"] != policy["token_secret"]
    await install(recovered, current)
    payload = json.loads(await database.admin.fetchval(CATALOG_SQL))
    _, blockers = contract_from_catalog(payload, current)
    assert not blockers, "recovery policy guards must permit the next publication"
    after = await database.admin.fetch("SELECT * FROM contacts ORDER BY id")
    assert len(after) == len(before) == 3
    assert next(row for row in after if row["id"] == B) == next(
        row for row in before if row["id"] == B
    )
    assert next(row for row in after if row["id"] == NEW) == next(
        row for row in before if row["id"] == NEW
    )
    assert next(row for row in after if row["id"] == A)["name"] == "public edit"
    assert next(row for row in after if row["id"] == A)["surname"] == "Preserved surname"
