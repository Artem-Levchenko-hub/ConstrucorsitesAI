import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
import test_billing_settlement_postgres as fixture_source
from test_historical_reservation_settlement import _parked_intent

database_url = fixture_source.database_url
pool = fixture_source.pool


async def test_guarded_0107_upgrade_and_retry(pool, database_url, monkeypatch, tmp_path):
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from alembic.script import ScriptDirectory
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
    from yleum_api.models.base import Base
    from yleum_api.ops import expired_provider_migration as bootstrap
    from yleum_api.services.generation import expired_provider_drain as evidence
    from yleum_api.services.generation.recovery_contract import (
        RecoveryContractError,
        RecoveryStateV3,
        decode_recovery,
    )
    from yleum_api.services.generation_deployment_drain import _LOCK

    from yleum_gateway.services import generation_attempts

    engine = create_async_engine(database_url.replace("postgresql://", "postgresql+asyncpg://", 1))
    incumbent_sha = "e" * 40

    async def verified_reader(*, release_sha=None, own=True):
        if own or release_sha != incumbent_sha:
            raise RecoveryContractError("reader release does not match the incumbent role")
        return "a" * 64

    monkeypatch.setattr(evidence, "require_recovery_readers", verified_reader)
    monkeypatch.setattr(bootstrap, "require_recovery_readers", verified_reader)
    monkeypatch.setattr(generation_attempts, "get_pool", lambda: pool)
    identity, release_sha, source, _, _ = await _parked_intent(pool)
    old = "0106_native_transcript_refusal"
    new = "0107_expired_provider_reservation"
    script = ScriptDirectory(str(fixture_source.ROOT / "apps/api/migrations"))

    def use_old_schema(connection):
        context = MigrationContext.configure(connection, opts={"target_metadata": Base.metadata})
        with Operations.context(context):
            script.get_revision(new).module.downgrade()
        context.stamp(script, old)

    async def state_of_database():
        async with pool.acquire() as conn:
            return await conn.fetchval("SELECT version_num FROM alembic_version"), await conn.fetchrow(
                "SELECT agent_state,status FROM generation_runs WHERE id=$1", identity.run_id,
            )

    async def migrate(scope, *, revision=release_sha, readers=incumbent_sha):
        return await bootstrap.migrate_expired_provider_schema(
            engine, scope, runtime_sha=revision, reader_release_sha=readers,
        )

    try:
        async with engine.begin() as conn:
            await conn.run_sync(use_old_schema)
        now = datetime.now(UTC)
        async with pool.acquire() as conn:
            agent = json.loads(await conn.fetchval(
                "SELECT agent_state FROM generation_runs WHERE id=$1", identity.run_id,
            ))
            raw = agent["recovery_v1"]
            raw["identity"]["supersedes_run_id"] = None
            raw.update(cursor="finalization", clock={
                "version": 1, "stage": "repair", "window_ms": 1500000,
                "segment_used_ms": 1500000, "interval_open": True,
                "segment_deadline": (now - timedelta(seconds=5)).isoformat(),
                "interval_id": str(uuid4()),
            })
            state = decode_recovery(raw)
            assert isinstance(state, RecoveryStateV3)
            await conn.execute(
                "UPDATE generation_runs SET agent_state=$2::jsonb WHERE id=$1",
                identity.run_id, json.dumps({"recovery_v1": state.model_dump(mode="json")}),
            )
        scope = evidence.ExpiredProviderScope(
            release_sha, identity.run_id, identity.attempt_id, state.state_revision,
            state.context_ref.sha256, source, evidence.clock_sha256(state),
        )
        before_head, before_run = await state_of_database()
        assert before_head == old
        async with AsyncSession(engine) as session:
            report = await evidence.classify_expired_provider(
                session, scope, mode="eligibility", reader_release_sha=release_sha,
            )
            assert not report.eligible
        for wrong in (release_sha, "1" * 40):
            with pytest.raises(bootstrap.MigrationBootstrapDenied):
                await migrate(scope, readers=wrong)
            assert (await state_of_database())[0] == old
        for bad, revision in (
            (scope, "1" * 40),
            (replace(scope, release_sha="2" * 40), "2" * 40),
            (replace(scope, state_revision=106), release_sha),
        ):
            with pytest.raises(bootstrap.MigrationBootstrapDenied):
                await migrate(bad, revision=revision)
            assert (await state_of_database())[0] == old
        async with pool.acquire() as conn, conn.transaction():
            await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))", _LOCK)
            with pytest.raises(bootstrap.MigrationBootstrapDenied):
                await migrate(scope)
        async with pool.acquire() as conn, conn.transaction():
            await conn.execute(
                "SELECT 1 FROM generation_provider_attempts WHERE attempt_id=$1 FOR UPDATE",
                identity.attempt_id,
            )
            with pytest.raises(bootstrap.MigrationBootstrapDenied):
                await migrate(scope)
        async with pool.acquire() as conn, conn.transaction():
            await conn.execute(
                "SELECT pg_advisory_xact_lock(1195724366,$1)",
                int.from_bytes(identity.run_id.bytes[-4:], "big", signed=True),
            )
            with pytest.raises(bootstrap.MigrationBootstrapDenied):
                await migrate(scope)
        assert (await state_of_database())[0] == old

        async def reject_after_ddl(_connection):
            raise bootstrap.MigrationBootstrapDenied("synthetic post-DDL confirmation loss")

        with monkeypatch.context() as m:
            m.setattr(bootstrap, "_verify_0107", reject_after_ddl)
            with pytest.raises(bootstrap.MigrationBootstrapDenied):
                await migrate(scope)
        assert await state_of_database() == (old, before_run)

        result = await migrate(scope)
        assert (result.action, result.settled) == ("upgraded", False)
        assert await state_of_database() == (new, before_run)
        result = await migrate(scope)
        assert (result.action, result.settled) == ("already_current", False)
        await generation_attempts.close_expired_before_dispatch(
            generation_attempts.ExpiredReservationScope(identity, release_sha, 105, source),
        )
        result = await migrate(scope)
        assert (result.action, result.settled) == ("already_current", True)
        assert await state_of_database() == (new, before_run)
        with monkeypatch.context() as m:
            m.setattr(bootstrap, "_MIGRATIONS", tmp_path / "missing")
            with pytest.raises(bootstrap.MigrationBootstrapDenied):
                await migrate(scope)
        async with engine.begin() as conn:
            await conn.execute(text(
                "ALTER TABLE generation_provider_attempts "
                "DROP CONSTRAINT ck_generation_provider_attempts_ck_generation_attempt_call"
            ))
        with pytest.raises(bootstrap.MigrationBootstrapDenied):
            await migrate(scope)
    finally:
        await engine.dispose()


def test_bootstrap_scope_rejects_duplicates_and_caller_directives():
    from yleum_api.ops import expired_provider_migration as bootstrap

    valid = {
        "version": 1, "release_sha": "a" * 40, "run_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        "reader_release_sha": "e" * 40, "attempt_id": str(uuid4()),
        "state_revision": 15, "context_sha256": "b" * 64, "source_sha256": "c" * 64,
        "clock_sha256": "d" * 64,
    }
    parsed = bootstrap._scope(json.dumps(valid).encode())
    assert (parsed.scope.release_sha, parsed.reader_release_sha) == (
        valid["release_sha"], valid["reader_release_sha"],
    )
    for invalid in (
        json.dumps({**valid, "sql": "SELECT 1"}).encode(),
        json.dumps({**valid, "settled": True}).encode(),
        json.dumps({**valid, "head": "anything"}).encode(),
        json.dumps({**valid, "run_id": valid["run_id"].upper()}).encode(),
        b'{"version":1,"version":1}',
        b" " * 4097,
    ):
        with pytest.raises((ValueError, TypeError)):
            bootstrap._scope(invalid)
