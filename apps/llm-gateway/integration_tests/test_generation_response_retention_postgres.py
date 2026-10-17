"""Real isolated PostgreSQL reservations and body lifecycle; never provider calls."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
import test_billing_settlement_postgres as fixture_source
from test_generation_attempts_postgres import intent

database_url = fixture_source.database_url
pool = fixture_source.pool
MIGRATION_PATH = (
    Path(__file__).resolve().parents[2] / "api/migrations/versions/0087_generation_responses.py"
)


async def test_body_lifecycle_is_separate_from_financial_attempt(pool):
    identity = await intent(pool)
    async with pool.acquire() as conn:
        assert await conn.fetchval("SELECT count(*) FROM generation_response_budgets") == 0
        assert (
            await conn.fetchval(
                "SELECT count(*) FROM generation_responses WHERE attempt_id=$1", identity.attempt_id
            )
            == 0
        )
        assert (
            await conn.fetchval(
                "SELECT phase FROM generation_provider_attempts WHERE attempt_id=$1",
                identity.attempt_id,
            )
            == "reserved"
        )


async def test_concurrent_initial_reservations_never_overspend(pool, monkeypatch):
    from yleum_gateway.services import generation_response_ledger as service

    monkeypatch.setattr(service, "get_pool", lambda: pool)
    identities = [await intent(pool) for _ in range(4)]
    capacity = service.CapacitySnapshot(
        "synthetic-private",
        2_200_000,
        10_000_000,
        uuid4(),
        uuid4(),
        expires_at=int(datetime.now(UTC).timestamp()) + 15,
    )

    async def admit(identity):
        try:
            return await service.admit_response(identity, capacity)
        except service.ResponseIndeterminate:
            return None

    admitted = await asyncio.gather(*(admit(item) for item in identities))
    assert sum(value is not None for value in admitted) == 2
    async with pool.acquire() as conn:
        allocated = await conn.fetchval(
            "SELECT allocated_bytes FROM generation_response_budgets WHERE bucket=$1",
            capacity.bucket,
        )
        assert 0 < allocated <= capacity.quota_bytes
        assert await conn.fetchval("SELECT count(*) FROM provider_calls") == 0


async def test_snapshot_before_other_writer_ack_does_not_regrant_physical_space(pool, monkeypatch):
    from yleum_gateway.services import generation_response_ledger as ledger

    monkeypatch.setattr(ledger, "get_pool", lambda: pool)
    first, second = await intent(pool), await intent(pool)
    # Both snapshots precede the first writer's materialization. The second
    # snapshot cannot deduct an ACK that happened after its measurement.
    snapshot = ledger.CapacitySnapshot(
        "snapshot-" + uuid4().hex,
        10_000_000,
        1_500_000,
        uuid4(),
        uuid4(),
        expires_at=int(datetime.now(UTC).timestamp()) + 15,
    )
    writer = await ledger.admit_response(first, snapshot)
    part = {
        "kind": "chunks",
        "index": 0,
        "key": "synthetic/ack",
        "sha256": "a" * 64,
        "bytes": 900_000,
    }
    await writer.reserve(part)
    await writer.acknowledge(part)
    with pytest.raises(ledger.ResponseIndeterminate, match="capacity unavailable"):
        await ledger.admit_response(second, snapshot)
    async with pool.acquire() as conn:
        assert (
            await conn.fetchval(
                "SELECT count(*) FROM generation_responses WHERE attempt_id=$1", second.attempt_id
            )
            == 0
        )
        assert await conn.fetchval("SELECT count(*) FROM provider_calls") == 0


async def test_capacity_expiry_while_pool_waits_never_grants_admission(pool, monkeypatch):
    from contextlib import asynccontextmanager
    from unittest.mock import AsyncMock

    import httpx

    from yleum_gateway.core import generation_response_auth as auth
    from yleum_gateway.services import generation_response_capture as capture
    from yleum_gateway.services import generation_response_ledger as ledger

    identity, nonce, epoch = await intent(pool), uuid4(), uuid4()
    clock = [datetime.now(UTC)]
    secret = "synthetic-capacity-secret-over-32-bytes"
    values = {
        "version": 1,
        "bucket": "expired-snapshot",
        "epoch": str(epoch),
        "quota_bytes": 10000000,
        "free_bytes": 10000000,
        "measured_at": int(clock[0].timestamp()),
    }
    token = auth.sign_capacity_snapshot(values, identity, str(nonce), secret=secret)

    class Clock:
        @staticmethod
        def now(tz):
            return clock[0]

    class WaitingPool:
        @asynccontextmanager
        async def acquire(self):
            async with pool.acquire() as conn:
                clock[0] += timedelta(seconds=16)
                yield conn

    monkeypatch.setattr(auth, "datetime", Clock)
    monkeypatch.setattr(auth, "_capacity_secret", lambda value=None: secret)
    monkeypatch.setattr(ledger, "_now", lambda: clock[0].timestamp(), raising=False)
    monkeypatch.setattr(ledger, "get_pool", WaitingPool)
    monkeypatch.setattr(capture, "uuid4", lambda: nonce)
    monkeypatch.setattr(capture, "validate_current_identity", AsyncMock())
    monkeypatch.setattr(capture, "PrivateObjects", lambda: SimpleNamespace(preflight=AsyncMock()))
    monkeypatch.setattr(
        capture,
        "get_settings",
        lambda: SimpleNamespace(
            generation_response_capacity_url="http://capacity.test",
            generation_response_s3_bucket=values["bucket"],
            generation_response_storage_epoch=str(epoch),
            generation_response_storage_budget_bytes=10000000,
            generation_response_capacity_min_free_bytes=0,
        ),
    )
    actual_client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: actual_client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(200, json={"assertion": token})
            ),
            **kwargs,
        ),
    )
    with pytest.raises(ledger.ResponseIndeterminate):
        await capture.preflight_response_store(identity)
    async with pool.acquire() as conn:
        assert (
            await conn.fetchval(
                "SELECT count(*) FROM generation_responses WHERE attempt_id=$1", identity.attempt_id
            )
            == 0
        )
        assert await conn.fetchval("SELECT count(*) FROM provider_calls") == 0


@pytest.mark.parametrize("committed", [False, True])
async def test_unknown_storage_admission_commit_never_grants_dispatch(pool, monkeypatch, committed):
    from contextlib import asynccontextmanager

    from yleum_gateway.services import generation_response_ledger as ledger

    identity = await intent(pool)

    class Connection:
        def __init__(self, actual):
            self.actual = actual

        def __getattr__(self, name):
            return getattr(self.actual, name)

        @asynccontextmanager
        async def transaction(self):
            async with self.actual.transaction():
                yield
                if not committed:
                    raise OSError("synthetic unknown commit")
            raise OSError("synthetic lost committed acknowledgement")

    class Pool:
        @asynccontextmanager
        async def acquire(self):
            async with pool.acquire() as conn:
                yield Connection(conn)

    monkeypatch.setattr(ledger, "get_pool", lambda: Pool())
    capacity = ledger.CapacitySnapshot(
        "unknown-" + uuid4().hex,
        10000000,
        10000000,
        uuid4(),
        uuid4(),
        expires_at=int(datetime.now(UTC).timestamp()) + 15,
    )
    permissions = []
    with pytest.raises(ledger.ResponseIndeterminate):
        permissions.append(await ledger.admit_response(identity, capacity))
    assert permissions == []
    async with pool.acquire() as conn:
        assert await conn.fetchval(
            "SELECT count(*) FROM generation_responses WHERE attempt_id=$1", identity.attempt_id
        ) == int(committed)
        assert (
            await conn.fetchval(
                "SELECT count(*) FROM provider_calls WHERE run_id=$1", identity.run_id
            )
            == 0
        )
        if committed:
            assert (
                await conn.fetchval(
                    "SELECT allocated_bytes FROM generation_response_budgets WHERE bucket=$1",
                    capacity.bucket,
                )
                > 0
            )


class Objects:
    def __init__(self):
        self.data = {}
        self.reads = 0

    async def put(self, key, data):
        self.data[key] = data

    async def get(self, key):
        self.reads += 1
        return self.data[key]

    async def delete(self, key):
        self.data.pop(key, None)


async def stored(pool, monkeypatch, *, reason="completed"):
    from yleum_gateway.core import db
    from yleum_gateway.services import generation_attempts as attempts
    from yleum_gateway.services import generation_response_ledger as ledger
    from yleum_gateway.services.generation_response_store import ChunkWriter

    monkeypatch.setattr(db, "get_pool", lambda: pool)
    monkeypatch.setattr(attempts, "get_pool", lambda: pool)
    monkeypatch.setattr(ledger, "get_pool", lambda: pool)
    identity = await intent(pool)
    writer = await ledger.admit_response(
        identity,
        ledger.CapacitySnapshot(
            "owned-" + str(uuid4()),
            10000000,
            10000000,
            uuid4(),
            uuid4(),
            expires_at=int(datetime.now(UTC).timestamp()) + 15,
        ),
    )
    call = await attempts.dispatch(
        identity,
        route="/v1/messages",
        model="claude-sonnet-5.5",
        user_id=identity.user_id,
        run_id=identity.run_id,
        project_id=identity.project_id,
    )
    # Synthetic settled journal receipt: this fixture tests body locks/lifecycle,
    # not monetary reconciliation (covered by the joint settlement suites).
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE provider_calls SET status=$3,finished_at=now(),receipt_hash=$2 WHERE id=$1",
            call,
            "c" * 64,
            "completed" if reason == "completed" else "failed",
        )
        await conn.execute(
            "UPDATE generation_provider_attempts SET phase='closed',terminal_reason=$2 WHERE attempt_id=$1",
            identity.attempt_id,
            reason,
        )
    objects = Objects()

    async def body():
        yield b"owned response bytes"

    await ChunkWriter(identity.attempt_id, objects, writer, chunk_bytes=4).store(
        body(), finish_reason="stop", route="/v1/messages"
    )
    await writer.reconciled()
    return identity, writer, objects, call


@pytest.mark.parametrize("reason", ["preconnect", "rate_limit_refused"])
async def test_confirmed_zero_charge_terminal_body_is_retained_without_new_post(
    pool, monkeypatch, reason
):
    from yleum_gateway.services.generation_response_store import load_generation_response

    identity, _, objects, _ = await stored(pool, monkeypatch, reason=reason)
    assert await load_generation_response(identity, objects=objects) == b"owned response bytes"


async def test_read_integrity_and_foreign_owner_fail_before_object_load(pool, monkeypatch):
    from yleum_gateway.services.generation_response_store import (
        ResponseIndeterminate,
        load_generation_response,
    )

    identity, _, objects, _ = await stored(pool, monkeypatch)
    assert await load_generation_response(identity, objects=objects) == b"owned response bytes"
    key = next(key for key in objects.data if "/chunks/" in key)
    objects.data[key] = b"corrupt"
    with pytest.raises(ResponseIndeterminate):
        await load_generation_response(identity, objects=objects)
    before = objects.reads
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE generation_runs SET status='cancelled' WHERE id=$1", identity.run_id
        )
    with pytest.raises(ResponseIndeterminate):
        await load_generation_response(identity, objects=objects)
    assert objects.reads == before


@pytest.mark.parametrize(
    "field",
    [
        "attempt_id",
        "run_id",
        "project_id",
        "user_id",
        "prompt_hash",
        "request_sha256",
        "owner_transfer",
    ],
)
async def test_current_identity_changes_deny_before_object_read(pool, monkeypatch, field):
    from dataclasses import replace

    from yleum_gateway.services.generation_response_store import (
        ResponseIndeterminate,
        load_generation_response,
    )

    identity, _, objects, _ = await stored(pool, monkeypatch)
    before = objects.reads
    if field == "owner_transfer":
        other = await intent(pool)
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE projects SET owner_id=$2 WHERE id=$1", identity.project_id, other.user_id
            )
        changed = identity
    else:
        changed = replace(
            identity,
            **{field: "f" * 64 if field.endswith("hash") or field == "request_sha256" else uuid4()},
        )
    with pytest.raises(ResponseIndeterminate):
        await load_generation_response(changed, objects=objects)
    assert objects.reads == before


async def test_completed_financial_receipt_without_body_is_not_recoverable(pool, monkeypatch):
    from yleum_gateway.services.generation_response_store import (
        ResponseIndeterminate,
        load_generation_response,
    )

    identity, _, objects, call = await stored(pool, monkeypatch)
    async with pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM generation_response_chunks WHERE attempt_id=$1", identity.attempt_id
        )
        await conn.execute(
            "DELETE FROM generation_responses WHERE attempt_id=$1", identity.attempt_id
        )
        assert await conn.fetchval(
            "SELECT receipt_hash IS NOT NULL FROM provider_calls WHERE id=$1", call
        )
    before = objects.reads
    with pytest.raises(ResponseIndeterminate):
        await load_generation_response(identity, objects=objects)
    assert objects.reads == before


async def test_cleanup_holds_and_terminal_ttl_preserve_all_financial_evidence(pool, monkeypatch):
    from yleum_gateway.services import generation_response_cleanup as cleanup

    identity, writer, objects, call = await stored(pool, monkeypatch)
    monkeypatch.setattr(cleanup, "get_pool", lambda: pool)
    monkeypatch.setattr(
        cleanup,
        "get_settings",
        lambda: SimpleNamespace(
            generation_response_cleanup_enabled=True,
            generation_response_retention_enabled=True,
            generation_response_retention_seconds=604800,
        ),
    )
    now = datetime.now(UTC)
    assert await cleanup.delete_expired_generation_responses(now, objects=objects) == 0
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE generation_runs SET status='completed',finished_at=$2 WHERE id=$1",
            identity.run_id,
            now - timedelta(days=8),
        )
        await conn.execute(
            "UPDATE generation_responses SET reconciled_at=$2,reconciliation_hold=true WHERE attempt_id=$1",
            identity.attempt_id,
            now - timedelta(days=8),
        )
    assert await cleanup.delete_expired_generation_responses(now, objects=objects) == 0
    assert objects.data
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE generation_responses SET reconciliation_hold=false WHERE attempt_id=$1",
            identity.attempt_id,
        )
    assert await cleanup.delete_expired_generation_responses(now, objects=objects) == 1
    assert not objects.data
    async with pool.acquire() as conn:
        assert (
            await conn.fetchval(
                "SELECT count(*) FROM generation_response_chunks WHERE attempt_id=$1",
                identity.attempt_id,
            )
            == 0
        )
        assert (
            await conn.fetchval("SELECT status FROM provider_calls WHERE id=$1", call)
            == "completed"
        )
        assert (
            await conn.fetchval(
                "SELECT phase FROM generation_provider_attempts WHERE attempt_id=$1",
                identity.attempt_id,
            )
            == "closed"
        )
        assert (
            await conn.fetchval(
                "SELECT allocated_bytes FROM generation_response_budgets WHERE bucket=$1",
                writer.bucket,
            )
            == 0
        )


async def test_cleanup_recent_bodies_do_not_starve_due_body(pool, monkeypatch):
    from yleum_gateway.services import generation_response_cleanup as cleanup

    identity, writer, objects, _ = await stored(pool, monkeypatch)
    monkeypatch.setattr(cleanup, "get_pool", lambda: pool)
    monkeypatch.setattr(
        cleanup,
        "get_settings",
        lambda: SimpleNamespace(
            generation_response_cleanup_enabled=True,
            generation_response_retention_enabled=True,
            generation_response_retention_seconds=604800,
        ),
    )
    now = datetime.now(UTC)
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE generation_runs SET status='completed',finished_at=$2 WHERE id=$1",
            identity.run_id,
            now - timedelta(days=8),
        )
        await conn.execute(
            "UPDATE generation_responses SET reconciled_at=$2 WHERE attempt_id=$1",
            identity.attempt_id,
            now - timedelta(days=8),
        )
        # Recent bodies sharing a finished task sort before the due body. They
        # have no owned journal, so neither lifetime nor accounting permits GC.
        for _ in range(50):
            other = uuid4()
            await conn.execute(
                "INSERT INTO generation_provider_attempts(attempt_id,run_id,project_id,user_id,dispatch_token,prompt_hash,request_sha256) "
                "SELECT $2,run_id,project_id,user_id,$2,prompt_hash,request_sha256 FROM generation_provider_attempts WHERE attempt_id=$1",
                identity.attempt_id,
                other,
            )
            await conn.execute(
                "INSERT INTO generation_responses(attempt_id,bucket,writer_token,state,allocated_bytes,manifest_key,manifest_sha256,response_sha256,reconciliation_hold,reconciled_at,created_at) "
                "VALUES($1,$2,$1,'complete',0,'synthetic','synthetic','synthetic',false,$3,$4)",
                other,
                writer.bucket,
                now,
                now - timedelta(days=9),
            )
    assert await cleanup.delete_expired_generation_responses(now, objects=objects) == 1
    assert not objects.data


async def test_read_hold_prevents_cleanup_without_holding_global_budget(pool, monkeypatch):
    from yleum_gateway.services import generation_response_cleanup as cleanup
    from yleum_gateway.services import generation_response_ledger as service
    from yleum_gateway.services.generation_response_store import load_generation_response

    identity, writer, objects, _ = await stored(pool, monkeypatch)
    now = datetime.now(UTC)
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE generation_responses SET reconciled_at=$2 WHERE attempt_id=$1",
            identity.attempt_id,
            now - timedelta(days=8),
        )
        epoch = await conn.fetchval(
            "SELECT storage_epoch FROM generation_response_budgets WHERE bucket=$1", writer.bucket
        )
    entered, release = asyncio.Event(), asyncio.Event()
    original_get = objects.get

    async def slow(key):
        entered.set()
        await release.wait()
        return await original_get(key)

    objects.get = slow
    reader = asyncio.create_task(load_generation_response(identity, objects=objects))
    try:
        await asyncio.wait_for(entered.wait(), 3)
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE generation_runs SET status='completed',finished_at=$2 WHERE id=$1",
                identity.run_id,
                now - timedelta(days=8),
            )
        monkeypatch.setattr(cleanup, "get_pool", lambda: pool)
        monkeypatch.setattr(
            cleanup,
            "get_settings",
            lambda: SimpleNamespace(
                generation_response_cleanup_enabled=True,
                generation_response_retention_enabled=True,
                generation_response_retention_seconds=604800,
            ),
        )
        assert (
            await asyncio.wait_for(
                cleanup.delete_expired_generation_responses(now, objects=objects), 2
            )
            == 0
        )
        other = await intent(pool)
        await asyncio.wait_for(
            service.admit_response(
                other,
                service.CapacitySnapshot(
                    writer.bucket,
                    10000000,
                    10000000,
                    epoch,
                    uuid4(),
                    expires_at=int(datetime.now(UTC).timestamp()) + 15,
                ),
            ),
            2,
        )
    finally:
        release.set()
        assert await asyncio.wait_for(reader, 3) == b"owned response bytes"
    assert await cleanup.delete_expired_generation_responses(now, objects=objects) == 1


async def test_duplicate_admission_cannot_hold_global_budget_behind_slow_reader(pool, monkeypatch):
    from yleum_gateway.services import generation_response_ledger as ledger
    from yleum_gateway.services.generation_response_store import load_generation_response

    identity, writer, objects, _ = await stored(pool, monkeypatch)
    other = await intent(pool)
    async with pool.acquire() as conn:
        epoch = await conn.fetchval(
            "SELECT storage_epoch FROM generation_response_budgets WHERE bucket=$1", writer.bucket
        )
    capacity = ledger.CapacitySnapshot(
        writer.bucket,
        10000000,
        10000000,
        epoch,
        uuid4(),
        expires_at=int(datetime.now(UTC).timestamp()) + 15,
    )
    reading, release, duplicate_entered = asyncio.Event(), asyncio.Event(), asyncio.Event()
    actual_get, actual_owned = objects.get, ledger._owned

    async def slow(key):
        reading.set()
        await release.wait()
        return await actual_get(key)

    async def observed_owned(conn, attempt_id, **kwargs):
        if attempt_id == identity.attempt_id:
            duplicate_entered.set()
        return await actual_owned(conn, attempt_id, **kwargs)

    objects.get = slow
    reader = asyncio.create_task(load_generation_response(identity, objects=objects))
    duplicate = None
    try:
        await asyncio.wait_for(reading.wait(), 3)
        monkeypatch.setattr(ledger, "_owned", observed_owned)
        duplicate = asyncio.create_task(ledger.admit_response(identity, capacity))
        await asyncio.wait_for(duplicate_entered.wait(), 3)
        await asyncio.wait_for(ledger.admit_response(other, capacity), 1)
        with pytest.raises(ledger.ResponseIndeterminate):
            await asyncio.wait_for(asyncio.shield(duplicate), 1)
        async with pool.acquire() as conn:
            assert (
                await conn.fetchval(
                    "SELECT count(*) FROM provider_calls WHERE run_id=$1", identity.run_id
                )
                == 1
            )
    finally:
        release.set()
        await asyncio.wait_for(reader, 3)
        if duplicate is not None:
            await asyncio.gather(duplicate, return_exceptions=True)


async def test_unknown_delete_keeps_allocations_and_same_namespace_retry_is_safe(pool, monkeypatch):
    from yleum_gateway.services import generation_response_cleanup as cleanup

    identity, writer, objects, call = await stored(pool, monkeypatch)
    now = datetime.now(UTC)
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE generation_runs SET status='completed',finished_at=$2 WHERE id=$1",
            identity.run_id,
            now - timedelta(days=8),
        )
        await conn.execute(
            "UPDATE generation_responses SET reconciled_at=$2 WHERE attempt_id=$1",
            identity.attempt_id,
            now - timedelta(days=8),
        )
        allocated = await conn.fetchval(
            "SELECT allocated_bytes FROM generation_response_budgets WHERE bucket=$1", writer.bucket
        )
    monkeypatch.setattr(cleanup, "get_pool", lambda: pool)
    monkeypatch.setattr(
        cleanup,
        "get_settings",
        lambda: SimpleNamespace(
            generation_response_cleanup_enabled=True,
            generation_response_retention_enabled=True,
            generation_response_retention_seconds=604800,
        ),
    )
    original_delete = objects.delete

    async def uncertain(key):
        await original_delete(key)
        raise OSError("synthetic lost deletion acknowledgement")

    objects.delete = uncertain
    assert await cleanup.delete_expired_generation_responses(now, objects=objects) == 0
    async with pool.acquire() as conn:
        assert (
            await conn.fetchval(
                "SELECT allocated_bytes FROM generation_response_budgets WHERE bucket=$1",
                writer.bucket,
            )
            == allocated
        )
        assert (
            await conn.fetchval(
                "SELECT state FROM generation_responses WHERE attempt_id=$1", identity.attempt_id
            )
            == "deleting"
        )
        assert await conn.fetchval("SELECT count(*) FROM provider_calls WHERE id=$1", call) == 1
    objects.delete = original_delete
    assert await cleanup.delete_expired_generation_responses(now, objects=objects) == 1
    assert not objects.data


async def test_stale_writer_and_ambiguous_receipt_never_release_hold(pool, monkeypatch):
    from yleum_gateway.services.generation_response_ledger import ResponseLedger
    from yleum_gateway.services.generation_response_store import ResponseIndeterminate

    identity, writer, _, _ = await stored(pool, monkeypatch)
    stale = ResponseLedger(identity.attempt_id, writer.bucket, uuid4())
    with pytest.raises(ResponseIndeterminate):
        await stale.reconciled()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE generation_responses SET reconciliation_hold=true WHERE attempt_id=$1",
            identity.attempt_id,
        )
        await conn.execute(
            "UPDATE generation_provider_attempts SET phase='indeterminate',terminal_reason=NULL WHERE attempt_id=$1",
            identity.attempt_id,
        )
    with pytest.raises(ResponseIndeterminate):
        await writer.reconciled()
    async with pool.acquire() as conn:
        assert await conn.fetchval(
            "SELECT reconciliation_hold FROM generation_responses WHERE attempt_id=$1",
            identity.attempt_id,
        )


@pytest.mark.parametrize("mode", ["empty", "populated", "concurrent"])
async def test_body_downgrade_preserves_committed_and_inflight_storage_evidence(
    pool, database_url, mode
):
    import importlib.util

    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    identity = await intent(pool)
    spec = importlib.util.spec_from_file_location("migration_task8", MIGRATION_PATH)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    schema = "task8_downgrade_" + uuid4().hex
    engine = create_async_engine(database_url.replace("postgresql://", "postgresql+asyncpg://", 1))
    pending = None

    def apply(conn, operation):
        migration.op = Operations(MigrationContext.configure(conn))
        getattr(migration, operation)()

    async def insert(conn):
        await conn.execute(
            text(
                f'INSERT INTO "{schema}".generation_responses(attempt_id,bucket,writer_token,allocated_bytes) '
                "VALUES(:id,'private',:token,10)"
            ),
            {"id": identity.attempt_id, "token": uuid4()},
        )

    try:
        async with engine.begin() as conn:
            await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
            await conn.execute(text(f'SET LOCAL search_path TO "{schema}",public'))
            await conn.run_sync(apply, "upgrade")
            await conn.execute(
                text(
                    "INSERT INTO generation_response_budgets(bucket,storage_epoch,quota_bytes,observation_nonce) VALUES('private',:id,1000,:id)"
                ),
                {"id": uuid4()},
            )
            if mode == "populated":
                await insert(conn)
        async with engine.connect() as writer, engine.connect() as reader:
            if mode == "concurrent":
                await writer.begin()
                await insert(writer)
            await reader.begin()
            await reader.execute(text(f'SET LOCAL search_path TO "{schema}",public'))
            pid = await reader.scalar(text("SELECT pg_backend_pid()"))
            pending = asyncio.create_task(reader.run_sync(apply, "downgrade"))
            if mode == "concurrent":
                async with engine.connect() as observer:
                    for _ in range(100):
                        blocked = await observer.scalar(
                            text(
                                "SELECT wait_event_type='Lock' FROM pg_stat_activity WHERE pid=:id"
                            ),
                            {"id": pid},
                        )
                        await observer.commit()
                        if blocked:
                            break
                        await asyncio.sleep(0.02)
                    else:
                        pytest.fail("downgrade did not wait for actual in-flight storage INSERT")
                await writer.commit()
            if mode == "empty":
                await pending
                await reader.commit()
            else:
                with pytest.raises(RuntimeError, match="evidence prevents downgrade"):
                    await asyncio.wait_for(pending, 5)
                await reader.rollback()
        async with engine.begin() as conn:
            exists = await conn.scalar(
                text("SELECT to_regclass(:name)"), {"name": schema + ".generation_responses"}
            )
            if mode == "empty":
                assert exists is None
            else:
                assert exists is not None
                assert (
                    await conn.scalar(text(f'SELECT count(*) FROM "{schema}".generation_responses'))
                    == 1
                )
    finally:
        if pending is not None and not pending.done():
            pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)
        async with engine.begin() as conn:
            await conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        await engine.dispose()


@pytest.mark.parametrize("edge", ["chat", "collected", "native", "sse"])
@pytest.mark.parametrize("failure", [None, "capacity", "storage", "billing"])
async def test_actual_routes_retention_financial_chain_and_one_physical_post(
    pool, monkeypatch, edge, failure
):
    import json
    from dataclasses import replace

    import httpx
    from fastapi import FastAPI

    from yleum_gateway.core import db
    from yleum_gateway.core.config import reset_settings_cache
    from yleum_gateway.core.generation_response_auth import HEADER, mint_generation_response_token
    from yleum_gateway.routers import chat, messages_native
    from yleum_gateway.services import file_logger
    from yleum_gateway.services import (
        generation_attempts as attempts,
    )
    from yleum_gateway.services import (
        generation_response_capture as capture,
    )
    from yleum_gateway.services import generation_response_ledger as ledger
    from yleum_gateway.services.generation_identity import request_digest
    from yleum_gateway.services.generation_response_store import (
        ResponseIndeterminate,
        load_generation_response,
    )

    for module in (db, attempts, ledger):
        monkeypatch.setattr(module, "get_pool", lambda: pool)
    monkeypatch.setenv("GENERATION_PROVIDER_ATTEMPTS_ENABLED", "true")
    monkeypatch.setenv("GENERATION_RESPONSE_RETENTION_ENABLED", "true")
    monkeypatch.setenv("GENERATION_RESPONSE_SIGNING_SECRET", "synthetic-response-purpose-secret-32")
    monkeypatch.setenv("LLMGW_API_KEY", "synthetic-no-provider")
    reset_settings_cache()
    monkeypatch.setattr(file_logger, "log_request", lambda _: None)
    if failure == "billing":
        from unittest.mock import AsyncMock

        monkeypatch.setattr(
            chat.billing,
            "charge",
            AsyncMock(side_effect=RuntimeError("synthetic wallet commit unknown")),
        )
    if failure == "preconnect_unconfirmed":
        from unittest.mock import AsyncMock

        monkeypatch.setattr(messages_native, "_finish_provider_call", AsyncMock(return_value=False))
    route = "/v1/messages" if edge == "native" else "/v1/chat/completions"
    body = {
        "model": "claude-sonnet-5.5",
        "messages": [{"role": "user", "content": "synthetic"}],
        "stream": edge == "sse",
    }
    if edge == "chat":
        body["max_tokens"] = 32
    identity = await intent(pool)
    identity = replace(identity, request_sha256=request_digest(route, body))
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE generation_provider_attempts SET request_sha256=$2 WHERE attempt_id=$1",
            identity.attempt_id,
            identity.request_sha256,
        )
    body["user"] = str(identity.user_id)
    body["metadata"] = {
        "run_id": str(identity.run_id),
        "project_id": str(identity.project_id),
        "user_id": str(identity.user_id),
        "free": True,
        "generation_provider_identity": identity.to_wire(),
    }
    objects = Objects()
    original_put = objects.put

    async def put(key, data):
        if failure == "storage":
            raise OSError("synthetic lost storage")
        await original_put(key, data)

    objects.put = put

    async def preflight(bound):
        assert bound == identity
        if failure == "capacity":
            raise ResponseIndeterminate("synthetic missing capacity authority")
        return objects, await ledger.admit_response(
            bound,
            ledger.CapacitySnapshot(
                "route-" + uuid4().hex,
                10000000,
                10000000,
                uuid4(),
                uuid4(),
                expires_at=int(datetime.now(UTC).timestamp()) + 15,
            ),
        )

    monkeypatch.setattr(capture, "preflight_response_store", preflight)
    posts, observations = [], []
    loop = asyncio.get_running_loop()
    tool = {
        "id": "call_synthetic_exact",
        "type": "function",
        "function": {
            "name": "write_file",
            "arguments": '{"path":"src/example.ts","content":"synthetic retained tool bytes"}',
        },
    }

    async def committed():
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT a.phase,b.state,b.allocated_bytes FROM generation_provider_attempts a JOIN generation_responses b USING(attempt_id) WHERE a.attempt_id=$1",
                identity.attempt_id,
            )
            observations.append(tuple(row.values()))

    def result(request):
        payload = json.loads(request.content)
        posts.append(payload)
        if failure in {"preconnect", "preconnect_unconfirmed"}:
            raise httpx.ConnectError("synthetic before provider acceptance", request=request)
        response = {
            "id": "synthetic-" + str(identity.attempt_id),
            "model": "claude-sonnet-5.5",
            "choices": [
                {
                    "finish_reason": "tool_calls",
                    "message": {"role": "assistant", "content": "retained", "tool_calls": [tool]},
                }
            ],
            "usage": {"prompt_tokens": 2, "completion_tokens": 1, "total_tokens": 3},
        }
        headers = {"x-llmgw-request-id": response["id"], "x-llmgw-cost-rub": "0"}
        if payload.get("stream"):
            response["choices"] = [
                {
                    "delta": {
                        "content": "retained",
                        "tool_calls": [{"index": 0, **tool}],
                        "native_blocks": [
                            {"type": "image", "source": {"type": "base64", "data": "c3ludGhldGlj"}}
                        ],
                    },
                    "finish_reason": "tool_calls",
                }
            ]
            return httpx.Response(
                200,
                headers=headers,
                content="data: " + json.dumps(response) + "\n\ndata: [DONE]\n\n",
            )
        return httpx.Response(200, headers=headers, json=response)

    def reply(request):
        asyncio.run_coroutine_threadsafe(committed(), loop).result(timeout=3)
        return result(request)

    async def async_reply(request):
        await committed()
        return result(request)

    monkeypatch.setattr(httpx, "HTTPTransport", lambda **kw: httpx.MockTransport(reply))
    monkeypatch.setattr(httpx, "AsyncHTTPTransport", lambda **kw: httpx.MockTransport(async_reply))
    app = FastAPI()
    app.include_router(chat.router)
    app.include_router(messages_native.router)
    headers = {
        HEADER: mint_generation_response_token(identity, purpose="generation_response.record")
    }
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app)) as client:
        first = await client.post("http://gateway.test" + route, json=body, headers=headers)
        second = await client.post("http://gateway.test" + route, json=body, headers=headers)
    await capture.drain_captures()
    assert second.status_code in {409, 503}, second.text
    if failure == "capacity":
        assert posts == [] and first.status_code in {409, 503}
    else:
        assert len(posts) == 1 and observations[0][0:2] == ("dispatching", "uploading")
        assert observations[0][2] > 0
        if failure in {"storage", "billing", "preconnect_unconfirmed"}:
            assert first.status_code == 503 or (edge == "sse" and "[DONE]" not in first.text)
            with pytest.raises(ResponseIndeterminate):
                await load_generation_response(identity, objects=objects)
            if failure == "billing":
                assert b"retained" in b"".join(
                    value for key, value in objects.data.items() if "/chunks/" in key
                )
                async with pool.acquire() as conn:
                    body_row = await conn.fetchrow(
                        "SELECT state,reconciliation_hold FROM generation_responses WHERE attempt_id=$1",
                        identity.attempt_id,
                    )
                    assert tuple(body_row.values()) == ("complete", True)
                    await conn.execute(
                        "UPDATE generation_runs SET status='completed',finished_at=now()-interval '8 days' WHERE id=$1",
                        identity.run_id,
                    )
                    await conn.execute(
                        "UPDATE generation_responses SET reconciled_at=now()-interval '8 days' WHERE attempt_id=$1",
                        identity.attempt_id,
                    )
                from yleum_gateway.services import generation_response_cleanup as cleanup

                monkeypatch.setattr(cleanup, "get_pool", lambda: pool)
                monkeypatch.setattr(
                    cleanup,
                    "get_settings",
                    lambda: SimpleNamespace(
                        generation_response_cleanup_enabled=True,
                        generation_response_retention_enabled=True,
                        generation_response_retention_seconds=604800,
                    ),
                )
                assert (
                    await cleanup.delete_expired_generation_responses(
                        datetime.now(UTC), objects=objects
                    )
                    == 0
                )
                assert objects.data
        elif failure == "preconnect":
            assert first.status_code == 502, first.text
            saved = await load_generation_response(identity, objects=objects)
            assert json.loads(saved)["body"]["error"]["details"]["retry_safe"] is True
            async with pool.acquire() as conn:
                assert await conn.fetchval(
                    "SELECT reconciliation_hold FROM generation_responses WHERE attempt_id=$1",
                    identity.attempt_id,
                ) is False
        else:
            assert first.status_code == 200, first.text
            saved = await load_generation_response(identity, objects=objects)
            assert saved
            envelope = json.loads(saved.splitlines()[0])
            if edge == "sse":
                raw_event = json.loads(json.loads(saved.splitlines()[1]))
                assert raw_event["choices"][0]["delta"]["tool_calls"] == [{"index": 0, **tool}]
                assert (
                    raw_event["choices"][0]["delta"]["native_blocks"][0]["source"]["data"]
                    == "c3ludGhldGlj"
                )
            elif edge == "native":
                item = next(
                    block for block in envelope["body"]["content"] if block["type"] == "tool_use"
                )
                assert item["id"] == tool["id"] and item["name"] == tool["function"]["name"]
                assert item["input"] == json.loads(tool["function"]["arguments"])
            else:
                assert envelope["body"]["choices"][0]["message"]["tool_calls"] == [tool]
            if edge == "sse":
                assert "[DONE]" in first.text
        async with pool.acquire() as conn:
            assert (
                await conn.fetchval(
                    "SELECT count(*) FROM provider_calls WHERE run_id=$1", identity.run_id
                )
                == 1
            )
            assert (
                await conn.fetchval(
                    "SELECT balance_rub FROM wallets WHERE user_id=$1", identity.user_id
                )
                == 100
            )
    reset_settings_cache()


@pytest.mark.parametrize("confirmed", [True, False])
async def test_native_preconnect_releases_body_hold_only_after_committed_receipt(
    pool, monkeypatch, confirmed
):
    await test_actual_routes_retention_financial_chain_and_one_physical_post(
        pool, monkeypatch, "native", "preconnect" if confirmed else "preconnect_unconfirmed"
    )
