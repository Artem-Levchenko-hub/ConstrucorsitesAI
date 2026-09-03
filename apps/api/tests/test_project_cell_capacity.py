from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from omnia_api.models.generation_run import GenerationRun
from omnia_api.models.project import Project
from omnia_api.models.project_cell import ProjectCellOperation, ProjectCellWorkspace
from omnia_api.models.user import User
from omnia_api.services.orchestrator_client import (
    OrchestratorUnavailable,
    ProjectCellResourceResponse,
)
from omnia_api.services.project_cell_capacity import (
    claim_capacity_turn,
    claim_idle_hibernation_victim,
    claim_stale_generation_lease,
    hibernate_one_idle_workspace,
    release_one_stale_generation_lease,
)
from omnia_api.services.project_cell_lifecycle import execute_cell_operation
from omnia_api.services.project_cells import (
    claim_cell_operation_committed,
    fail_cell_operation,
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

    turns = [
        await claim_capacity_turn(db_session, run.id)
        for run in (first, second, third)
    ]

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
