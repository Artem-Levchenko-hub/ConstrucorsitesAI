from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from omnia_api.models.generation_run import GenerationRun
from omnia_api.models.project import Project
from omnia_api.models.project_cell import ProjectCellWorkspace
from omnia_api.models.user import User
from omnia_api.services.project_cell_capacity import (
    claim_capacity_turn,
    claim_idle_hibernation_victim,
    claim_stale_generation_lease,
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
