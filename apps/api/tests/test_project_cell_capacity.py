from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from yleum_api.models.generation_run import GenerationRun
from yleum_api.models.project import Project
from yleum_api.models.project_cell import (
    ProjectCellActivityLease,
    ProjectCellOperation,
    ProjectCellWorkspace,
)
from yleum_api.models.user import User
from yleum_api.services.orchestrator_client import (
    EnsureProjectCellResourcesRequest,
    OrchestratorBadRequest,
    OrchestratorUnavailable,
    ProjectCellCapacityRejection,
    ProjectCellCapacityWait,
    ProjectCellLeaseObservation,
    ProjectCellResourceResponse,
)
from yleum_api.services.project_cell_capacity import (
    _hibernate_victim_still_idle,
    claim_capacity_turn,
    claim_idle_hibernation_victim,
    claim_stale_generation_lease,
    hibernate_one_idle_workspace,
    release_one_stale_generation_lease,
    wait_for_capacity,
)
from yleum_api.services.project_cell_lifecycle import execute_cell_operation
from yleum_api.services.project_cells import (
    claim_cell_operation_committed,
    complete_cell_operation,
    execution_run_id,
    fail_cell_operation,
    mark_cell_operation_indeterminate,
    park_cell_operation_for_capacity,
    reserve_cell_operation,
)

pytestmark = pytest.mark.asyncio


async def _project_run(
    session: AsyncSession,
    owner: User,
    *,
    created_at: datetime,
    label: str,
) -> tuple[Project, GenerationRun, ProjectCellWorkspace]:
    project = Project(
        owner_id=owner.id,
        name=label,
        slug=f"capacity-{label}-{uuid4().hex}",
        template="max_miniapp",
    )
    session.add(project)
    await session.flush()
    run = GenerationRun(
        project_id=project.id,
        user_id=owner.id,
        idempotency_key=f"capacity-{label}-{uuid4().hex}",
        prompt_hash=label * 8,
        status="queued_for_capacity",
        created_at=created_at,
    )
    session.add(run)
    await session.flush()
    workspace = ProjectCellWorkspace(
        project_id=project.id,
        owner_id=owner.id,
        provider="docker_owner_canary",
        state="ready",
        generation_run_id=None,
        ready_at=created_at,
    )
    session.add(workspace)
    await session.flush()
    return project, run, workspace


async def test_capacity_turn_is_fifo_by_created_at_then_id(db_session: AsyncSession) -> None:
    owner = User(email=f"capacity-{uuid4().hex}@example.test", password_hash="x")
    db_session.add(owner)
    await db_session.flush()
    now = datetime.now(UTC)
    _, first, _ = await _project_run(
        db_session, owner, created_at=now - timedelta(seconds=2), label="a"
    )
    _, second, _ = await _project_run(
        db_session, owner, created_at=now - timedelta(seconds=1), label="b"
    )
    _, third, _ = await _project_run(db_session, owner, created_at=now, label="c")

    turns = [await claim_capacity_turn(db_session, run.id) for run in (first, second, third)]

    # Порядок очереди прежний, но ход дают не только первому: на хосте бывает
    # место сразу на нескольких, и держать остальных — это то самое «по очереди».
    assert [(turn.may_start, turn.position) for turn in turns] == [
        (True, 1),
        (True, 2),
        (True, 3),
    ]


async def test_capacity_queue_and_hibernation_victims_are_per_orchestrator_host(
    db_session: AsyncSession,
) -> None:
    """Phase 3 / stage B: a run only queues behind runs on ITS host, and only a
    cell on the same host is worth pausing for it."""
    owner = User(email=f"hosts-{uuid4().hex}@example.test", password_hash="x")
    db_session.add(owner)
    await db_session.flush()
    now = datetime.now(UTC)
    _, on_core_first, cell_core_first = await _project_run(
        db_session, owner, created_at=now - timedelta(seconds=3), label="a"
    )
    _, on_commerce, cell_commerce = await _project_run(
        db_session, owner, created_at=now - timedelta(seconds=2), label="b"
    )
    _, on_core_second, _ = await _project_run(db_session, owner, created_at=now, label="c")
    cell_commerce.orchestrator = "commerce"
    await db_session.flush()

    turns = {
        run.id: await claim_capacity_turn(db_session, run.id)
        for run in (on_core_first, on_commerce, on_core_second)
    }
    assert (turns[on_core_first.id].may_start, turns[on_core_first.id].position) == (True, 1)
    # the commerce run is the head of its own queue although it was created later
    assert (turns[on_commerce.id].may_start, turns[on_commerce.id].position) == (True, 1)
    assert (turns[on_core_second.id].may_start, turns[on_core_second.id].position) == (True, 2)

    # a requester on core gets the idle core cell, never the commerce one
    victim = await claim_idle_hibernation_victim(db_session, requesting_run_id=on_core_second.id)
    assert victim is not None and victim.id == cell_core_first.id
    # on commerce the only cell belongs to the requester itself: nothing to pause
    assert await claim_idle_hibernation_victim(db_session, requesting_run_id=on_commerce.id) is None


async def test_hibernation_victim_excludes_active_generation_and_requester(
    db_session: AsyncSession,
) -> None:
    owner = User(email=f"victim-{uuid4().hex}@example.test", password_hash="x")
    db_session.add(owner)
    await db_session.flush()
    now = datetime.now(UTC)
    _, requesting, requesting_workspace = await _project_run(
        db_session, owner, created_at=now, label="requesting"
    )
    _, active_run, active_workspace = await _project_run(
        db_session, owner, created_at=now - timedelta(seconds=3), label="active"
    )
    active_run.status = "running"
    active_workspace.generation_run_id = active_run.id
    _, _, idle_workspace = await _project_run(
        db_session, owner, created_at=now - timedelta(seconds=5), label="idle"
    )

    victim = await claim_idle_hibernation_victim(
        db_session,
        requesting_run_id=requesting.id,
    )

    assert victim is not None
    assert victim.id == idle_workspace.id
    assert victim.id not in {requesting_workspace.id, active_workspace.id}


async def test_terminal_activity_reconciliation_clears_ghost_lease(
    test_engine: AsyncEngine,
) -> None:
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    now = datetime.now(UTC)
    async with factory() as session:
        owner = User(email=f"terminal-activity-{uuid4().hex}@example.test", password_hash="x")
        session.add(owner)
        await session.flush()
        _, requesting_run, _ = await _project_run(
            session, owner, created_at=now, label="terminal-requesting"
        )
        _, _, idle_workspace = await _project_run(
            session,
            owner,
            created_at=now - timedelta(seconds=5),
            label="terminal-idle",
        )
        pause, _ = await reserve_cell_operation(
            session,
            workspace_id=idle_workspace.id,
            generation_run_id=None,
            kind="pause",
            idempotency_key=f"terminal-activity:{requesting_run.id}",
            request={"checkpoint_ref": "terminal-activity"},
        )
        activity = ProjectCellActivityLease(
            workspace_id=idle_workspace.id,
            generation_run_id=None,
            kind="command",
            state="active",
            fencing_epoch=1,
            started_at=now - timedelta(minutes=1),
            deadline_at=now + timedelta(minutes=5),
            heartbeat_at=now - timedelta(seconds=15),
        )
        session.add(activity)
        await session.commit()
        activity_id = activity.operation_id

    async def operation_status(_workspace_id, _operation_id):
        return SimpleNamespace(
            state="completed",
            heartbeat_at=now,
            log_bytes=256,
        )

    client = SimpleNamespace(agent_operation_status=operation_status)
    assert await _hibernate_victim_still_idle(
        factory,
        workspace_id=idle_workspace.id,
        pause_operation_id=pause.id,
        client=client,
    )

    async with factory() as session:
        reconciled = await session.get(ProjectCellActivityLease, activity_id)
        assert reconciled is not None
        assert reconciled.state == "completed"
        assert reconciled.finished_at == now
        assert reconciled.log_bytes == 256


async def test_terminal_generation_lease_is_recoverable_but_active_is_not(
    db_session: AsyncSession,
) -> None:
    owner = User(email=f"stale-{uuid4().hex}@example.test", password_hash="x")
    db_session.add(owner)
    await db_session.flush()
    now = datetime.now(UTC)
    _, requesting, _ = await _project_run(
        db_session, owner, created_at=now, label="requesting-stale"
    )
    _, active_run, active_workspace = await _project_run(
        db_session, owner, created_at=now - timedelta(seconds=2), label="still-active"
    )
    active_run.status = "running"
    active_workspace.generation_run_id = active_run.id
    _, terminal_run, stale_workspace = await _project_run(
        db_session, owner, created_at=now - timedelta(seconds=3), label="terminal"
    )
    terminal_run.status = "completed"
    stale_workspace.generation_run_id = terminal_run.id
    await db_session.flush()

    claimed = await claim_stale_generation_lease(
        db_session,
        requesting_run_id=requesting.id,
    )

    assert claimed is not None
    assert claimed[0].id == stale_workspace.id
    assert claimed[1] == terminal_run.id


@pytest.mark.parametrize("api_state", ["provisioning", "failed"])
async def test_completed_ensure_makes_bootstrap_orphan_reclaimable(
    test_engine: AsyncEngine,
    api_state: str,
) -> None:
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    async with factory() as session:
        owner = User(email=f"orphan-{uuid4().hex}@example.test", password_hash="x")
        session.add(owner)
        await session.flush()
        now = datetime.now(UTC)
        _, requesting, _ = await _project_run(session, owner, created_at=now, label="request")
        _, terminal, workspace = await _project_run(
            session,
            owner,
            created_at=now - timedelta(minutes=1),
            label="orphan",
        )
        terminal.status = "failed"
        workspace.state = api_state
        workspace.generation_run_id = terminal.id
        await session.flush()
        assert await claim_stale_generation_lease(session, requesting_run_id=requesting.id) is None
        ensure, _ = await reserve_cell_operation(
            session,
            workspace_id=workspace.id,
            generation_run_id=terminal.id,
            kind="ensure",
            idempotency_key=f"orphan-ensure:{workspace.id}",
            request={"profile_version": "docker-owner-cell-resources-v1"},
        )
        await session.commit()
    await claim_cell_operation_committed(factory, ensure.id)
    async with factory() as session:
        await complete_cell_operation(session, ensure.id, {})
        failed, _ = await reserve_cell_operation(
            session,
            workspace_id=workspace.id,
            generation_run_id=terminal.id,
            kind="release",
            idempotency_key=f"capacity:release:{workspace.id}:{terminal.id}",
            request={},
        )
        await session.commit()
    await claim_cell_operation_committed(factory, failed.id)
    async with factory() as session:
        await fail_cell_operation(session, failed.id, "confirmed pre-effect profile rejection")
        await session.commit()

    calls = []

    async def control(request):
        calls.append(request)
        return ProjectCellResourceResponse(
            workspace_id=workspace.id,
            state="resources_ready",
            provider_ref="cell-orphan",
            fencing_epoch=request.fencing_epoch,
            checkpoint_ref=None,
            has_workspace=True,
            has_agent_home=True,
            has_postgres=True,
            has_redis=True,
        )

    observations = []

    async def observe(request):
        from dataclasses import replace

        observations.append(request)
        response = ProjectCellResourceResponse(
            workspace_id=workspace.id,
            state="resources_ready",
            provider_ref="cell-orphan",
            fencing_epoch=request.fencing_epoch,
            checkpoint_ref=None,
            has_workspace=True,
            has_agent_home=True,
            has_postgres=True,
            has_redis=True,
        )
        return replace(
            response,
            lease_observation=ProjectCellLeaseObservation(
                version=1,
                workspace_id=UUID(str(workspace.id)),
                project_id=UUID(str(workspace.project_id)),
                owner_id=UUID(str(owner.id)),
                operation_id=request.operation_id,
                request_digest=request.request_digest,
                fencing_epoch=request.fencing_epoch,
                active_generation_run_id=UUID(str(terminal.id)),
                active_generation_fencing_epoch=1,
                quiescent=False,
            ),
        )

    client = SimpleNamespace(control=control, observe_resources=observe)
    assert not await release_one_stale_generation_lease(
        factory,
        requesting_run_id=requesting.id,
        client=client,
    )
    assert len(observations) == 1 and not calls
    assert await release_one_stale_generation_lease(
        factory,
        requesting_run_id=requesting.id,
        client=client,
    )
    assert len(calls) == 1
    assert calls[0].operation_id != failed.id
    assert calls[0].fencing_epoch == 4
    async with factory() as session:
        recovered = await session.get(ProjectCellWorkspace, workspace.id)
        assert recovered.state == "ready"
        assert recovered.generation_run_id is None


@pytest.mark.parametrize("terminal_status", ["failed", "completed", "cancelled"])
async def test_capacity_wait_never_resurrects_terminal_generation(
    test_engine: AsyncEngine,
    terminal_status: str,
) -> None:
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    async with factory() as session:
        owner = User(email=f"terminal-queue-{uuid4().hex}@example.test", password_hash="x")
        session.add(owner)
        await session.flush()
        _, run, workspace = await _project_run(
            session,
            owner,
            created_at=datetime.now(UTC),
            label="terminal-queue",
        )
        run.status = terminal_status
        operation, _ = await reserve_cell_operation(
            session,
            workspace_id=workspace.id,
            generation_run_id=run.id,
            kind="ensure",
            idempotency_key=f"terminal-queue:{run.id}",
            request={"profile_version": "docker-owner-cell-resources-v1"},
        )
        await session.commit()

    async def forbidden(*_args):
        pytest.fail("terminal run must not dispatch or emit running progress")

    outcome = await wait_for_capacity(
        factory,
        run_id=run.id,
        operation_id=operation.id,
        client=SimpleNamespace(ensure=forbidden),
        emit=forbidden,
    )
    assert outcome.status == "cancelled"
    async with factory() as session:
        assert (await session.get(GenerationRun, run.id)).status == terminal_status


@pytest.mark.parametrize(
    ("parked_before_wait", "owned_refusal", "emit_crosses_due", "stop_on_emit"),
    [
        (False, True, False, False),
        (True, True, False, False),
        (True, False, False, False),
        (True, True, True, False),
        (True, True, False, True),
    ],
    ids=("fresh-refusal", "saved-refusal", "missing-owner", "emit-crosses-due", "stop-on-emit"),
)
async def test_capacity_wait_respects_exact_refusal_due_time(
    test_engine: AsyncEngine,
    parked_before_wait: bool,
    owned_refusal: bool,
    emit_crosses_due: bool,
    stop_on_emit: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from yleum_api.services import project_cell_capacity as capacity
    from yleum_api.services import project_cells
    from yleum_api.services.generation_runs import apply_cancelled_generation_locked

    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    current = datetime.now(UTC)

    class QueueClock(datetime):
        @classmethod
        def now(cls, tz=None):
            assert tz is UTC
            return current

    async def sleep(seconds: float) -> None:
        nonlocal current
        current += timedelta(seconds=seconds)

    monkeypatch.setattr(capacity, "datetime", QueueClock)
    monkeypatch.setattr(project_cells, "datetime", QueueClock)
    monkeypatch.setattr(capacity, "asyncio", SimpleNamespace(sleep=sleep, timeout=asyncio.timeout))
    async with factory() as session:
        owner = User(email=f"{uuid4().hex}@example.test", password_hash="x")
        session.add(owner)
        await session.flush()
        project, run, workspace = await _project_run(
            session, owner, created_at=current, label="due-refusal",
        )
        project.project_cell_enabled = True
        run.execution_backend = "worker"
        run.execution_started_at = run.created_at
        workspace.generation_run_id = run.id
        profile = "docker-owner-cell-resources-v1"
        operation, _ = await reserve_cell_operation(
            session,
            workspace_id=workspace.id,
            generation_run_id=run.id,
            kind="ensure",
            idempotency_key=f"generation:{run.id}:ensure:{profile}",
            request={"profile_version": profile},
        )
        await session.commit()

    requests: list[EnsureProjectCellResourcesRequest] = []
    sent_at: list[datetime] = []

    async def ensure(request: EnsureProjectCellResourcesRequest) -> ProjectCellResourceResponse:
        requests.append(request)
        sent_at.append(current)
        if len(requests) <= 2:
            raise ProjectCellCapacityWait(ProjectCellCapacityRejection(
                operation_id=request.operation_id,
                fencing_epoch=request.fencing_epoch,
                request_digest=request.request_digest,
                reason="insufficient_memory",
                retry_after_seconds=1,
                effect_applied=False,
            ))
        return ProjectCellResourceResponse(
            workspace_id=workspace.id, state="resources_ready", provider_ref="test-cell",
            fencing_epoch=request.fencing_epoch, checkpoint_ref=None, has_workspace=True,
            has_agent_home=True, has_postgres=True, has_redis=True,
        )

    async def emit(_payload: dict[str, object]) -> None:
        nonlocal current
        if emit_crosses_due and len(requests) == 1:
            current += timedelta(seconds=2)
        if stop_on_emit:
            async with factory() as session:
                saved = await session.get(GenerationRun, run.id, with_for_update=True)
                assert saved is not None
                await apply_cancelled_generation_locked(session, saved)
                await session.commit()

    reclaimed_before_attempt: list[int] = []

    async def reclaim(*_args, **_kwargs) -> bool:
        reclaimed_before_attempt.append(len(requests))
        return False

    monkeypatch.setattr(
        "yleum_api.services.project_cell_capacity.hibernate_one_idle_workspace", reclaim,
    )
    ownership = execution_run_id.set(run.id)
    client = SimpleNamespace(ensure=ensure)
    try:
        if parked_before_wait:
            parked = await execute_cell_operation(factory, operation.id, client)
            assert parked.status == "waiting_capacity"
            if not owned_refusal or stop_on_emit:
                async with factory() as session:
                    saved = await session.get(ProjectCellOperation, operation.id)
                    assert saved is not None
                    if not owned_refusal:
                        saved.execution_run_id = None
                    if stop_on_emit:
                        saved.next_attempt_at = current
                    await session.commit()
        outcome = await wait_for_capacity(
            factory,
            run_id=run.id,
            operation_id=operation.id,
            client=client,
            emit=emit,
            dispatch_token=uuid4(),
            initial_attempt=lambda: execute_cell_operation(factory, operation.id, client),
        )
    finally:
        execution_run_id.reset(ownership)

    if stop_on_emit:
        assert outcome.status == "cancelled" and outcome.operation_id == operation.id
        assert reclaimed_before_attempt == []
        assert len(requests) == 1
        return

    assert outcome.status == "completed" and outcome.operation_id == operation.id
    assert len(requests) == 3
    assert {request.operation_id for request in requests} == {operation.id}
    assert {request.request_digest for request in requests} == {requests[0].request_digest}
    assert [request.fencing_epoch for request in requests] == [1, 2, 3]
    assert sent_at[1] - sent_at[0] >= timedelta(seconds=1)
    assert sent_at[2] - sent_at[1] >= timedelta(seconds=1)
    assert reclaimed_before_attempt == ([1, 2] if owned_refusal else [2])


async def test_long_capacity_wait_renews_dispatch_and_observes_stop(
    test_engine: AsyncEngine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from yleum_api.services import generation_runs
    from yleum_api.services import project_cell_capacity as capacity

    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    started = current = datetime.now(UTC)
    retry_at = started + timedelta(seconds=75)
    dispatch_token = uuid4()

    class QueueClock(datetime):
        @classmethod
        def now(cls, tz=None):
            assert tz is UTC
            return current

    async with factory() as session:
        owner = User(email=f"{uuid4().hex}@example.test", password_hash="x")
        session.add(owner)
        await session.flush()
        _, run, workspace = await _project_run(
            session, owner, created_at=started, label="long-wait",
        )
        run.execution_backend = "worker"
        workspace.generation_run_id = run.id
        operation, _ = await reserve_cell_operation(
            session,
            workspace_id=workspace.id,
            generation_run_id=run.id,
            kind="ensure",
            idempotency_key=f"generation:{run.id}:ensure:long-wait",
            request={"profile_version": "docker-owner-cell-resources-v1"},
        )
        await session.commit()

    async def ensure(request: EnsureProjectCellResourcesRequest) -> ProjectCellResourceResponse:
        assert request.fencing_epoch == 1, "the not-yet-due ensure must not be dispatched"
        raise ProjectCellCapacityWait(ProjectCellCapacityRejection(
            operation_id=request.operation_id,
            fencing_epoch=request.fencing_epoch,
            request_digest=request.request_digest,
            reason="insufficient_memory",
            retry_after_seconds=10,
            effect_applied=False,
        ))

    sleep_calls: list[float] = []

    async def sleep(seconds: float) -> None:
        nonlocal current
        assert 0 < seconds <= 10
        sleep_calls.append(seconds)
        async with factory() as session:
            saved = await session.get(GenerationRun, run.id)
            parked = await session.get(ProjectCellOperation, operation.id)
            assert saved is not None and parked is not None
            assert generation_runs.read_capacity_dispatch_claim(saved) == (
                dispatch_token, current + timedelta(seconds=30),
            )
            assert parked.next_attempt_at == retry_at and parked.attempt_count == 1
            current += timedelta(seconds=seconds)
            if current >= started + timedelta(seconds=35):
                saved.status = "cancel_requested"
                await session.commit()

    async def forbidden_reclamation(*_args, **_kwargs) -> bool:
        pytest.fail("capacity must not be reclaimed before the retry is due")

    async def emit(_payload: dict[str, object]) -> None:
        pass

    monkeypatch.setattr(capacity, "datetime", QueueClock)
    monkeypatch.setattr(generation_runs, "datetime", QueueClock)
    monkeypatch.setattr(capacity, "asyncio", SimpleNamespace(sleep=sleep, timeout=asyncio.timeout))
    monkeypatch.setattr(capacity, "hibernate_one_idle_workspace", forbidden_reclamation)
    ownership = execution_run_id.set(run.id)
    client = SimpleNamespace(ensure=ensure)
    try:
        refused = await execute_cell_operation(factory, operation.id, client)
        assert refused.status == "waiting_capacity"
        async with factory() as session:
            parked = await session.get(ProjectCellOperation, operation.id)
            assert parked is not None
            parked.next_attempt_at = retry_at
            await session.commit()
        outcome = await wait_for_capacity(
            factory, run_id=run.id, operation_id=operation.id, client=client, emit=emit,
            dispatch_token=dispatch_token,
            initial_attempt=lambda: execute_cell_operation(factory, operation.id, client),
        )
    finally:
        execution_run_id.reset(ownership)

    assert sleep_calls == [10.0, 10.0, 10.0, 10.0]
    assert current < retry_at
    assert outcome.status == "cancelled" and outcome.operation_id == operation.id


async def test_queue_deadline_precedes_runtime_creation(test_engine: AsyncEngine) -> None:
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    async with factory() as session:
        owner = User(email=f"deadline-{uuid4().hex}@example.test", password_hash="x")
        session.add(owner)
        await session.flush()
        _, run, workspace = await _project_run(
            session,
            owner,
            created_at=datetime.now(UTC) - timedelta(hours=3),
            label="deadline",
        )
        operation, _ = await reserve_cell_operation(
            session,
            workspace_id=workspace.id,
            generation_run_id=run.id,
            kind="ensure",
            idempotency_key=f"deadline:{run.id}",
            request={"profile_version": "docker-owner-cell-resources-v1"},
        )
        await session.commit()

    async def forbidden(*_args):
        pytest.fail("expired queue must not dispatch")

    with pytest.raises(TimeoutError, match="capacity queue deadline exceeded"):
        await wait_for_capacity(
            factory,
            run_id=run.id,
            operation_id=operation.id,
            client=SimpleNamespace(ensure=forbidden),
            emit=forbidden,
        )
    async with factory() as session:
        assert (await session.get(ProjectCellOperation, operation.id)).status == "cancelled"


@pytest.mark.parametrize("initial_attempt", [False, True])
async def test_queue_deadline_interrupts_slow_provider_but_preserves_unknown_effect(
    test_engine: AsyncEngine,
    monkeypatch: pytest.MonkeyPatch,
    initial_attempt: bool,
) -> None:
    from yleum_api.services import project_cell_capacity

    monkeypatch.setattr(
        project_cell_capacity,
        "get_settings",
        lambda: SimpleNamespace(
            project_cell_capacity_wait_seconds=2, project_cell_parallel_admissions=4
        ),
    )
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    async with factory() as session:
        owner = User(email=f"slow-queue-{uuid4().hex}@example.test", password_hash="x")
        session.add(owner)
        await session.flush()
        _, run, workspace = await _project_run(
            session,
            owner,
            created_at=datetime.now(UTC),
            label="slow-queue",
        )
        operation, _ = await reserve_cell_operation(
            session,
            workspace_id=workspace.id,
            generation_run_id=run.id,
            kind="ensure",
            idempotency_key=f"slow-queue:{run.id}",
            request={"profile_version": "docker-owner-cell-resources-v1"},
        )
        await session.commit()
    calls = []

    async def ensure(request):
        calls.append(request)
        await asyncio.Event().wait()

    async def emit(_payload):
        return None

    with pytest.raises(TimeoutError, match="capacity queue deadline exceeded"):
        await wait_for_capacity(
            factory,
            run_id=run.id,
            operation_id=operation.id,
            client=SimpleNamespace(ensure=ensure),
            emit=emit,
            initial_attempt=(
                (
                    lambda: execute_cell_operation(
                        factory,
                        operation.id,
                        SimpleNamespace(ensure=ensure),
                    )
                )
                if initial_attempt
                else None
            ),
        )
    assert len(calls) == 1
    async with factory() as session:
        persisted = await session.get(ProjectCellOperation, operation.id)
        assert persisted.status == "indeterminate"
        assert persisted.fencing_epoch == calls[0].fencing_epoch


@pytest.mark.parametrize("same_project", [False, True])
async def test_unknown_ensure_reconciles_at_higher_fence_then_releases(
    test_engine: AsyncEngine,
    same_project: bool,
) -> None:
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    async with factory() as session:
        owner = User(email=f"reconcile-{uuid4().hex}@example.test", password_hash="x")
        session.add(owner)
        await session.flush()
        project, terminal, workspace = await _project_run(
            session,
            owner,
            created_at=datetime.now(UTC),
            label="unknown",
        )
        terminal.status = "failed"
        workspace.state = "provisioning"
        workspace.generation_run_id = terminal.id
        if same_project:
            requesting = GenerationRun(
                project_id=project.id,
                user_id=owner.id,
                idempotency_key=f"retry-{uuid4()}",
                prompt_hash="a" * 64,
                status="queued_for_capacity",
            )
            session.add(requesting)
            await session.flush()
        else:
            _, requesting, _ = await _project_run(
                session,
                owner,
                created_at=datetime.now(UTC),
                label="request",
            )
        ensure, _ = await reserve_cell_operation(
            session,
            workspace_id=workspace.id,
            generation_run_id=terminal.id,
            kind="ensure",
            idempotency_key=f"unknown:{terminal.id}",
            request={"profile_version": "docker-owner-cell-resources-v1"},
        )
        await session.commit()
    await claim_cell_operation_committed(factory, ensure.id)
    async with factory() as session:
        await mark_cell_operation_indeterminate(session, ensure.id, "cancelled_after_dispatch")
        await session.commit()
    calls = []

    def response(request, *, state="resources_ready"):
        return ProjectCellResourceResponse(
            workspace_id=workspace.id,
            state=state,
            provider_ref="reconciled-cell",
            fencing_epoch=request.fencing_epoch,
            checkpoint_ref=getattr(request, "checkpoint_ref", None),
            has_workspace=True,
            has_agent_home=True,
            has_postgres=True,
            has_redis=True,
        )

    async def observe(request):
        calls.append(("observe", request))
        if len(calls) == 1:
            raise OrchestratorUnavailable("observation response lost")
        from dataclasses import replace

        return replace(
            response(request),
            lease_observation=ProjectCellLeaseObservation(
                version=1,
                workspace_id=UUID(str(workspace.id)),
                project_id=UUID(str(workspace.project_id)),
                owner_id=UUID(str(workspace.owner_id)),
                operation_id=request.operation_id,
                request_digest=request.request_digest,
                fencing_epoch=request.fencing_epoch,
                active_generation_run_id=UUID(str(terminal.id)),
                active_generation_fencing_epoch=1,
                quiescent=False,
            ),
        )

    async def control(request):
        calls.append((request.kind, request))
        return response(
            request,
            state="resources_paused" if request.kind == "pause" else "resources_ready",
        )

    client = SimpleNamespace(observe_resources=observe, control=control)
    kwargs = {"workspace_id": workspace.id} if same_project else {}
    results = [
        await release_one_stale_generation_lease(
            factory,
            requesting_run_id=requesting.id,
            client=client,
            **kwargs,
        )
        for _ in range(3)
    ]
    assert results == [False, False, True]
    assert [kind for kind, _ in calls] == ["observe", "observe", "release"]
    assert [request.fencing_epoch for _, request in calls] == [2, 3, 4]
    async with factory() as session:
        recovered = await session.get(ProjectCellWorkspace, workspace.id)
        assert recovered.state == "ready"
        assert recovered.generation_run_id is None
        assert (await session.get(ProjectCellOperation, ensure.id)).status == "indeterminate"
        assert (await session.get(GenerationRun, terminal.id)).status == "failed"
    if not same_project:
        assert await hibernate_one_idle_workspace(
            factory,
            requesting_run_id=requesting.id,
            client=client,
        )
        async with factory() as session:
            assert (await session.get(ProjectCellWorkspace, workspace.id)).state == "stopped"


async def test_failed_reclamation_is_bounded_and_does_not_starve_other_cells(
    test_engine: AsyncEngine,
) -> None:
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    async with factory() as session:
        owner = User(email=f"bounded-{uuid4().hex}@example.test", password_hash="x")
        session.add(owner)
        await session.flush()
        now = datetime.now(UTC)
        _, requesting, _ = await _project_run(session, owner, created_at=now, label="request")
        _, failed_run, failed_cell = await _project_run(
            session,
            owner,
            created_at=now - timedelta(minutes=2),
            label="bad-cell",
        )
        _, other_run, other_cell = await _project_run(
            session,
            owner,
            created_at=now - timedelta(minutes=1),
            label="other-cell",
        )
        failed_run.status = other_run.status = "completed"
        failed_cell.generation_run_id = failed_run.id
        other_cell.generation_run_id = other_run.id
        await session.commit()

    calls = []

    async def control(request):
        calls.append(request)
        if request.workspace_id == failed_cell.id:
            raise OrchestratorBadRequest(
                "confirmed rejection",
                status_code=409,
                details={
                    "operation_id": str(request.operation_id),
                    "fencing_epoch": request.fencing_epoch,
                    "request_digest": request.request_digest,
                    "effect_applied": False,
                },
            )
        return ProjectCellResourceResponse(
            workspace_id=request.workspace_id,
            state="resources_ready",
            provider_ref="other-cell",
            fencing_epoch=request.fencing_epoch,
            checkpoint_ref=None,
            has_workspace=True,
            has_agent_home=True,
            has_postgres=True,
            has_redis=True,
        )

    results = [
        await release_one_stale_generation_lease(
            factory,
            requesting_run_id=requesting.id,
            client=SimpleNamespace(control=control),
        )
        for _ in range(5)
    ]
    assert results == [False, True, False, False, False]
    assert [request.workspace_id for request in calls] == [failed_cell.id, other_cell.id]
    assert len({request.operation_id for request in calls}) == 2


async def test_capacity_replays_indeterminate_release_before_hibernation(
    test_engine: AsyncEngine,
) -> None:
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    async with factory() as session:
        owner = User(email=f"release-replay-{uuid4().hex}@example.test", password_hash="x")
        session.add(owner)
        await session.flush()
        now = datetime.now(UTC)
        _, requesting_run, _ = await _project_run(
            session,
            owner,
            created_at=now,
            label="release-requesting",
        )
        _, stale_run, stale_workspace = await _project_run(
            session,
            owner,
            created_at=now - timedelta(seconds=3),
            label="release-stale",
        )
        stale_run.status = "failed"
        stale_workspace.generation_run_id = stale_run.id
        release, _ = await reserve_cell_operation(
            session,
            workspace_id=stale_workspace.id,
            generation_run_id=stale_run.id,
            kind="release",
            idempotency_key=f"generation:{stale_run.id}:release:test",
            request={},
        )
        await session.commit()

    unavailable_client = type(
        "UnavailableClient",
        (),
        {"control": lambda *_args, **_kwargs: None},
    )()

    async def unavailable_control(*_args: object, **_kwargs: object) -> object:
        raise OrchestratorUnavailable("response lost after dispatch")

    unavailable_client.control = unavailable_control
    first_outcome = await execute_cell_operation(factory, release.id, unavailable_client)
    assert first_outcome.status == "indeterminate"
    assert first_outcome.fencing_epoch == 1

    async with factory() as session:
        failed_retry, _ = await reserve_cell_operation(
            session,
            workspace_id=stale_workspace.id,
            generation_run_id=stale_run.id,
            kind="release",
            idempotency_key=f"capacity:release:{stale_workspace.id}:{stale_run.id}",
            request={},
        )
        await session.commit()
    await claim_cell_operation_committed(factory, failed_retry.id)
    async with factory() as session:
        await fail_cell_operation(session, failed_retry.id, "confirmed pre-effect rejection")
        await session.commit()

    replayed_requests: list[object] = []

    async def replay_control(request: object) -> ProjectCellResourceResponse:
        replayed_requests.append(request)
        return ProjectCellResourceResponse(
            workspace_id=stale_workspace.id,
            state="resources_ready",
            provider_ref="cell-1",
            fencing_epoch=1,
            checkpoint_ref=None,
            has_workspace=True,
            has_agent_home=True,
            has_postgres=True,
            has_redis=True,
        )

    replay_client = type("ReplayClient", (), {})()
    replay_client.control = replay_control

    released = await release_one_stale_generation_lease(
        factory,
        requesting_run_id=requesting_run.id,
        client=replay_client,
    )

    assert released is True
    assert len(replayed_requests) == 1
    replayed_request = replayed_requests[0]
    assert replayed_request.operation_id == release.id
    assert replayed_request.fencing_epoch == 1
    async with factory() as session:
        refreshed_workspace = await session.get(ProjectCellWorkspace, stale_workspace.id)
        refreshed_release = await session.get(ProjectCellOperation, release.id)
        assert refreshed_workspace is not None
        assert refreshed_workspace.generation_run_id is None
        assert refreshed_release is not None
        assert refreshed_release.status == "completed"


async def test_capacity_wait_reconciles_ensure_after_orchestrator_restart(
    test_engine: AsyncEngine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    async with factory() as session:
        owner = User(email=f"ensure-restart-{uuid4().hex}@example.test", password_hash="x")
        session.add(owner)
        await session.flush()
        _project, run, workspace = await _project_run(
            session,
            owner,
            created_at=datetime.now(UTC),
            label="ensure-restart",
        )
        workspace.state = "provisioning"
        workspace.generation_run_id = run.id
        operation, _ = await reserve_cell_operation(
            session,
            workspace_id=workspace.id,
            generation_run_id=run.id,
            kind="ensure",
            idempotency_key=f"generation:{run.id}:ensure:restart-test",
            request={"profile_version": "docker-owner-cell-resources-v1"},
        )
        await session.commit()

    await claim_cell_operation_committed(factory, operation.id)
    async with factory() as session:
        await park_cell_operation_for_capacity(
            session,
            operation.id,
            reason="insufficient_memory",
            retry_after_seconds=1,
        )
        parked = await session.get(ProjectCellOperation, operation.id)
        assert parked is not None
        parked.next_attempt_at = datetime.now(UTC)
        await session.commit()

    async def no_idle_workspace(*_args: object, **_kwargs: object) -> bool:
        return False

    async def no_delay(_seconds: float) -> None:
        return None

    monkeypatch.setattr(
        "yleum_api.services.project_cell_capacity.hibernate_one_idle_workspace",
        no_idle_workspace,
    )
    monkeypatch.setattr(
        "yleum_api.services.project_cell_capacity.asyncio.sleep",
        no_delay,
    )

    requests: list[object] = []
    observations: list[object] = []

    async def ensure(request: object) -> ProjectCellResourceResponse:
        requests.append(request)
        if len(requests) == 1:
            raise OrchestratorUnavailable("orchestrator restarted during dispatch")
        assert len(requests) == 2
        assert request.operation_id != operation.id, "unknown original ensure must not be replayed"
        assert request.fencing_epoch > observations[0].fencing_epoch
        return await observe(request)

    async def observe(request: object) -> ProjectCellResourceResponse:
        if request.operation_id != requests[-1].operation_id:
            observations.append(request)
        return ProjectCellResourceResponse(
            workspace_id=workspace.id,
            state="resources_ready",
            provider_ref=f"cell-{workspace.id}",
            fencing_epoch=request.fencing_epoch,
            checkpoint_ref=None,
            has_workspace=True,
            has_agent_home=True,
            has_postgres=True,
            has_redis=True,
        )

    client = type("RestartingClient", (), {})()
    client.ensure = ensure
    client.observe_resources = observe

    outcome = await wait_for_capacity(
        factory,
        run_id=run.id,
        operation_id=operation.id,
        client=client,
        emit=no_idle_workspace,
    )

    assert outcome.status == "completed"
    assert len(requests) == 2 and len(observations) == 1
    assert outcome.kind == "ensure"
    assert requests[0].operation_id == operation.id
    assert observations[0].operation_id != operation.id
    assert observations[0].fencing_epoch > requests[0].fencing_epoch
    async with factory() as session:
        persisted = await session.get(ProjectCellOperation, operation.id)
        assert persisted is not None
        assert persisted.status == "indeterminate"
        assert persisted.attempt_count == 2


async def test_capacity_retries_pause_after_confirmed_terminal_failure(
    test_engine: AsyncEngine,
) -> None:
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    async with factory() as session:
        owner = User(email=f"pause-retry-{uuid4().hex}@example.test", password_hash="x")
        session.add(owner)
        await session.flush()
        now = datetime.now(UTC)
        _, requesting_run, _ = await _project_run(
            session,
            owner,
            created_at=now,
            label="pause-requesting",
        )
        _, _, idle_workspace = await _project_run(
            session,
            owner,
            created_at=now - timedelta(seconds=5),
            label="pause-idle",
        )
        checkpoint_ref = f"capacity-{requesting_run.id.hex[:12]}"
        failed_pause, _ = await reserve_cell_operation(
            session,
            workspace_id=idle_workspace.id,
            generation_run_id=None,
            kind="pause",
            idempotency_key=f"capacity:{requesting_run.id}:pause:{idle_workspace.id}",
            request={"checkpoint_ref": checkpoint_ref},
        )
        await session.commit()
    await claim_cell_operation_committed(factory, failed_pause.id)
    async with factory() as session:
        await fail_cell_operation(session, failed_pause.id, "checkpoint source too large")
        await session.commit()

    replayed_requests: list[object] = []

    async def pause_control(request: object) -> ProjectCellResourceResponse:
        replayed_requests.append(request)
        return ProjectCellResourceResponse(
            workspace_id=idle_workspace.id,
            state="resources_paused",
            provider_ref="cell-1",
            fencing_epoch=2,
            checkpoint_ref=checkpoint_ref,
            has_workspace=True,
            has_agent_home=True,
            has_postgres=True,
            has_redis=True,
        )

    client = type("PauseClient", (), {})()
    client.control = pause_control

    paused = await hibernate_one_idle_workspace(
        factory,
        requesting_run_id=requesting_run.id,
        client=client,
    )

    assert paused is True
    assert len(replayed_requests) == 1
    retry_request = replayed_requests[0]
    assert retry_request.operation_id != failed_pause.id
    assert retry_request.fencing_epoch == 2
    async with factory() as session:
        workspace = await session.get(ProjectCellWorkspace, idle_workspace.id)
        retry = await session.get(ProjectCellOperation, retry_request.operation_id)
        assert workspace is not None
        assert workspace.state == "stopped"
        assert retry is not None
        assert retry.idempotency_key.endswith(":2")
        assert retry.status == "completed"


async def test_wake_capacity_wait_is_recognised_like_ensure(monkeypatch) -> None:
    """Пробуждение обязано понимать «ожидание ёмкости» так же, как создание.

    Иначе получается несовместимость между половинами: оркестратор отвечает 429
    «подожди», а клиент считает это отказом и роняет операцию. Именно так и было
    до правки — только с той стороны отказ приходил как 500, и владелец видел
    вечное «разворачивается».
    """
    from uuid import uuid4

    from yleum_api.services import orchestrator_client as oc

    request = oc.ControlProjectCellResourcesRequest(
        workspace_id=uuid4(),
        kind="wake",
        checkpoint_ref=None,
        operation_id=uuid4(),
        fencing_epoch=9,
        request_digest="e" * 64,
    )
    details = {
        "operation_id": str(request.operation_id),
        "fencing_epoch": request.fencing_epoch,
        "request_digest": request.request_digest,
        "effect_applied": False,
        "reason": "insufficient_cpu",
        "retry_after_seconds": 2,
    }

    async def refusing(*args, **kwargs):
        raise oc.OrchestratorBadRequest(
            "capacity", status_code=429, upstream_code="capacity_wait", details=details
        )

    monkeypatch.setattr(oc, "_request", refusing)

    with pytest.raises(oc.ProjectCellCapacityWait) as caught:
        await oc.HttpProjectCellOrchestratorClient().control(request)

    assert caught.value.rejection.reason == "insufficient_cpu"
    assert caught.value.rejection.retry_after_seconds == 2


async def test_wake_rejects_a_mismatched_capacity_answer(monkeypatch) -> None:
    """Ответ про чужую операцию не принимается за свой."""
    from uuid import uuid4

    from yleum_api.services import orchestrator_client as oc

    request = oc.ControlProjectCellResourcesRequest(
        workspace_id=uuid4(),
        kind="wake",
        checkpoint_ref=None,
        operation_id=uuid4(),
        fencing_epoch=9,
        request_digest="e" * 64,
    )
    details = {
        "operation_id": str(uuid4()),
        "fencing_epoch": 9,
        "request_digest": "e" * 64,
        "effect_applied": False,
        "reason": "insufficient_cpu",
        "retry_after_seconds": 2,
    }

    async def refusing(*args, **kwargs):
        raise oc.OrchestratorBadRequest(
            "capacity", status_code=429, upstream_code="capacity_wait", details=details
        )

    monkeypatch.setattr(oc, "_request", refusing)

    with pytest.raises(oc.OrchestratorUnavailable):
        await oc.HttpProjectCellOrchestratorClient().control(request)


async def test_the_queue_admits_several_projects_at_once_but_still_bounds_the_herd(
    db_session: AsyncSession,
) -> None:
    """Владелец 26.09: «множество пользователей должны запускать генерацию
    одновременно, а не по очереди».

    Очередь по-прежнему упорядочена и по-прежнему ограничена — иначе на хост
    разом ломились бы все и получали отказы. Но голова теперь шире одного:
    сколько именно, решает настройка, а окончательное «да» всё равно за
    оркестратором, который знает настоящую занятость хоста.
    """
    owner = User(email=f"parallel-{uuid4().hex}@example.test", password_hash="x")
    db_session.add(owner)
    await db_session.flush()
    now = datetime.now(UTC)
    runs = []
    for index in range(6):
        _, run, _ = await _project_run(
            db_session,
            owner,
            created_at=now - timedelta(seconds=10 - index),
            label=f"p{index}",
        )
        runs.append(run)

    turns = [await claim_capacity_turn(db_session, run.id) for run in runs]

    assert [turn.position for turn in turns] == [1, 2, 3, 4, 5, 6]
    assert [turn.may_start for turn in turns] == [True, True, True, True, False, False]


async def test_a_run_that_is_not_queued_never_gets_a_turn(db_session: AsyncSession) -> None:
    """Нулевая позиция значит «в очереди его нет» и не должна читаться как ход.

    Проверка узкая намеренно: расширение головы делается сравнением позиции с
    пределом, и без этой границы «нет в очереди» стало бы «можно начинать».
    """
    owner = User(email=f"unqueued-{uuid4().hex}@example.test", password_hash="x")
    db_session.add(owner)
    await db_session.flush()
    _, run, _ = await _project_run(db_session, owner, created_at=datetime.now(UTC), label="z")
    run.status = "running"
    await db_session.flush()

    turn = await claim_capacity_turn(db_session, run.id)

    assert (turn.position, turn.may_start) == (0, False)


def test_the_waiting_line_names_the_place_in_the_queue() -> None:
    """Пять минут одинаковой строки «Ожидаю ресурсы сервера» читаются как поломка.

    Владелец 26.09 прислал скриншот именно с этим. Номер в очереди — то немногое,
    что превращает ожидание в понятное: видно, что очередь движется. Первому
    номер не показываем: он уже занимает место.
    """
    from yleum_api.services.project_cell_capacity import _WAITING_DETAIL, _waiting_detail

    assert _waiting_detail(1) == _WAITING_DETAIL
    assert _waiting_detail(0) == _WAITING_DETAIL
    assert _waiting_detail(4).startswith("Вы 4-й в очереди.")
    assert _WAITING_DETAIL in _waiting_detail(4)
