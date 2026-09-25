from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from yleum_api.models.generation_run import GenerationRun
from yleum_api.models.project import Project
from yleum_api.models.project_cell import ProjectCellWorkspace
from yleum_api.models.user import User
from yleum_api.services.project_cell_activity import (
    ActivityKind,
    ActivityState,
    ProjectCellActivityConflict,
    activity_blocks_hibernation,
    finish_activity,
    heartbeat_activity,
    start_activity,
)

pytestmark = pytest.mark.asyncio


async def _new_user(session: AsyncSession, label: str) -> User:
    user = User(email=f"activity-{label}-{uuid.uuid4().hex}@example.com", password_hash="x")
    session.add(user)
    await session.flush()
    return user


async def _new_project(session: AsyncSession, owner: User) -> Project:
    project = Project(
        owner_id=owner.id,
        name="Activity test",
        slug=f"activity-{uuid.uuid4().hex}",
        template="blank",
    )
    session.add(project)
    await session.flush()
    return project


async def _new_run(session: AsyncSession, owner: User, project: Project) -> GenerationRun:
    run = GenerationRun(
        project_id=project.id,
        user_id=owner.id,
        idempotency_key=f"activity-run:{uuid.uuid4().hex}",
        prompt_hash="a" * 64,
        status="running",
        agent_state={},
    )
    session.add(run)
    await session.flush()
    return run


async def _new_workspace(
    session: AsyncSession,
    owner: User,
    project: Project,
    run: GenerationRun,
) -> ProjectCellWorkspace:
    workspace = ProjectCellWorkspace(
        project_id=project.id,
        owner_id=owner.id,
        provider="docker_owner_canary",
        state="ready",
        generation_run_id=run.id,
        fencing_epoch=7,
    )
    session.add(workspace)
    await session.flush()
    return workspace


async def test_activity_lease_blocks_hibernation_until_finished(
    db_session: AsyncSession,
) -> None:
    owner = await _new_user(db_session, "owner")
    project = await _new_project(db_session, owner)
    run = await _new_run(db_session, owner, project)
    workspace = await _new_workspace(db_session, owner, project, run)
    now = datetime(2026, 9, 4, tzinfo=UTC)

    lease = await start_activity(
        db_session,
        workspace_id=workspace.id,
        generation_run_id=run.id,
        kind=ActivityKind.FINALIZATION,
        fencing_epoch=7,
        proof_key="a" * 64,
        phase="full_build",
        now=now,
        deadline_at=now + timedelta(minutes=5),
    )
    assert await activity_blocks_hibernation(db_session, workspace_id=workspace.id) is True

    await heartbeat_activity(
        db_session,
        operation_id=lease.operation_id,
        workspace_id=workspace.id,
        fencing_epoch=7,
        heartbeat_at=now + timedelta(seconds=15),
        phase="runtime_probe",
        log_bytes=256,
    )
    await finish_activity(
        db_session,
        operation_id=lease.operation_id,
        state=ActivityState.COMPLETED,
        finished_at=now + timedelta(minutes=1),
        diagnostic="ok",
        log_bytes=512,
    )

    assert await activity_blocks_hibernation(db_session, workspace_id=workspace.id) is False


async def test_only_one_active_activity_per_workspace(db_session: AsyncSession) -> None:
    owner = await _new_user(db_session, "single")
    project = await _new_project(db_session, owner)
    run = await _new_run(db_session, owner, project)
    workspace = await _new_workspace(db_session, owner, project, run)
    now = datetime(2026, 9, 4, tzinfo=UTC)

    await start_activity(
        db_session,
        workspace_id=workspace.id,
        generation_run_id=run.id,
        kind=ActivityKind.COMMAND,
        fencing_epoch=7,
        now=now,
        deadline_at=now + timedelta(minutes=5),
    )
    with pytest.raises(ProjectCellActivityConflict, match="active activity"):
        await start_activity(
            db_session,
            workspace_id=workspace.id,
            generation_run_id=run.id,
            kind=ActivityKind.SNAPSHOT,
            fencing_epoch=7,
            now=now,
            deadline_at=now + timedelta(minutes=5),
        )
    assert await activity_blocks_hibernation(db_session, workspace_id=workspace.id) is True


async def test_heartbeat_requires_exact_workspace_and_fence(db_session: AsyncSession) -> None:
    owner = await _new_user(db_session, "fence")
    project = await _new_project(db_session, owner)
    run = await _new_run(db_session, owner, project)
    workspace = await _new_workspace(db_session, owner, project, run)
    now = datetime(2026, 9, 4, tzinfo=UTC)
    lease = await start_activity(
        db_session,
        workspace_id=workspace.id,
        generation_run_id=run.id,
        kind=ActivityKind.COMMAND,
        fencing_epoch=7,
        now=now,
        deadline_at=now + timedelta(minutes=5),
    )

    with pytest.raises(ProjectCellActivityConflict, match="exact active"):
        await heartbeat_activity(
            db_session,
            operation_id=lease.operation_id,
            workspace_id=workspace.id,
            fencing_epoch=8,
            heartbeat_at=now + timedelta(seconds=15),
        )


async def test_finish_is_idempotent_only_for_matching_terminal_state(
    db_session: AsyncSession,
) -> None:
    owner = await _new_user(db_session, "finish")
    project = await _new_project(db_session, owner)
    run = await _new_run(db_session, owner, project)
    workspace = await _new_workspace(db_session, owner, project, run)
    now = datetime(2026, 9, 4, tzinfo=UTC)
    lease = await start_activity(
        db_session,
        workspace_id=workspace.id,
        generation_run_id=run.id,
        kind=ActivityKind.FINALIZATION,
        fencing_epoch=7,
        now=now,
        deadline_at=now + timedelta(minutes=5),
    )
    finished = await finish_activity(
        db_session,
        operation_id=lease.operation_id,
        state=ActivityState.COMPLETED,
        finished_at=now + timedelta(minutes=1),
        diagnostic="AUTH_SECRET=hidden-value\n" + ("x" * 10_000),
    )
    replay = await finish_activity(
        db_session,
        operation_id=lease.operation_id,
        state=ActivityState.COMPLETED,
        finished_at=now + timedelta(minutes=2),
    )

    assert replay is finished
    assert replay.finished_at == now + timedelta(minutes=1)
    assert "hidden-value" not in (replay.redacted_diagnostic or "")
    assert len((replay.redacted_diagnostic or "").encode("utf-8")) <= 4096
    with pytest.raises(ProjectCellActivityConflict, match="already completed"):
        await finish_activity(
            db_session,
            operation_id=lease.operation_id,
            state=ActivityState.FAILED,
            finished_at=now + timedelta(minutes=2),
        )


@pytest.mark.parametrize("failure_boundary", [None, "emit", "finish"])
async def test_exact_failed_activity_replays_original_code_without_work(
    db_session, test_engine, monkeypatch, failure_boundary
):
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from yleum_api.services.orchestrator_client import OrchestratorBadRequest
    from yleum_api.services.project_cell_activity import ActivityStart, run_with_activity_lease

    owner = await _new_user(db_session, "replay")
    project = await _new_project(db_session, owner)
    run = await _new_run(db_session, owner, project)
    workspace = await _new_workspace(db_session, owner, project, run)
    await db_session.commit()
    lease = ActivityStart(
        uuid.uuid4(),
        workspace.id,
        run.id,
        ActivityKind.COMMAND,
        7,
        datetime.now(UTC) + timedelta(minutes=5),
        "a" * 64,
        "prepare",
    )
    calls = []

    async def work():
        calls.append("work")
        raise OrchestratorBadRequest(
            "not safe to echo",
            status_code=409,
            upstream_code="protected_environment_recovery_required",
        )

    async def poll(operation_id):
        raise AssertionError("known terminal failure must not poll or execute")

    async def emit(kind, payload):
        if failure_boundary == "emit" and kind == "tool.finished":
            raise OSError("event store unavailable")

    if failure_boundary == "finish":
        from yleum_api.services import project_cell_activity

        async def failed_persistence(*args, **kwargs):
            raise OSError("activity store unavailable")

        monkeypatch.setattr(project_cell_activity, "finish_activity", failed_persistence)
    for attempt in range(1 if failure_boundary == "finish" else 2):
        with pytest.raises(Exception, match="protected_environment_recovery_required") as raised:
            await run_with_activity_lease(
                session_factory=async_sessionmaker(test_engine, expire_on_commit=False),
                lease=lease,
                work=work,
                poll_status=poll,
                emit=emit,
            )
        assert raised.value.operation_id == lease.operation_id
    assert calls == ["work"]


@pytest.mark.parametrize(
    "state,saved_response",
    [
        (ActivityState.COMPLETED, 42),
        (ActivityState.FAILED, 0),
        (ActivityState.TIMED_OUT, -1),
    ],
)
@pytest.mark.parametrize("decoder_failure", [False, True])
async def test_completed_activity_replays_saved_journal_without_work(
    db_session, test_engine, state, saved_response, decoder_failure
):
    from types import SimpleNamespace

    from sqlalchemy.ext.asyncio import async_sessionmaker

    from yleum_api.services.project_cell_activity import ActivityStart, run_with_activity_lease

    owner = await _new_user(db_session, "completed")
    project = await _new_project(db_session, owner)
    run = await _new_run(db_session, owner, project)
    workspace = await _new_workspace(db_session, owner, project, run)
    now = datetime.now(UTC)
    saved = await start_activity(
        db_session,
        workspace_id=workspace.id,
        generation_run_id=run.id,
        kind=ActivityKind.COMMAND,
        fencing_epoch=7,
        now=now,
        deadline_at=now + timedelta(minutes=5),
        proof_key="a" * 64,
    )
    await finish_activity(db_session, operation_id=saved.operation_id, state=state, finished_at=now)
    await db_session.commit()
    lease = ActivityStart(
        saved.operation_id,
        workspace.id,
        run.id,
        ActivityKind.COMMAND,
        7,
        now + timedelta(minutes=5),
        "a" * 64,
    )
    calls = []

    async def work():
        pytest.fail("terminal replay must never execute work")

    async def poll(operation_id):
        calls.append(operation_id)
        return SimpleNamespace(
            operation_id=operation_id, state="completed", terminal_response=saved_response
        )

    async def replay(status):
        if decoder_failure:
            raise ValueError("invalid retained command identity")
        return status.terminal_response

    async def emit(*args):
        pytest.fail("terminal replay must not publish another start or finish")

    async def run_replay():
        return await run_with_activity_lease(
            session_factory=async_sessionmaker(test_engine, expire_on_commit=False),
            lease=lease,
            work=work,
            poll_status=poll,
            emit=emit,
            replay_terminal=replay,
            terminal_state=lambda result: (
                ActivityState.COMPLETED
                if result == 42
                else ActivityState.TIMED_OUT
                if result == -1
                else ActivityState.FAILED
            ),
        )

    if decoder_failure:
        from yleum_api.services.project_cell_errors import ProjectCellInfrastructureError

        with pytest.raises(ProjectCellInfrastructureError, match="activity_replay_unavailable"):
            await run_replay()
    else:
        assert await run_replay() == saved_response
    assert calls == [saved.operation_id]
    await db_session.refresh(saved)
    assert saved.state == state.value and saved.finished_at == now


@pytest.mark.parametrize(
    "field,value",
    [
        ("workspace_id", uuid.uuid4()),
        ("generation_run_id", uuid.uuid4()),
        ("fencing_epoch", 8),
        ("proof_key", "b" * 64),
        ("kind", ActivityKind.SNAPSHOT),
        ("current_fence", 8),
        ("current_run", None),
    ],
)
async def test_activity_replay_rejects_changed_authority_before_effects(
    db_session, test_engine, field, value
):
    from dataclasses import replace

    from sqlalchemy.ext.asyncio import async_sessionmaker

    from yleum_api.services.project_cell_activity import ActivityStart, run_with_activity_lease

    owner = await _new_user(db_session, "authority")
    project = await _new_project(db_session, owner)
    run = await _new_run(db_session, owner, project)
    workspace = await _new_workspace(db_session, owner, project, run)
    now = datetime.now(UTC)
    saved = await start_activity(
        db_session,
        workspace_id=workspace.id,
        generation_run_id=run.id,
        kind=ActivityKind.COMMAND,
        fencing_epoch=7,
        now=now,
        deadline_at=now + timedelta(minutes=5),
        proof_key="a" * 64,
    )
    await db_session.commit()
    lease = ActivityStart(
        saved.operation_id,
        workspace.id,
        run.id,
        ActivityKind.COMMAND,
        7,
        now + timedelta(minutes=5),
        "a" * 64,
    )

    if field == "current_fence":
        workspace.fencing_epoch = value
        await db_session.commit()
    elif field == "current_run":
        workspace.generation_run_id = value
        await db_session.commit()
    else:
        lease = replace(lease, **{field: value})

    async def forbidden(*args):
        pytest.fail("mismatched authority reached an effect")

    with pytest.raises(ProjectCellActivityConflict, match="envelope mismatch"):
        await run_with_activity_lease(
            session_factory=async_sessionmaker(test_engine, expire_on_commit=False),
            lease=lease,
            work=forbidden,
            poll_status=forbidden,
            emit=forbidden,
        )
    await db_session.refresh(saved)
    assert saved.state == "active" and saved.finished_at is None


async def test_cancellation_stays_terminal_and_replay_never_resurrects_work(
    db_session, test_engine
):
    import asyncio
    from types import SimpleNamespace

    from sqlalchemy.ext.asyncio import async_sessionmaker

    from yleum_api.models.project_cell import ProjectCellActivityLease
    from yleum_api.services.project_cell_activity import ActivityStart, run_with_activity_lease

    owner = await _new_user(db_session, "cancel-replay")
    project = await _new_project(db_session, owner)
    run = await _new_run(db_session, owner, project)
    workspace = await _new_workspace(db_session, owner, project, run)
    await db_session.commit()
    lease = ActivityStart(
        uuid.uuid4(),
        workspace.id,
        run.id,
        ActivityKind.COMMAND,
        7,
        datetime.now(UTC) + timedelta(minutes=5),
        "a" * 64,
    )
    started = asyncio.Event()
    calls = []

    async def work():
        calls.append("work")
        started.set()
        await asyncio.Event().wait()

    async def poll(operation_id):
        calls.append("poll")
        return SimpleNamespace(
            state="running",
            heartbeat_at=datetime.now(UTC),
            terminal_response=None,
            phase="prepare",
            log_bytes=0,
        )

    async def emit(*args):
        pass

    async def run_work():
        return await run_with_activity_lease(
            session_factory=async_sessionmaker(test_engine, expire_on_commit=False),
            lease=lease,
            work=work,
            poll_status=poll,
            emit=emit,
        )

    task = asyncio.create_task(run_work())
    await asyncio.wait_for(started.wait(), 5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    with pytest.raises(asyncio.CancelledError):
        await run_work()
    saved = await db_session.get(ProjectCellActivityLease, lease.operation_id)
    assert saved.state == "cancelled" and saved.finished_at is not None
    assert calls == ["work", "poll"]
