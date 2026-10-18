"""The long-lived worker, not an API background response, owns deletion."""

import asyncio
from types import SimpleNamespace
from uuid import uuid4

import pytest

from yleum_api.workers import run


def test_worker_startup_registers_project_deletion_executor(monkeypatch):
    started = []

    class Thread:
        def __init__(self, *, target, name, daemon):
            assert daemon is True
            self.target, self.name = target, name

        def start(self):
            started.append((self.name, self.target))

    monkeypatch.setattr(run.threading, "Thread", Thread)
    monkeypatch.setattr(
        run, "get_settings", lambda: SimpleNamespace(billing_lifecycle_enabled=False),
    )
    run._start_workers()
    assert ("project-deletion", run._run_project_deletion) in started
    assert sum(name == "project-deletion" for name, _ in started) == 1


def test_deletion_worker_waits_for_current_reader_readiness(monkeypatch):
    from yleum_api.services.generation.recovery_readers import wait_for_readers

    received = []

    async def executor(*, before_tick):
        received.append(before_tick)

    monkeypatch.setattr(run, "run_project_deletion_forever", executor)
    run._run_project_deletion()
    assert received == [wait_for_readers]


async def test_poll_loop_rotates_past_old_pending_failures(monkeypatch):
    from sqlalchemy.ext import asyncio as sqlalchemy_asyncio

    from yleum_api.services import project_deletion

    projects = [uuid4(), uuid4(), uuid4()]
    dispatched = []
    ticks = 0

    class Engine:
        disposed = False

        async def dispose(self):
            self.disposed = True

    engine = Engine()

    async def pending(_engine, *, exclude=()):
        return [project_id for project_id in projects if project_id not in exclude]

    async def finish(_engine, project_id):
        dispatched.append(project_id)
        return "pending"

    async def before_tick():
        nonlocal ticks
        ticks += 1
        if ticks == 4:
            raise asyncio.CancelledError

    monkeypatch.setattr(sqlalchemy_asyncio, "create_async_engine", lambda *_a, **_k: engine)
    monkeypatch.setattr(project_deletion, "pending_project_deletions", pending)
    monkeypatch.setattr(project_deletion, "finish_pending_project_deletion", finish)
    monkeypatch.setattr(project_deletion, "_POLL_SECONDS", 0)
    with pytest.raises(asyncio.CancelledError):
        await project_deletion.run_project_deletion_forever(before_tick=before_tick)
    assert dispatched[:3] == projects
    assert engine.disposed is True


async def test_ambiguous_lock_acquisition_discards_the_database_session():
    from yleum_api.services.project_deletion import project_deletion_claim

    class Connection:
        invalidated = False

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            pass

        async def scalar(self, *_args):
            raise TimeoutError("lock reply lost after dispatch")

        async def invalidate(self):
            self.invalidated = True

    connection = Connection()
    engine = SimpleNamespace(connect=lambda: connection)
    with pytest.raises(TimeoutError):
        async with project_deletion_claim(engine, uuid4()):
            raise AssertionError("No executor may run after an unknown claim")
    assert connection.invalidated is True
