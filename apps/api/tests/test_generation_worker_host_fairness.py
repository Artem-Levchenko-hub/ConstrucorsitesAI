"""Host admission fairness without Redis, PostgreSQL or model calls."""

from __future__ import annotations

import asyncio
import sqlite3
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy.dialects import postgresql, sqlite

from yleum_api.models.generation_run import GenerationRun
from yleum_api.models.project import Project
from yleum_api.models.project_cell import ProjectCellWorkspace
from yleum_api.models.user import User
from yleum_api.services.generation_runs import capacity_admitted_dispatch_token
from yleum_api.workers import generation


def candidate(host, *, admitted=False, status="pending", maintenance=False):
    return generation.HostDispatch(uuid4(), host, status, admitted, maintenance)


def test_waiting_host_does_not_take_every_dispatch_slot():
    a = [candidate("A") for _ in range(110)]
    b = candidate("B")
    selected = generation.select_host_runs_to_start([*a, b], {}, {}, 8)
    assert [item.run_id for item in selected] == [a[0].run_id, b.run_id]


def test_batch_reserves_before_a_task_can_become_queued():
    a = [candidate("A") for _ in range(8)]
    selected = generation.select_host_runs_to_start(a, {}, {}, 8)
    assert selected == a[:1]
    active = {a[0].run_id: object()}
    assert generation.select_host_runs_to_start(a, active, {a[0].run_id: a[0]}, 8) == []


def test_admitted_runs_release_only_host_quota_and_allow_eight_on_one_host():
    active = {}
    info = {}
    for _ in range(8):
        row = candidate("A")
        assert generation.select_host_runs_to_start([row], active, info, 8) == [row]
        active[row.run_id] = object()
        info[row.run_id] = generation.HostDispatch(row.run_id, "A", "running", True)
    assert generation.select_host_runs_to_start([candidate("B")], active, info, 8) == []


def test_unknown_host_rebind_and_round_robin():
    unknown = candidate(None)
    other = candidate(None)
    a, b = candidate("A"), candidate("B")
    assert generation.select_host_runs_to_start([unknown, other], {}, {}, 8) == [unknown]
    active = {unknown.run_id: object()}
    info = {unknown.run_id: generation.HostDispatch(unknown.run_id, "A")}
    assert generation.select_host_runs_to_start([other, a, b], active, info, 8) == [other, b]
    assert generation.select_host_runs_to_start([a, b], {}, {}, 1, after_host="h:A") == [b]


def test_maintenance_has_one_reserved_place_but_keeps_global_budget():
    waiting = candidate("A")
    active = {waiting.run_id: object()}
    info = {waiting.run_id: waiting}
    cleanup = [candidate("A", maintenance=True) for _ in range(3)]
    b = candidate("B")
    assert generation.select_host_runs_to_start([b], active, info, 8, maintenance=cleanup) == [
        cleanup[0],
        b,
    ]
    assert generation.select_host_runs_to_start([b], active, info, 1, maintenance=cleanup) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("healthy_single_host", [False, True])
async def test_actual_loop_blocked_host_does_not_hide_free_host(monkeypatch, healthy_single_host):
    a = [candidate("A") for _ in range(110)]
    b = candidate("B")
    rows = a[:9] if healthy_single_host else [*a, b]
    snapshots = {r.run_id: r for r in rows}
    entered = []
    tasks = []
    tick, heartbeat = asyncio.Queue(), asyncio.Queue()
    release = asyncio.Event()

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            pass

    async def scan(_session, active):
        return ({run_id: snapshots[run_id] for run_id in active}, rows, [])

    async def execute(run_id):
        tasks.append(asyncio.current_task())
        entered.append(run_id)
        await release.wait()
        return True

    async def redis_set(*_args, **_kwargs):
        await heartbeat.put(None)

    async def scan_sleep(_seconds):
        await tick.get()

    monkeypatch.setattr(generation, "get_engine", lambda: object())
    monkeypatch.setattr(generation, "async_sessionmaker", lambda *_a, **_kw: Session)
    monkeypatch.setattr(generation, "_load_dispatch_scan", scan, raising=False)
    monkeypatch.setattr(generation, "execute_dispatch", execute)
    monkeypatch.setattr(generation, "get_redis", lambda: SimpleNamespace(set=redis_set))
    monkeypatch.setattr(
        generation, "asyncio", SimpleNamespace(create_task=asyncio.create_task, sleep=scan_sleep)
    )
    loop = asyncio.create_task(generation._run_dispatch_forever())
    try:
        async with asyncio.timeout(3):
            await heartbeat.get()
            await asyncio.sleep(0)
            if healthy_single_host:
                for count in range(1, 9):
                    assert entered == [row.run_id for row in a[:count]]
                    snapshots[a[count - 1].run_id] = replace(
                        a[count - 1], status="running", admitted=True
                    )
                    await tick.put(None)
                    await heartbeat.get()
                    await asyncio.sleep(0)
                assert len(entered) == 8  # The ninth still needs global connection room.
                return
            assert entered == [a[0].run_id, b.run_id]
            await tick.put(None)
            await heartbeat.get()
            assert entered == [a[0].run_id, b.run_id]
            snapshots[a[0].run_id] = replace(a[0], status="running", admitted=True)
            await tick.put(None)
            await heartbeat.get()
            await asyncio.sleep(0)
            assert entered == [a[0].run_id, b.run_id, a[1].run_id]
    finally:
        loop.cancel()
        for task in tasks:
            task.cancel()
        await asyncio.gather(loop, *tasks, return_exceptions=True)


def test_partitioned_sql_window_returns_b_after_more_than_100_older_a_requests():
    # Execute the actual relational window against SQLite, not a mocked list.
    # The production query uses SQL common to PostgreSQL and SQLite.
    with sqlite3.connect(":memory:") as connection:
        connection.execute(
            "CREATE TABLE generation_runs (id text, project_id text, "
            "execution_backend text, status text, execution_started_at text, "
            "created_at integer)"
        )
        connection.execute(
            "CREATE TABLE project_cell_workspaces (project_id text UNIQUE, orchestrator text)"
        )
        ids = [uuid4() for _ in range(112)]
        for index, run_id in enumerate(ids):
            connection.execute(
                "INSERT INTO generation_runs VALUES (?,?, 'worker','pending',NULL,?)",
                (run_id.hex, run_id.hex, index),
            )
            connection.execute(
                "INSERT INTO project_cell_workspaces VALUES (?,?)",
                (run_id.hex, "A" if index < 110 else "B"),
            )
        query = generation._unstarted_candidates_query([]).compile(
            dialect=sqlite.dialect(), compile_kwargs={"literal_binds": True}
        )
        rows = connection.execute(str(query)).fetchall()
        assert rows == [(ids[0].hex, "A"), (ids[110].hex, "B")]
        query = generation._unstarted_candidates_query([ids[0]]).compile(
            dialect=sqlite.dialect(), compile_kwargs={"literal_binds": True}
        )
        assert connection.execute(str(query)).fetchall() == [(ids[1].hex, "A"), (ids[110].hex, "B")]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status, token, admitted",
    [
        ("running", str(uuid4()), True),
        ("running", None, False),
        ("running", "bad-token", False),
        ("running", 42, False),
        ("running", {}, False),
        ("running", [str(uuid4())], False),
        ("running", uuid4().hex, True),
        ("queued_for_capacity", str(uuid4()), False),
        ("pending", str(uuid4()), False),
        ("completed", str(uuid4()), False),
    ],
)
async def test_only_running_with_valid_controller_admission_releases_host(status, token, admitted):
    run_id = uuid4()
    run = SimpleNamespace(
        id=run_id, status=status, agent_state={"capacity_admitted_dispatch_token": token}
    )

    class Session:
        calls = 0

        async def execute(self, _query):
            self.calls += 1
            if self.calls == 1:
                # Only the raw admission token is selected from JSON, not full
                # response/agent payloads on every two-second heartbeat scan.
                sql = str(_query.compile(dialect=postgresql.dialect()))
                assert len(_query.selected_columns) == 4
                assert "response_payload" not in sql and "generation_runs.error" not in sql
                assert "generation_runs.agent_state[" in sql
            return SimpleNamespace(
                all=lambda: [(run_id, status, token, "A")] if self.calls == 1 else []
            )

    info, _, _ = await generation._load_dispatch_scan(Session(), [run_id])
    assert info[run_id].admitted is admitted
    assert admitted == (status == "running" and capacity_admitted_dispatch_token(run) is not None)
    next_a = candidate("A")
    assert generation.select_host_runs_to_start([next_a], {run_id: object()}, info, 8) == (
        [next_a] if admitted else []
    )


def test_sql_routes_cancel_started_orphan_and_terminal_cleanup_outside_host_queue():
    with sqlite3.connect(":memory:") as connection:
        connection.execute(
            "CREATE TABLE generation_runs (id text, project_id text, "
            "execution_backend text, status text, execution_started_at text, "
            "created_at integer)"
        )
        connection.execute(
            "CREATE TABLE project_cell_workspaces (project_id text UNIQUE, orchestrator text)"
        )
        connection.execute(
            "CREATE TABLE project_cell_operations (execution_run_id text, status text)"
        )
        ids = [uuid4() for _ in range(8)]
        cases = [
            ("pending", None, None),
            ("queued_for_capacity", "started", None),
            ("running", "started", None),
            ("cancel_requested", None, None),
            ("completed", None, "running"),
            ("failed", "started", None),
            ("completed", None, "completed"),
            ("cancel_requested", None, None),
        ]
        for index, (status, started, operation) in enumerate(cases):
            run_id = ids[index].hex
            connection.execute(
                "INSERT INTO generation_runs VALUES (?,?,?,?,?,?)",
                (run_id, run_id, "worker" if index < 7 else "in_process", status, started, index),
            )
            connection.execute("INSERT INTO project_cell_workspaces VALUES (?, 'A')", (run_id,))
            if operation:
                connection.execute(
                    "INSERT INTO project_cell_operations VALUES (?,?)", (run_id, operation)
                )

        def execute(query):
            return connection.execute(
                str(query.compile(dialect=sqlite.dialect(), compile_kwargs={"literal_binds": True}))
            ).fetchall()

        assert execute(generation._unstarted_candidates_query([])) == [(ids[0].hex, "A")]
        assert execute(generation._maintenance_candidates_query([])) == [
            (run_id.hex, "A") for run_id in ids[1:5]
        ]
        assert execute(generation._maintenance_candidates_query(ids[1:4])) == [(ids[4].hex, "A")]


def test_cleanup_keeps_global_slot_but_does_not_reserve_host_provisional_slot():
    cleanup = candidate("A", maintenance=True)
    next_a = candidate("A")
    assert generation.select_host_runs_to_start(
        [next_a], {cleanup.run_id: object()}, {cleanup.run_id: cleanup}, 2
    ) == [next_a]
    assert (
        generation.select_host_runs_to_start(
            [next_a], {cleanup.run_id: object()}, {cleanup.run_id: cleanup}, 1
        )
        == []
    )


@pytest.mark.asyncio
async def test_postgres_scan_keeps_host_b_visible_and_reads_real_admission(db_session):
    owner = User(id=uuid4(), email=f"host-fairness-{uuid4()}@example.test")
    db_session.add(owner)
    await db_session.flush()
    projects = [
        Project(
            id=uuid4(), owner_id=owner.id, name="Fairness",
            slug=f"fair-{uuid4().hex}", template="max_miniapp",
        )
        for _ in range(111)
    ]
    db_session.add_all(projects)
    await db_session.flush()
    now = datetime.now(UTC)
    runs = []
    for index, project in enumerate(projects):
        run = GenerationRun(
            id=uuid4(),
            project_id=project.id,
            user_id=owner.id,
            idempotency_key=str(uuid4()),
            prompt_hash="f" * 64,
            execution_backend="worker",
            status="pending",
            created_at=now + timedelta(seconds=index),
        )
        runs.append(run)
        db_session.add(run)
        db_session.add(
            ProjectCellWorkspace(
                project_id=project.id,
                owner_id=owner.id,
                provider="docker_owner_canary",
                state="ready",
                orchestrator="A" if index < 110 else "B",
            )
        )
    await db_session.flush()
    _, normal, cleanup = await generation._load_dispatch_scan(db_session, [])
    assert [(item.run_id, item.host) for item in normal] == [(runs[0].id, "A"), (runs[-1].id, "B")]
    assert cleanup == []
    runs[0].status = "running"
    runs[0].execution_started_at = now
    runs[0].agent_state = {"capacity_admitted_dispatch_token": str(uuid4())}
    await db_session.flush()
    info, normal, cleanup = await generation._load_dispatch_scan(db_session, [runs[0].id])
    assert info[runs[0].id].admitted is True
    assert [(item.run_id, item.host) for item in normal] == [(runs[1].id, "A"), (runs[-1].id, "B")]
    assert cleanup == []
    # Restarted scanner has no local owner: only the existing orphan path,
    # never the queue for fresh model execution.
    _, normal, cleanup = await generation._load_dispatch_scan(db_session, [])
    assert runs[0].id not in [item.run_id for item in normal]
    assert [item.run_id for item in cleanup] == [runs[0].id]


def test_run_moving_between_scan_queries_is_dispatched_only_once():
    row = candidate("A")
    cleanup = replace(row, maintenance=True)
    assert generation.select_host_runs_to_start([row], {}, {}, 8, maintenance=[cleanup]) == [
        cleanup
    ]


def test_seven_running_plus_one_waiter_still_fills_global_budget():
    running = [candidate("A", status="running", admitted=True) for _ in range(7)]
    waiting = candidate("A", status="queued_for_capacity")
    rows = [*running, waiting]
    assert (
        generation.select_host_runs_to_start(
            [candidate("B")],
            {row.run_id: object() for row in rows},
            {row.run_id: row for row in rows},
            8,
        )
        == []
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("limit", [1, 8])
async def test_actual_loop_alternates_lock_busy_cleanup_and_fresh_with_one_slot(monkeypatch, limit):
    bootstrap = [candidate(f"bootstrap-{index}") for index in range(limit - 1)]
    cleanup = candidate("A", maintenance=True)
    fresh = candidate("B")
    attempted = []
    tick, heartbeat = asyncio.Queue(), asyncio.Queue()
    hold = asyncio.Event()
    tasks = []
    scans = 0

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            pass

    async def scan(_session, active):
        nonlocal scans
        scans += 1
        info = {
            row.run_id: replace(row, status="running", admitted=True)
            for row in bootstrap
            if row.run_id in active
        }
        rows = bootstrap if bootstrap and scans == 1 else [fresh]
        return info, rows, [cleanup]

    async def execute(run_id):
        tasks.append(asyncio.current_task())
        if run_id in {row.run_id for row in bootstrap}:
            await hold.wait()
        else:
            attempted.append(run_id)
        return False  # E.g. advisory lock is still owned by another worker.

    async def redis_set(*_args, **_kwargs):
        await heartbeat.put(None)

    async def scan_sleep(_seconds):
        await tick.get()

    monkeypatch.setattr(generation, "get_engine", lambda: object())
    monkeypatch.setattr(generation, "async_sessionmaker", lambda *_a, **_kw: Session)
    monkeypatch.setattr(generation, "_load_dispatch_scan", scan)
    monkeypatch.setattr(generation, "execute_dispatch", execute)
    monkeypatch.setattr(generation, "current_dispatch_limit", lambda: limit)
    monkeypatch.setattr(generation, "get_redis", lambda: SimpleNamespace(set=redis_set))
    monkeypatch.setattr(
        generation, "asyncio", SimpleNamespace(create_task=asyncio.create_task, sleep=scan_sleep)
    )
    loop = asyncio.create_task(generation._run_dispatch_forever())
    try:
        async with asyncio.timeout(3):
            for _ in range(5):
                await heartbeat.get()
                await asyncio.sleep(0)
                await tick.put(None)
            assert attempted == [
                cleanup.run_id,
                fresh.run_id,
                cleanup.run_id,
                fresh.run_id,
                cleanup.run_id,
            ]
    finally:
        loop.cancel()
        for task in tasks:
            task.cancel()
        await asyncio.gather(loop, *tasks, return_exceptions=True)
