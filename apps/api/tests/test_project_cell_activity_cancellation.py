from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.test_project_cell_activity import _new_project, _new_run, _new_user, _new_workspace
from yleum_api.models.project_cell import ProjectCellActivityLease
from yleum_api.services.orchestrator_client import (
    OrchestratorUnavailable,
    ProjectCellAgentOperationStatus,
)
from yleum_api.services.project_cell_activity import (
    ActivityKind,
    ActivityStart,
    activity_blocks_hibernation,
    run_with_activity_lease,
)

pytestmark = pytest.mark.asyncio


async def _setup(db_session):
    owner = await _new_user(db_session, "cancel-finalpoll")
    project = await _new_project(db_session, owner)
    run = await _new_run(db_session, owner, project)
    workspace = await _new_workspace(db_session, owner, project, run)
    await db_session.commit()
    return ActivityStart(
        uuid4(),
        workspace.id,
        run.id,
        ActivityKind.COMMAND,
        7,
        datetime.now(UTC) + timedelta(minutes=5),
        "a" * 64,
    )


def _status(operation_id: UUID, state: str = "completed"):
    now = datetime.now(UTC)
    if state == "unknown":
        return SimpleNamespace(
            operation_id=operation_id,
            state=state,
            heartbeat_at=now,
            terminal_response=None,
            phase="full_build",
            log_bytes=12,
        )
    return ProjectCellAgentOperationStatus.from_json(
        {
            "operation_id": str(operation_id),
            "state": state,
            "phase": "full_build",
            "started_at": now.isoformat(),
            "deadline_at": (now + timedelta(minutes=5)).isoformat(),
            "heartbeat_at": now.isoformat(),
            "log_bytes": 12,
            "terminal_response": None,
        }
    )


async def test_transient_final_poll_preserves_cancellation_and_commits_before_event(
    db_session,
    test_engine,
):
    lease = await _setup(db_session)
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    entered = asyncio.Event()
    calls = []
    events = []

    async def work():
        calls.append("work")
        entered.set()
        await asyncio.Event().wait()

    async def poll(operation_id):
        calls.append("poll")
        if calls.count("poll") == 1:
            raise OrchestratorUnavailable("synthetic transient final GET")
        return _status(operation_id)

    async def emit(name, payload):
        if name == "tool.finished":
            async with factory() as session:
                row = await session.get(ProjectCellActivityLease, lease.operation_id)
                assert row.state == "cancelled" and row.finished_at is not None
        events.append(name)

    task = asyncio.create_task(
        run_with_activity_lease(
            session_factory=factory,
            lease=lease,
            work=work,
            poll_status=poll,
            emit=emit,
        )
    )
    await asyncio.wait_for(entered.wait(), 5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    row = await db_session.get(ProjectCellActivityLease, lease.operation_id)
    assert row.state == "cancelled" and row.finished_at is not None
    assert calls == ["work", "poll", "poll"]
    assert events == ["tool.started", "tool.finished"]
    assert not await activity_blocks_hibernation(db_session, workspace_id=lease.workspace_id)


@pytest.mark.parametrize(
    "outcome", ["running", "unknown", "wrong_operation", "wrong_terminal", "unavailable"]
)
async def test_uncertain_remote_cancellation_keeps_durable_writer_guard(
    db_session,
    test_engine,
    outcome,
):
    lease = await _setup(db_session)
    entered = asyncio.Event()
    calls = []
    events = []

    async def work():
        calls.append("work")
        entered.set()
        await asyncio.Event().wait()

    async def poll(operation_id):
        calls.append("poll")
        if outcome == "unavailable":
            raise OrchestratorUnavailable("synthetic unknown GET")
        status = _status(
            uuid4() if outcome == "wrong_operation" else operation_id,
            "completed" if outcome in {"wrong_operation", "wrong_terminal"} else outcome,
        )
        if outcome == "wrong_terminal":
            status = replace(status, terminal_response=SimpleNamespace(operation_id=uuid4()))
        return status

    async def emit(name, payload):
        events.append(name)

    task = asyncio.create_task(
        run_with_activity_lease(
            session_factory=async_sessionmaker(test_engine, expire_on_commit=False),
            lease=lease,
            work=work,
            poll_status=poll,
            emit=emit,
        )
    )
    await asyncio.wait_for(entered.wait(), 5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 6)
    row = await db_session.get(ProjectCellActivityLease, lease.operation_id)
    assert row.state == "active" and row.finished_at is None
    assert await activity_blocks_hibernation(db_session, workspace_id=lease.workspace_id)
    assert calls.count("work") == 1 and 1 <= calls.count("poll") <= 3
    assert events == ["tool.started"]


@pytest.mark.parametrize(
    "boundary", ["event", "event_cancel", "poll_cancel", "second_cancel", "hung_get", "commit"]
)
async def test_final_cleanup_is_drained_and_cancellation_survives_boundary_failure(
    db_session,
    test_engine,
    monkeypatch,
    boundary,
):
    from yleum_api.services import project_cell_activity

    lease = await _setup(db_session)
    entered = asyncio.Event()
    polling = asyncio.Event()
    release_poll = asyncio.Event()
    local_drained = asyncio.Event()
    calls = []
    monkeypatch.setattr(project_cell_activity, "_FINAL_POLL_SECONDS", 0.12)

    async def work():
        calls.append("work")
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            local_drained.set()

    async def poll(operation_id):
        calls.append("poll")
        if boundary == "poll_cancel":
            raise asyncio.CancelledError("journal reader cancelled")
        assert local_drained.is_set()
        polling.set()
        if boundary in {"second_cancel", "hung_get"}:
            await release_poll.wait()
        return _status(operation_id)

    async def emit(name, payload):
        if name == "tool.finished" and boundary == "event":
            raise OSError("synthetic event outage")
        if name == "tool.finished" and boundary == "event_cancel":
            raise asyncio.CancelledError("event storage cancelled")

    commits = 0

    class FailingCommitSession(AsyncSession):
        async def commit(self):
            nonlocal commits
            commits += 1
            if boundary == "commit" and commits == 2:
                from sqlalchemy import text

                # PostgreSQL aborts the settlement transaction after its flush.
                await self.execute(text("SELECT 1 / 0"))
            await super().commit()

    task = asyncio.create_task(
        run_with_activity_lease(
            session_factory=async_sessionmaker(
                test_engine,
                class_=FailingCommitSession,
                expire_on_commit=False,
            ),
            lease=lease,
            work=work,
            poll_status=poll,
            emit=emit,
        )
    )
    await asyncio.wait_for(entered.wait(), 5)
    start = asyncio.get_running_loop().time()
    task.cancel("owner stop")
    if boundary != "poll_cancel":
        await asyncio.wait_for(polling.wait(), 5)
    if boundary == "second_cancel":
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
        release_poll.set()
    with pytest.raises(asyncio.CancelledError) as cancelled:
        await asyncio.wait_for(task, 2)
    assert cancelled.value.args == ("owner stop",)
    assert calls.count("work") == 1 and calls.count("poll") == 1
    row = await db_session.get(ProjectCellActivityLease, lease.operation_id)
    if boundary in {"hung_get", "commit", "poll_cancel"}:
        assert row.state == "active" and row.finished_at is None
    else:
        assert row.state == "cancelled" and row.finished_at is not None
    if boundary == "hung_get":
        assert asyncio.get_running_loop().time() - start < 1


async def test_local_cancelled_probe_closes_only_after_awaited_work_drains(
    db_session,
    test_engine,
):
    lease = replace(await _setup(db_session), kind=ActivityKind.TOOL)
    entered = asyncio.Event()
    drained = asyncio.Event()

    async def work():
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            await asyncio.sleep(0)
            drained.set()

    async def poll(operation_id):
        pytest.fail("local cancellation must not depend on a remote journal")

    async def emit(name, payload):
        if name == "tool.finished":
            assert drained.is_set()

    task = asyncio.create_task(
        run_with_activity_lease(
            session_factory=async_sessionmaker(test_engine, expire_on_commit=False),
            lease=lease,
            work=work,
            poll_status=poll,
            emit=emit,
            controller_owned=False,
        )
    )
    await asyncio.wait_for(entered.wait(), 5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    row = await db_session.get(ProjectCellActivityLease, lease.operation_id)
    assert row.state == "cancelled" and row.finished_at is not None


@pytest.mark.parametrize("journal", ["completed", "running", "unavailable", "wrong_operation"])
async def test_terminal_generation_recovers_exact_activity_before_fenced_stale_release(
    db_session,
    test_engine,
    journal,
):
    from yleum_api.models.generation_run import GenerationRun
    from yleum_api.models.project_cell import ProjectCellOperation, ProjectCellWorkspace
    from yleum_api.services.orchestrator_client import ProjectCellResourceResponse
    from yleum_api.services.project_cell_activity import start_activity
    from yleum_api.services.project_cell_capacity import (
        claim_stale_generation_lease,
        release_one_stale_generation_lease,
    )

    lease = await _setup(db_session)
    run = await db_session.get(GenerationRun, lease.generation_run_id)
    run.status = "cancelled"
    await start_activity(
        db_session,
        workspace_id=lease.workspace_id,
        generation_run_id=run.id,
        kind=lease.kind,
        fencing_epoch=lease.fencing_epoch,
        deadline_at=lease.deadline_at,
        now=datetime.now(UTC),
        operation_id=lease.operation_id,
        proof_key=lease.proof_key,
    )
    await db_session.commit()
    assert (
        await claim_stale_generation_lease(
            db_session,
            requesting_run_id=run.id,
            workspace_id=lease.workspace_id,
        )
        is None
    )
    await db_session.rollback()
    controls = []
    polls = []

    async def poll(workspace_id, operation_id):
        assert workspace_id == lease.workspace_id and operation_id == lease.operation_id
        # Network read occurs without a workspace row lock. Use an independent
        # transaction to inspect the same row while the controller is polled.
        async with async_sessionmaker(test_engine)() as session:
            from sqlalchemy import select

            locked = await session.scalar(
                select(ProjectCellWorkspace)
                .where(
                    ProjectCellWorkspace.id == workspace_id,
                )
                .with_for_update(nowait=True)
            )
            assert locked.generation_run_id == lease.generation_run_id
        polls.append(operation_id)
        if journal == "unavailable":
            raise OrchestratorUnavailable("synthetic GET outage")
        return _status(
            uuid4() if journal == "wrong_operation" else operation_id,
            "completed" if journal == "wrong_operation" else journal,
        )

    async def control(request):
        controls.append(request)
        assert request.kind == "release"
        async with factory() as session:
            operation = await session.get(ProjectCellOperation, request.operation_id)
            assert operation.generation_run_id == lease.generation_run_id
        assert request.fencing_epoch > lease.fencing_epoch
        return ProjectCellResourceResponse(
            workspace_id=request.workspace_id,
            state="resources_ready",
            provider_ref="qa-cell",
            fencing_epoch=request.fencing_epoch,
            checkpoint_ref=None,
            has_workspace=True,
            has_agent_home=True,
            has_postgres=True,
            has_redis=True,
        )

    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    released = await release_one_stale_generation_lease(
        factory,
        requesting_run_id=lease.generation_run_id,
        workspace_id=lease.workspace_id,
        client=SimpleNamespace(agent_operation_status=poll, control=control),
    )
    async with factory() as session:
        row = await session.get(ProjectCellActivityLease, lease.operation_id)
        workspace = await session.get(ProjectCellWorkspace, lease.workspace_id)
        if journal == "completed":
            assert released and row.state == "cancelled" and row.finished_at is not None
            assert workspace.generation_run_id is None
            assert len(controls) == 1
            operation = await session.get(ProjectCellOperation, controls[0].operation_id)
            assert operation.status == "completed"
        else:
            assert not released and row.state == "active" and row.finished_at is None
            assert workspace.generation_run_id == lease.generation_run_id
            assert not controls
    assert polls == [lease.operation_id]


async def test_durable_cleanup_tick_retries_unknown_after_restart_without_new_work(
    db_session,
    test_engine,
    monkeypatch,
):
    from yleum_api.models.generation_run import GenerationRun
    from yleum_api.models.project_cell import ProjectCellWorkspace
    from yleum_api.services import project_cell_capacity
    from yleum_api.services.generation_deployment_drain import drain_status
    from yleum_api.services.orchestrator_client import ProjectCellResourceResponse
    from yleum_api.services.project_cell_activity import start_activity

    lease = await _setup(db_session)
    run = await db_session.get(GenerationRun, lease.generation_run_id)
    run.status = "cancelled"
    await start_activity(
        db_session,
        workspace_id=lease.workspace_id,
        generation_run_id=run.id,
        kind=lease.kind,
        fencing_epoch=7,
        deadline_at=lease.deadline_at,
        now=datetime.now(UTC),
        operation_id=lease.operation_id,
        proof_key=lease.proof_key,
    )
    await db_session.commit()
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    calls = []
    available = False

    async def poll(workspace_id, operation_id):
        calls.append("GET")
        assert workspace_id == lease.workspace_id and operation_id == lease.operation_id
        if not available:
            raise OrchestratorUnavailable("synthetic persistent journal outage")
        return _status(operation_id)

    async def control(request):
        calls.append("release")
        assert request.kind == "release" and request.fencing_epoch > 7
        return ProjectCellResourceResponse(
            workspace_id=request.workspace_id,
            state="resources_ready",
            provider_ref="qa-cell",
            fencing_epoch=request.fencing_epoch,
            checkpoint_ref=None,
            has_workspace=True,
            has_agent_home=True,
            has_postgres=True,
            has_redis=True,
        )

    client = SimpleNamespace(agent_operation_status=poll, control=control)
    monkeypatch.setattr(project_cell_capacity, "_TERMINAL_CLEANUP_CURSOR", None)
    assert await project_cell_capacity.advance_terminal_generation_cleanup(factory, client) == 0
    async with factory() as session:
        status = await drain_status(session, bootstrap=True)
        assert not status["drained"] and status["active"]["leases"] == 1
        workspace = await session.get(ProjectCellWorkspace, lease.workspace_id)
        assert workspace.generation_run_id == lease.generation_run_id
    # Restart loses only the scan cursor; the durable activity is retried.
    monkeypatch.setattr(project_cell_capacity, "_TERMINAL_CLEANUP_CURSOR", None)
    available = True
    assert await project_cell_capacity.advance_terminal_generation_cleanup(factory, client) == 1
    async with factory() as session:
        status = await drain_status(session, bootstrap=True)
        assert status["drained"]
        row = await session.get(ProjectCellActivityLease, lease.operation_id)
        workspace = await session.get(ProjectCellWorkspace, lease.workspace_id)
        assert row.state == "cancelled" and row.finished_at is not None
        assert workspace.generation_run_id is None
    assert calls == ["GET", "GET", "release"]


async def test_cancelled_started_event_cannot_orphan_a_never_dispatched_command(
    db_session,
    test_engine,
):
    lease = await _setup(db_session)
    entered = asyncio.Event()
    work_calls = []

    async def work():
        work_calls.append("work")
        pytest.fail("cancelled start event dispatched a command")

    async def poll(operation_id):
        pytest.fail("a newly allocated never-dispatched operation has no remote writer")

    async def emit(name, payload):
        if name == "tool.started":
            entered.set()
            await asyncio.Event().wait()

    task = asyncio.create_task(
        run_with_activity_lease(
            session_factory=async_sessionmaker(test_engine, expire_on_commit=False),
            lease=lease,
            work=work,
            poll_status=poll,
            emit=emit,
        )
    )
    await asyncio.wait_for(entered.wait(), 5)
    task.cancel("owner stop before dispatch")
    with pytest.raises(asyncio.CancelledError) as cancelled:
        await task
    assert cancelled.value.args == ("owner stop before dispatch",)
    row = await db_session.get(ProjectCellActivityLease, lease.operation_id)
    assert row.state == "cancelled" and row.finished_at is not None
    assert not work_calls


async def test_cancelled_reattached_started_event_still_requires_remote_terminal(
    db_session,
    test_engine,
):
    from yleum_api.services.project_cell_activity import start_activity

    lease = await _setup(db_session)
    await start_activity(
        db_session,
        workspace_id=lease.workspace_id,
        generation_run_id=lease.generation_run_id,
        kind=lease.kind,
        fencing_epoch=7,
        deadline_at=lease.deadline_at,
        now=datetime.now(UTC),
        operation_id=lease.operation_id,
        proof_key=lease.proof_key,
    )
    await db_session.commit()
    entered = asyncio.Event()
    calls = []

    async def work():
        pytest.fail("reattached start event cancellation must not redispatch")

    async def poll(operation_id):
        calls.append("GET")
        return _status(operation_id, "running")

    async def emit(name, payload):
        assert name == "tool.started"
        entered.set()
        await asyncio.Event().wait()

    task = asyncio.create_task(
        run_with_activity_lease(
            session_factory=async_sessionmaker(test_engine, expire_on_commit=False),
            lease=lease,
            work=work,
            poll_status=poll,
            emit=emit,
        )
    )
    await asyncio.wait_for(entered.wait(), 5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await db_session.refresh(await db_session.get(ProjectCellActivityLease, lease.operation_id))
    row = await db_session.get(ProjectCellActivityLease, lease.operation_id)
    assert row.state == "active" and row.finished_at is None
    assert calls == ["GET", "GET", "GET"]


@pytest.mark.parametrize("drift", ["activity_envelope", "new_generation"])
async def test_recovery_receipt_cannot_release_a_changed_generation_or_activity_envelope(
    db_session,
    test_engine,
    drift,
):
    from yleum_api.models.generation_run import GenerationRun
    from yleum_api.models.project import Project
    from yleum_api.models.project_cell import ProjectCellWorkspace
    from yleum_api.models.user import User
    from yleum_api.services.project_cell_activity import start_activity
    from yleum_api.services.project_cell_capacity import release_one_stale_generation_lease

    lease = await _setup(db_session)
    old_run = await db_session.get(GenerationRun, lease.generation_run_id)
    old_run.status = "cancelled"
    old_workspace = await db_session.get(ProjectCellWorkspace, lease.workspace_id)
    owner = await db_session.get(User, old_workspace.owner_id)
    project = await db_session.get(Project, old_workspace.project_id)
    replacement = await _new_run(db_session, owner, project)
    await start_activity(
        db_session,
        workspace_id=lease.workspace_id,
        generation_run_id=lease.generation_run_id,
        kind=lease.kind,
        fencing_epoch=7,
        deadline_at=lease.deadline_at,
        now=datetime.now(UTC),
        operation_id=lease.operation_id,
        proof_key=lease.proof_key,
    )
    await db_session.commit()
    factory = async_sessionmaker(test_engine, expire_on_commit=False)

    async def poll(workspace_id, operation_id):
        async with factory() as session:
            if drift == "activity_envelope":
                activity = await session.get(ProjectCellActivityLease, operation_id)
                activity.proof_key = "b" * 64
            else:
                workspace = await session.get(ProjectCellWorkspace, workspace_id)
                workspace.generation_run_id = replacement.id
                workspace.fencing_epoch += 1
            await session.commit()
        return _status(operation_id)

    async def forbidden_control(request):
        pytest.fail("old command receipt released changed authority")

    assert not await release_one_stale_generation_lease(
        factory,
        requesting_run_id=lease.generation_run_id,
        workspace_id=lease.workspace_id,
        client=SimpleNamespace(agent_operation_status=poll, control=forbidden_control),
    )
    async with factory() as session:
        workspace = await session.get(ProjectCellWorkspace, lease.workspace_id)
        activity = await session.get(ProjectCellActivityLease, lease.operation_id)
        if drift == "activity_envelope":
            assert activity.state == "active" and activity.finished_at is None
            assert workspace.generation_run_id == lease.generation_run_id
        else:
            assert activity.state == "cancelled" and activity.finished_at is not None
            assert workspace.generation_run_id == replacement.id and workspace.fencing_epoch == 8


@pytest.mark.parametrize("reattach", [False, True])
async def test_start_event_outage_cannot_terminalize_a_reattached_remote_writer(
    db_session,
    test_engine,
    reattach,
):
    from yleum_api.services.project_cell_activity import start_activity

    lease = await _setup(db_session)
    if reattach:
        await start_activity(
            db_session,
            workspace_id=lease.workspace_id,
            generation_run_id=lease.generation_run_id,
            kind=lease.kind,
            fencing_epoch=7,
            deadline_at=lease.deadline_at,
            now=datetime.now(UTC),
            operation_id=lease.operation_id,
            proof_key=lease.proof_key,
        )
        await db_session.commit()
    original = OSError("synthetic start event outage")
    calls = []

    async def work():
        pytest.fail("failed start event must not dispatch a command")

    async def poll(operation_id):
        calls.append("GET")
        return _status(operation_id, "running")

    async def emit(name, payload):
        if name == "tool.started":
            raise original
        calls.append(name)

    with pytest.raises(OSError) as raised:
        await run_with_activity_lease(
            session_factory=async_sessionmaker(test_engine, expire_on_commit=False),
            lease=lease,
            work=work,
            poll_status=poll,
            emit=emit,
        )
    assert raised.value is original
    row = await db_session.get(ProjectCellActivityLease, lease.operation_id)
    if reattach:
        assert row.state == "active" and row.finished_at is None
        assert await activity_blocks_hibernation(db_session, workspace_id=lease.workspace_id)
        assert "tool.finished" not in calls
    else:
        assert row.state == "failed" and row.finished_at is not None


@pytest.mark.parametrize("journal", ["completed", "running", "unavailable", "wrong_operation"])
@pytest.mark.parametrize("replacement_writer", [False, True, "terminal"])
async def test_cleanup_recovers_retained_activity_after_actual_executor_release(
    db_session,
    test_engine,
    monkeypatch,
    journal,
    replacement_writer,
):
    from yleum_api.models.generation_run import GenerationRun
    from yleum_api.models.project import Project
    from yleum_api.models.project_cell import ProjectCellWorkspace
    from yleum_api.models.user import User
    from yleum_api.services import project_cell_capacity, project_cell_executor
    from yleum_api.services.generation_deployment_drain import drain_status
    from yleum_api.services.orchestrator_client import ProjectCellResourceResponse
    from yleum_api.services.project_cell_activity import start_activity

    lease = await _setup(db_session)
    run = await db_session.get(GenerationRun, lease.generation_run_id)
    run.status = "cancelled"
    await start_activity(
        db_session,
        workspace_id=lease.workspace_id,
        generation_run_id=run.id,
        kind=lease.kind,
        fencing_epoch=7,
        deadline_at=lease.deadline_at,
        now=datetime.now(UTC),
        operation_id=lease.operation_id,
        proof_key=lease.proof_key,
    )
    await db_session.commit()
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    controls = []
    polls = []

    async def control(request):
        controls.append(request.operation_id)
        assert request.kind == "release" and request.fencing_epoch > 7
        return ProjectCellResourceResponse(
            workspace_id=request.workspace_id,
            state="resources_ready",
            provider_ref="qa-cell",
            fencing_epoch=request.fencing_epoch,
            checkpoint_ref=None,
            has_workspace=True,
            has_agent_home=True,
            has_postgres=True,
            has_redis=True,
        )

    async def poll(workspace_id, operation_id):
        polls.append(operation_id)
        assert workspace_id == lease.workspace_id and operation_id == lease.operation_id
        if journal == "unavailable":
            raise OrchestratorUnavailable("synthetic retained journal outage")
        return _status(
            uuid4() if journal == "wrong_operation" else operation_id,
            "completed" if journal == "wrong_operation" else journal,
        )

    controller = SimpleNamespace(control=control, agent_operation_status=poll)
    monkeypatch.setattr(
        project_cell_executor, "HttpProjectCellOrchestratorClient", lambda: controller
    )
    # Real executor release performs reservation, higher-fence remote-contract
    # receipt persistence and its normal lease clearing in physical PostgreSQL.
    await project_cell_executor._release_generation_lease(
        session_factory=factory,
        workspace_id=lease.workspace_id,
        generation_run_id=lease.generation_run_id,
        profile_version="qa-review",
    )
    replacement_id = None
    async with factory() as session:
        workspace = await session.get(ProjectCellWorkspace, lease.workspace_id)
        row = await session.get(ProjectCellActivityLease, lease.operation_id)
        assert workspace.generation_run_id is None and workspace.fencing_epoch > 7
        assert row.state == "active" and row.finished_at is None
        if replacement_writer:
            owner = await session.get(User, workspace.owner_id)
            project = await session.get(Project, workspace.project_id)
            replacement = await _new_run(session, owner, project)
            replacement_id = replacement.id
            if replacement_writer == "terminal":
                replacement.status = "completed"
            workspace.generation_run_id = replacement.id
            workspace.fencing_epoch += 1
            await session.commit()
        preserved_fence = workspace.fencing_epoch
    monkeypatch.setattr(project_cell_capacity, "_TERMINAL_CLEANUP_CURSOR", None)
    await project_cell_capacity.advance_terminal_generation_cleanup(factory, controller)
    async with factory() as session:
        workspace = await session.get(ProjectCellWorkspace, lease.workspace_id)
        row = await session.get(ProjectCellActivityLease, lease.operation_id)
        status = await drain_status(session, bootstrap=True)
        assert workspace.generation_run_id == replacement_id
        assert workspace.fencing_epoch == preserved_fence
        if journal == "completed":
            assert row.state == "cancelled" and row.finished_at is not None
            assert status["active"]["leases"] == 0
        else:
            assert row.state == "active" and row.finished_at is None
            assert status["active"]["leases"] == 1 and not status["drained"]
    assert polls == [lease.operation_id]
    assert len(controls) == 1, "old activity reconciliation issued a new release control"
