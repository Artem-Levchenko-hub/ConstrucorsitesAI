"""Admission must not race an already dispatched, terminal run's reconciliation.

Pure session/clock doubles: no database, provider or real-time sleeps are used.
"""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest

from yleum_api.models.generation_run import GenerationRun
from yleum_api.models.project import Project
from yleum_api.models.project_cell import ProjectCellWorkspace
from yleum_api.models.user import User
from yleum_api.services import project_cell_executor as executor

pytestmark = pytest.mark.asyncio


class OwnEnsureReached(Exception):
    pass


class Harness:
    def __init__(self, monkeypatch):
        self.now = datetime(2026, 10, 26, tzinfo=UTC)
        self.user = SimpleNamespace(id=uuid4())
        self.project = SimpleNamespace(id=uuid4(), owner_id=self.user.id, project_cell_enabled=True)
        self.previous = SimpleNamespace(id=uuid4(), status="failed")
        self.run = SimpleNamespace(
            id=uuid4(), project_id=self.project.id, user_id=self.user.id,
            status="pending", agent_state={}, created_at=self.now,
        )
        self.workspace = SimpleNamespace(
            id=uuid4(), project_id=self.project.id, owner_id=self.user.id,
            generation_run_id=self.previous.id,
        )
        self.running = True
        self.active_transactions = set()
        self.queries = []
        self.sleeps = []
        self.emissions = []
        self.reclaims = []
        self.on_sleep = lambda: setattr(self, "running", False)
        self.release_succeeds = True
        self.on_reclaim = lambda: None
        harness = self

        class Clock(datetime):
            @classmethod
            def now(cls, tz=None):
                return harness.now

        monkeypatch.setattr(executor, "datetime", Clock)
        monkeypatch.setattr(executor, "monotonic", lambda: self.now.timestamp())
        monkeypatch.setattr(executor, "get_settings", lambda: SimpleNamespace(
            use_cell_resource_profile_v2=False, project_cell_capacity_wait_seconds=30,
        ))
        monkeypatch.setattr(executor, "get_engine", lambda: None)
        monkeypatch.setattr(executor, "async_sessionmaker", lambda *a, **kw: self.factory)
        monkeypatch.setattr(executor.asyncio, "sleep", self.sleep)

    def factory(self):
        harness = self

        class Session:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                harness.active_transactions.discard(self)

            async def get(self, model, key, **kwargs):
                harness.active_transactions.add(self)
                return {
                    Project: harness.project, User: harness.user,
                    ProjectCellWorkspace: harness.workspace,
                    GenerationRun: harness.run if key == harness.run.id else harness.previous,
                }[model]

            async def scalar(self, query):
                harness.active_transactions.add(self)
                sql = str(query)
                harness.queries.append(query)
                if "FROM generation_runs" in sql:
                    return harness.run
                if "FROM project_cell_workspaces" in sql:
                    return harness.workspace.id
                if "FROM project_cell_operations" in sql:
                    values = query.compile().params.values()
                    if "running" in values:
                        return uuid4() if harness.running else None
                    return uuid4() if harness.workspace.generation_run_id else None
                raise AssertionError(sql)

            async def execute(self, query):
                result = await self.scalar(query)
                return SimpleNamespace(scalar_one_or_none=lambda: result)

            async def refresh(self, obj):
                harness.active_transactions.add(self)

            async def commit(self):
                harness.active_transactions.discard(self)

        return Session()

    async def sleep(self, seconds):
        assert not self.active_transactions, "SQL transaction held across polling wait"
        assert self.run.status == "pending"
        self.sleeps.append(seconds)
        self.now += timedelta(seconds=seconds)
        self.on_sleep()

    async def emit(self, payload):
        assert not self.active_transactions
        self.emissions.append(payload)

    async def wait(self):
        await executor._wait_for_previous_cell_reconciliation(
            self.factory, project_id=self.project.id, user_id=self.user.id,
            run_id=self.run.id, workspace_id=self.workspace.id,
            previous_run_id=self.previous.id, emit=self.emit,
        )

    async def admission(self, monkeypatch):
        async def lock(*args):
            return None

        async def control(*args, **kwargs):
            return SimpleNamespace(selected=True, ready=True)

        async def workspace(*args, **kwargs):
            return self.workspace, False

        async def release(*args, **kwargs):
            assert not self.running, "reclaimer called before prior RUNNING operation settled"
            self.reclaims.append(kwargs)
            succeeded = self.release_succeeds
            self.on_reclaim()
            if succeeded:
                self.workspace.generation_run_id = None
            return succeeded

        async def profile(*args):
            return "profile"

        async def reserve(*args, **kwargs):
            assert self.workspace.generation_run_id == self.run.id
            raise OwnEnsureReached

        monkeypatch.setattr(executor, "lock_workspace_operations", lock)
        monkeypatch.setattr(executor, "inspect_project_cell_control", control)
        monkeypatch.setattr(executor, "get_or_create_workspace", workspace)
        monkeypatch.setattr(executor, "release_one_stale_generation_lease", release)
        monkeypatch.setattr(executor, "resolve_workspace_profile", profile)
        monkeypatch.setattr(executor, "reserve_cell_operation", reserve)
        await executor.maybe_create_project_cell_executor(
            project_id=self.project.id, project_slug="qa", project_template="max_miniapp",
            user_id=self.user.id, generation_run_id=self.run.id,
            docs_media_execute=None, agent_emit=self.emit,
        )


async def test_admission_waits_for_running_old_operation_before_reclaim(monkeypatch):
    h = Harness(monkeypatch)
    with pytest.raises(OwnEnsureReached):
        await h.admission(monkeypatch)
    assert h.sleeps == [1]
    assert len(h.reclaims) == 1
    assert h.run.status == "pending"
    assert h.run.agent_state == {}


async def test_wait_uses_only_exact_old_running_ensure_or_reconcile(monkeypatch):
    h = Harness(monkeypatch)
    await h.wait()
    queries = [q for q in h.queries if "FROM project_cell_operations" in str(q)]
    assert len(queries) == 2
    for query in queries:
        params = query.compile().params
        assert h.previous.id in params.values()
        assert h.workspace.id in params.values()
        assert "running" in params.values()
        assert ["ensure", "reconcile"] in params.values()
        assert "FOR UPDATE" not in str(query)
    assert h.emissions[0]["status"] == "running"


@pytest.mark.parametrize("change", [
    "cancel", "terminal", "owner", "run_owner", "project", "binding", "workspace_owner",
    "workspace_project",
])
async def test_wait_fails_closed_on_changed_admission(monkeypatch, change):
    h = Harness(monkeypatch)

    def change_state():
        target, field, value = {
            "cancel": (h.run, "status", "cancel_requested"),
            "terminal": (h.run, "status", "failed"),
            "owner": (h.project, "owner_id", uuid4()),
            "run_owner": (h.run, "user_id", uuid4()),
            "project": (h.run, "project_id", uuid4()),
            "binding": (h.workspace, "generation_run_id", uuid4()),
            "workspace_owner": (h.workspace, "owner_id", uuid4()),
            "workspace_project": (h.workspace, "project_id", uuid4()),
        }[change]
        setattr(target, field, value)

    h.on_sleep = change_state
    with pytest.raises((executor.ProjectCellOwnershipError, executor.ProjectCellStateConflict)):
        await h.wait()
    assert h.workspace.generation_run_id != h.run.id
    assert h.running is True


async def test_wait_honors_original_capacity_deadline_without_reset(monkeypatch):
    h = Harness(monkeypatch)
    h.run.created_at -= timedelta(seconds=29)
    original = h.run.created_at
    h.on_sleep = lambda: None
    with pytest.raises(TimeoutError, match="deadline"):
        await h.wait()
    assert h.sleeps == [1]
    assert h.run.created_at == original
    assert h.run.status == "pending"
    assert h.running is True


async def test_wait_cancellation_does_not_mutate_old_operation(monkeypatch):
    h = Harness(monkeypatch)

    def cancel():
        raise asyncio.CancelledError

    h.on_sleep = cancel
    with pytest.raises(asyncio.CancelledError):
        await h.wait()
    assert h.running is True
    assert h.workspace.generation_run_id == h.previous.id
    assert not h.active_transactions


async def test_expired_original_deadline_cannot_start_reclaim_after_old_operation_settles(
    monkeypatch,
):
    h = Harness(monkeypatch)
    h.running = False
    h.now += timedelta(seconds=30)
    with pytest.raises(TimeoutError, match="deadline"):
        await h.admission(monkeypatch)
    assert h.reclaims == []
    assert h.sleeps == []
    assert h.workspace.generation_run_id == h.previous.id


async def test_no_running_operation_does_not_wait_or_change_reclaim_budget(monkeypatch):
    h = Harness(monkeypatch)
    h.running = False
    h.release_succeeds = False
    with pytest.raises(executor.ProjectCellExecutorUnavailable, match="needs reconciliation"):
        await h.admission(monkeypatch)
    assert h.sleeps == []
    assert len(h.reclaims) == 4
    assert [r["reclaim_for_repair"] for r in h.reclaims] == [True, True, True, False]


async def test_heartbeat_is_truthful_and_periodic_without_sql_locks(monkeypatch):
    h = Harness(monkeypatch)
    h.on_sleep = lambda: setattr(h, "running", len(h.sleeps) < 16)
    await h.wait()
    assert len(h.sleeps) == 16
    assert len(h.emissions) == 2
    assert h.run.status == "pending"


async def test_each_existing_reclaim_attempt_checks_newly_running_dependency(monkeypatch):
    h = Harness(monkeypatch)
    h.running = False
    h.release_succeeds = False

    def start_dependency():
        h.running = True
        h.release_succeeds = True

    h.on_reclaim = start_dependency
    with pytest.raises(OwnEnsureReached):
        await h.admission(monkeypatch)
    assert h.sleeps == [1]
    assert len(h.reclaims) == 2


async def test_pending_wait_does_not_require_or_create_capacity_claim(monkeypatch):
    from yleum_api.services.generation import supervisor

    h = Harness(monkeypatch)
    monkeypatch.setattr(supervisor, "get_engine", lambda: None)
    monkeypatch.setattr(supervisor, "async_sessionmaker", lambda *a, **kw: h.factory)
    result = await supervisor._renew_capacity_dispatch_claim(h.run.id, uuid4())
    assert result == "prequeue"
    assert h.run.status == "pending"
    assert h.run.agent_state == {}
    assert not h.active_transactions
