from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from omnia_api.models.generation_run import GenerationRun
from omnia_api.models.project import Project
from omnia_api.models.project_cell import ProjectCellWorkspace
from omnia_api.models.user import User
from omnia_api.services.project_cell_activity import (
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
