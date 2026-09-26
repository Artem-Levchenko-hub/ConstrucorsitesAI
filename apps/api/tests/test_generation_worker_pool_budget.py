"""Dispatcher regression tests: no database, Redis or model calls."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from yleum_api.core import db
from yleum_api.services import readiness
from yleum_api.workers import generation


def test_configured_limit_cannot_consume_every_database_connection(monkeypatch):
    monkeypatch.setattr(
        generation, "get_settings", lambda: SimpleNamespace(generation_worker_max_concurrent=64)
    )
    assert generation.current_dispatch_limit() == 8
    assert 2 * generation.current_dispatch_limit() <= db.DATABASE_POOL_CAPACITY - 4


def test_budget_matches_the_pool_that_is_actually_created(monkeypatch):
    options = {}

    def create(_url, **kwargs):
        options.update(kwargs)
        return object()

    monkeypatch.setattr(db, "_engine", None)
    monkeypatch.setattr(db, "_session_factory", None)
    monkeypatch.setattr(db, "create_async_engine", create)
    monkeypatch.setattr(db, "async_sessionmaker", lambda *_a, **_kw: object())
    monkeypatch.setattr(db, "get_settings", lambda: SimpleNamespace(database_url="unused"))
    db.get_engine()
    assert options["pool_size"] + options["max_overflow"] == db.DATABASE_POOL_CAPACITY == 20


def test_heartbeat_distinguishes_capacity_waiting_from_running():
    raw = generation.dispatch_heartbeat_payload(
        active=8, limit=8, configured_limit=64, running=2, waiting_capacity=5, other=1
    )
    payload = json.loads(raw)
    assert payload["configured_limit"] == 64
    assert payload["limit"] == 8
    assert readiness.parse_worker_load(raw) == "8/8"
    assert readiness.parse_worker_dispatch_details(raw) == {
        "generation_worker_running": "2",
        "generation_worker_waiting_capacity": "5",
        "generation_worker_other": "1",
        "generation_worker_configured_limit": "64",
        "generation_worker_effective_limit": "8",
    }


@pytest.mark.parametrize("raw", [None, b"invalid", b"{}", b"[]", b"\xff"])
def test_old_or_malformed_heartbeat_details_are_unknown(raw):
    assert set(readiness.parse_worker_dispatch_details(raw).values()) == {"unknown"}


@pytest.mark.asyncio
@pytest.mark.parametrize("configured, effective", [(2, 2), (64, 8)])
async def test_dispatch_loop_overlaps_runs_reuses_slots_and_reports_waiters(
    monkeypatch, configured, effective
):
    """Drive the actual scan loop; both first tasks must enter before either exits."""
    ids = [uuid4() for _ in range(effective + 1)]
    statuses = dict.fromkeys(ids, "pending")
    statuses.update({ids[0]: "running", ids[1]: "queued_for_capacity"})
    entered = {run_id: asyncio.Event() for run_id in ids}
    release = {run_id: asyncio.Event() for run_id in ids}
    calls = []
    payloads = []
    tasks = []
    tick = asyncio.Queue()
    heartbeat = asyncio.Queue()
    real_sleep = asyncio.sleep
    pool = asyncio.BoundedSemaphore(db.DATABASE_POOL_CAPACITY)

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            pass

        async def scalars(self, _query):
            return SimpleNamespace(all=lambda: [i for i in ids if statuses[i] != "completed"])

        async def execute(self, _query):
            return SimpleNamespace(all=lambda: list(statuses.items()))

    async def execute(run_id):
        tasks.append(asyncio.current_task())
        calls.append(run_id)
        # Hold BOTH ownership and work connections, rather than assuming work
        # returns its checkout before another generation starts.
        async with pool, pool:
            entered[run_id].set()
            await release[run_id].wait()
            statuses[run_id] = "completed"
        return True

    async def redis_set(_key, value, **_kwargs):
        payloads.append(json.loads(value))
        await heartbeat.put(None)

    async def scan_sleep(_seconds):
        await tick.get()

    monkeypatch.setattr(generation, "get_engine", lambda: object())
    monkeypatch.setattr(generation, "async_sessionmaker", lambda *_a, **_kw: Session)
    monkeypatch.setattr(generation, "get_redis", lambda: SimpleNamespace(set=redis_set))
    monkeypatch.setattr(generation, "execute_dispatch", execute)
    monkeypatch.setattr(
        generation, "asyncio", SimpleNamespace(create_task=asyncio.create_task, sleep=scan_sleep)
    )
    monkeypatch.setattr(
        generation,
        "get_settings",
        lambda: SimpleNamespace(
            generation_worker_max_concurrent=configured, omnia_release_sha="test"
        ),
    )
    loop = asyncio.create_task(generation._run_dispatch_forever())
    try:
        async with asyncio.timeout(3):
            await heartbeat.get()
            await asyncio.gather(*(entered[i].wait() for i in ids[:effective]))
            assert not entered[ids[-1]].is_set()
            # A control query still completes while every allowed run holds
            # both connections. An unsafe limit hangs here and times out.
            async with pool, pool, pool, pool:
                pass
            await tick.put(None)
            await heartbeat.get()
            assert calls == ids[:effective]
            assert payloads[-1]["running"] == 1
            assert payloads[-1]["waiting_capacity"] == 1
            assert payloads[-1]["other"] == effective - 2
            assert payloads[-1]["configured_limit"] == configured
            assert payloads[-1]["limit"] == effective
            release[ids[0]].set()
            await real_sleep(0)
            await tick.put(None)
            await heartbeat.get()
            await entered[ids[-1]].wait()
            assert calls == ids
    finally:
        loop.cancel()
        for task in tasks:
            task.cancel()
        await asyncio.gather(loop, *tasks, return_exceptions=True)


@pytest.mark.asyncio
async def test_public_health_exposes_effective_limit_and_capacity_waiting(monkeypatch):
    monkeypatch.setattr(
        readiness, "get_settings", lambda: SimpleNamespace(use_generation_worker=True)
    )
    monkeypatch.setattr(readiness, "_database_ok", AsyncMock(return_value=True))
    monkeypatch.setattr(readiness, "_redis_and_worker", AsyncMock(return_value=(True, True, "abc")))
    monkeypatch.setattr(
        readiness, "_deploy_control_plane_ok", AsyncMock(return_value=(True, "abc"))
    )
    monkeypatch.setattr(readiness, "_preview_storage_ok", AsyncMock(return_value=True))
    raw = generation.dispatch_heartbeat_payload(
        active=8, limit=8, configured_limit=64, running=2, waiting_capacity=5, other=1
    )
    monkeypatch.setattr(
        readiness, "get_redis", lambda: SimpleNamespace(get=AsyncMock(return_value=raw))
    )
    report = await readiness.probe_readiness()
    assert report.checks["generation_worker"] == "ok"
    assert report.dependencies["generation_worker_load"] == "8/8"
    assert report.dependencies["generation_worker_configured_limit"] == "64"
    assert report.dependencies["generation_worker_effective_limit"] == "8"
    assert report.dependencies["generation_worker_running"] == "2"
    assert report.dependencies["generation_worker_waiting_capacity"] == "5"
