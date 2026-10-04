"""Actual worker queries under only the deploy script's billing role grants."""

import re
from pathlib import Path
from uuid import uuid4

import asyncpg
import pytest
import test_billing_settlement_postgres as fixtures
import test_generation_billing_postgres as generation
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

database_url = fixtures.database_url
pool = fixtures.pool
ROOT = Path(__file__).resolve().parents[3]
ACCESS = ROOT / "infra/max-k3s/commerce/remote/10-core-billing-access.sh"
GRANTS = ROOT / "infra/max-k3s/commerce/remote/billing-generation-grants.sql"
HOOK = 'psql_admin -v billing_role=max_billing -f "$BILLING_GRANTS_SQL"'


@pytest.fixture
async def worker_role(database_url):
    role = "qa_billing_access_" + uuid4().hex
    assert re.fullmatch(r"qa_billing_access_[0-9a-f]{32}", role)
    conn = await asyncpg.connect(database_url)
    try:
        await conn.execute(f'CREATE ROLE "{role}" NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT')
        script = ACCESS.read_text(encoding="utf-8")
        legacy = re.search(r'psql_admin -v db="\$PG_DB" <<\x27SQL\x27\n(.*?)\nSQL', script, re.S)
        assert legacy is not None
        db = database_url.rsplit("/", 1)[1]
        assert re.fullmatch(r"qa_settlement_[0-9a-f]{32}", db)
        await conn.execute(
            legacy[1].replace(':"db"', f'"{db}"').replace("max_billing", f'"{role}"')
        )
        if HOOK in script:
            await conn.execute(GRANTS.read_text(encoding="utf-8").replace(':"billing_role"', f'"{role}"'))
        yield role
    finally:
        # This role owns no objects and was granted access only in this owned DB.
        # Revoke only its grants before dropping the exact newly-created role.
        await conn.execute(f'DROP OWNED BY "{role}"')
        await conn.execute(f'DROP ROLE "{role}"')
        await conn.close()


async def _tick(database_url, role):
    from yleum_api.services.billing_cycle import run_billing_tick

    engine = create_async_engine(database_url.replace("postgresql://", "postgresql+asyncpg://", 1))
    try:
        async with engine.connect() as conn:
            # Session-level role remains through the worker's independent commits.
            await conn.execute(text(f'SET ROLE "{role}"'))
            await conn.commit()
            async with AsyncSession(bind=conn, expire_on_commit=False) as session:
                return await run_billing_tick(session)
    finally:
        await engine.dispose()


async def test_billing_worker_grants_allow_actual_quiet_tick(database_url, worker_role):
    assert await _tick(database_url, worker_role) == (0, 0)


async def test_billing_role_settles_and_refunds_once(pool, database_url, worker_role):
    data = await generation._run(pool, database_url)
    user, project, messages, run = data
    await generation._provider(user, project, messages, run, "role-paid-" + str(run), "2")
    await generation._prepare_accept(database_url, data)
    assert await generation._finish(database_url, run) == "completed"
    # Own synthetic fixture exercises the worker's missing-intent recovery INSERTs.
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM generation_billing_outbox WHERE run_id=$1", run)
        await conn.execute("DELETE FROM generation_billing_intents WHERE run_id=$1", run)
    await _tick(database_url, worker_role)
    await _tick(database_url, worker_role)
    async with pool.acquire() as conn:
        assert await conn.fetchval("SELECT balance_rub FROM wallets WHERE user_id=$1", user) == 98
        assert await conn.fetchval("SELECT count(*) FROM wallet_charges WHERE user_id=$1", user) == 1
        assert await conn.fetchval("SELECT settled_at IS NOT NULL FROM generation_billing_outbox WHERE run_id=$1", run)
    failed = await generation._run(pool, database_url)
    fu, fp, fm, fr = failed
    await generation._historical_debit(pool, fu, fp, fm, fr, "role-legacy-" + str(fr), "3")
    assert await generation._finish(database_url, fr, failed=True) == "failed"
    await _tick(database_url, worker_role)
    await _tick(database_url, worker_role)
    async with pool.acquire() as conn:
        assert await conn.fetchval("SELECT balance_rub FROM wallets WHERE user_id=$1", fu) == 100
        assert await conn.fetchval("SELECT count(*) FROM wallet_charges WHERE user_id=$1 AND entry_type='refund'", fu) == 1


async def test_new_billing_grants_are_least_privilege_and_idempotent(database_url, worker_role):
    conn = await asyncpg.connect(database_url)
    try:
        sql = GRANTS.read_text(encoding="utf-8").replace(':"billing_role"', f'"{worker_role}"')
        await conn.execute(sql)
        await conn.execute(sql)
        expected = {
            "generation_runs": {"SELECT", "UPDATE"},
            "generation_billing_policies": {"SELECT"},
            "generation_billing_intents": {"SELECT", "INSERT"},
            "generation_billing_outbox": {"SELECT", "INSERT", "UPDATE"},
            "usage": {"SELECT"}, "messages": {"SELECT"}, "snapshots": {"SELECT"},
        }
        for table, privileges in expected.items():
            for privilege in {"SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE", "REFERENCES", "TRIGGER"}:
                assert await conn.fetchval(
                    "SELECT has_table_privilege($1,$2,$3)", worker_role, "public." + table, privilege
                ) == (privilege in privileges), (table, privilege)
        assert not await conn.fetchval("SELECT has_schema_privilege($1,'public','CREATE')", worker_role)
        safe = await conn.fetchrow("SELECT rolsuper,rolcreatedb,rolcreaterole FROM pg_roles WHERE rolname=$1", worker_role)
        assert not any(safe.values())
        await conn.execute(f'SET ROLE "{worker_role}"')
        for query in (
            "UPDATE usage SET cost_rub=0 WHERE false",
            "UPDATE messages SET content='' WHERE false",
            "UPDATE snapshots SET commit_sha='' WHERE false",
            "UPDATE generation_billing_policies SET is_free=true WHERE false",
            "UPDATE generation_billing_intents SET amount_rub=0 WHERE false",
            "DELETE FROM generation_runs WHERE false",
            "TRUNCATE generation_billing_outbox",
            "CREATE TABLE public.qa_forbidden(id int)",
        ):
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await conn.execute(query)
    finally:
        await conn.execute("RESET ROLE")
        await conn.close()
