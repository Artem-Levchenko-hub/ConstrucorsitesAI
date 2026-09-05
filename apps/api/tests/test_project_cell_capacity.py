from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from omnia_api.models.generation_run import GenerationRun
from omnia_api.models.project import Project
from omnia_api.models.project_cell import (
    ProjectCellActivityLease,
    ProjectCellOperation,
    ProjectCellWorkspace,
)
from omnia_api.models.user import User
from omnia_api.services.orchestrator_client import (
    OrchestratorBadRequest,
    OrchestratorUnavailable,
    ProjectCellResourceResponse,
)
from omnia_api.services.project_cell_capacity import (
    _hibernate_victim_still_idle,
    claim_capacity_turn,
    claim_idle_hibernation_victim,
    claim_stale_generation_lease,
    hibernate_one_idle_workspace,
    release_one_stale_generation_lease,
    wait_for_capacity,
)
from omnia_api.services.project_cell_lifecycle import execute_cell_operation
from omnia_api.services.project_cells import (
    claim_cell_operation_committed,
    complete_cell_operation,
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

    assert [(turn.is_head, turn.position) for turn in turns] == [
        (True, 1),
        (False, 2),
        (False, 3),
    ]


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

    assert await release_one_stale_generation_lease(
        factory,
        requesting_run_id=requesting.id,
        client=SimpleNamespace(control=control),
    )
    assert len(calls) == 1
    assert calls[0].operation_id != failed.id
    assert calls[0].fencing_epoch == 3
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
    from omnia_api.services import project_cell_capacity

    monkeypatch.setattr(
        project_cell_capacity,
        "get_settings",
        lambda: SimpleNamespace(project_cell_capacity_wait_seconds=2),
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
        return response(request)

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
        await session.commit()

    async def no_idle_workspace(*_args: object, **_kwargs: object) -> bool:
        return False

    async def no_delay(_seconds: float) -> None:
        return None

    monkeypatch.setattr(
        "omnia_api.services.project_cell_capacity.hibernate_one_idle_workspace",
        no_idle_workspace,
    )
    monkeypatch.setattr(
        "omnia_api.services.project_cell_capacity.asyncio.sleep",
        no_delay,
    )

    requests: list[object] = []
    observations: list[object] = []

    async def ensure(request: object) -> ProjectCellResourceResponse:
        requests.append(request)
        assert len(requests) == 1, "unknown ensure must not be blindly replayed"
        raise OrchestratorUnavailable("orchestrator restarted during dispatch")

    async def observe(request: object) -> ProjectCellResourceResponse:
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
    assert len(requests) == len(observations) == 1
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
