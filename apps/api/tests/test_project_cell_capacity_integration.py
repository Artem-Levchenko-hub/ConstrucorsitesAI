from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from omnia_api.models.generation_run import GenerationRun
from omnia_api.models.message import Message
from omnia_api.models.project import Project
from omnia_api.models.project_cell import ProjectCellOperation, ProjectCellWorkspace
from omnia_api.models.user import User
from omnia_api.routers import messages
from omnia_api.services.generation_runs import GenerationDispatch, store_generation_dispatch

pytestmark = pytest.mark.asyncio


async def _queued_dispatch(session: AsyncSession) -> GenerationRun:
    owner = User(email=f"capacity-race-{uuid4().hex}@example.test", password_hash="x")
    session.add(owner)
    await session.flush()
    project = Project(
        owner_id=owner.id,
        name="Capacity race",
        slug=f"capacity-race-{uuid4().hex}",
        template="max_miniapp",
    )
    session.add(project)
    await session.flush()
    user_message = Message(project_id=project.id, role="user", content="Собери приложение")
    assistant_message = Message(project_id=project.id, role="assistant", content="")
    session.add_all((user_message, assistant_message))
    await session.flush()
    run = GenerationRun(
        project_id=project.id,
        user_id=owner.id,
        user_message_id=user_message.id,
        assistant_message_id=assistant_message.id,
        idempotency_key=f"capacity-race-{uuid4().hex}",
        prompt_hash="c" * 64,
        status="queued_for_capacity",
    )
    session.add(run)
    await session.flush()
    store_generation_dispatch(
        run,
        GenerationDispatch(
            schema_version=1,
            project_id=project.id,
            user_id=owner.id,
            user_message_id=user_message.id,
            assistant_message_id=assistant_message.id,
            current_snapshot_id=None,
            prompt_text="Собери приложение",
            model_id="google/gemini-2.5-pro",
            force_model=None,
            is_free=False,
            free_business_id=None,
            orchestrate=True,
            selected_elements=[],
        ),
    )
    await session.commit()
    return run


async def test_two_api_schedulers_claim_one_dispatch_and_recover_expired_lease(
    test_engine: AsyncEngine,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run = await _queued_dispatch(db_session)
    spawned: list[dict[str, object]] = []

    monkeypatch.setattr(messages, "get_engine", lambda: test_engine)
    monkeypatch.setattr(
        messages,
        "_spawn_process_prompt",
        lambda **kwargs: spawned.append(dict(kwargs)),
    )
    messages._PROMPT_TASKS.clear()

    claimed = await asyncio.gather(
        messages.resume_capacity_queued_generations(),
        messages.resume_capacity_queued_generations(),
    )

    assert sorted(claimed) == [0, 1]
    assert [item["run_id"] for item in spawned] == [run.id]
    first_token = spawned[0]["capacity_dispatch_token"]

    async with AsyncSession(test_engine, expire_on_commit=False) as session:
        persisted = await session.get(GenerationRun, run.id)
        assert persisted is not None
        state = dict(persisted.agent_state or {})
        claim = dict(state["capacity_dispatch_claim"])
        claim["expires_at"] = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
        state["capacity_dispatch_claim"] = claim
        persisted.agent_state = state
        await session.commit()

    assert await messages.resume_capacity_queued_generations() == 1
    assert [item["run_id"] for item in spawned] == [run.id, run.id]
    assert spawned[1]["capacity_dispatch_token"] != first_token


async def test_queued_cancellation_is_atomic_and_not_resumed_after_restart(
    test_engine: AsyncEngine,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run = await _queued_dispatch(db_session)
    workspace = ProjectCellWorkspace(
        project_id=run.project_id,
        owner_id=run.user_id,
        provider="docker_owner_canary",
        state="provisioning",
        generation_run_id=run.id,
    )
    db_session.add(workspace)
    await db_session.flush()
    operation = ProjectCellOperation(
        workspace_id=workspace.id,
        generation_run_id=run.id,
        idempotency_key=f"cancel-{uuid4().hex}",
        request_digest="d" * 64,
        fencing_epoch=1,
        kind="ensure",
        status="waiting_capacity",
        request_payload={"profile_version": "docker-owner-cell-resources-v1"},
        capacity_reason="insufficient_memory",
        attempt_count=1,
    )
    db_session.add(operation)
    await db_session.commit()
    assistant_message_id = run.assistant_message_id
    assert assistant_message_id is not None

    monkeypatch.setattr(messages, "get_engine", lambda: test_engine)
    await messages._finalize_cancelled_generation(
        run.project_id,
        assistant_message_id,
        run.id,
    )

    async with AsyncSession(test_engine, expire_on_commit=False) as session:
        cancelled_run = await session.get(GenerationRun, run.id)
        cancelled_message = await session.get(Message, assistant_message_id)
        cancelled_operation = await session.get(ProjectCellOperation, operation.id)
        assert cancelled_run is not None and cancelled_run.status == "cancelled"
        assert cancelled_message is not None
        assert "[Отменено пользователем]" in cancelled_message.content
        assert cancelled_operation is not None
        assert cancelled_operation.status == "cancelled"

    assert await messages.resume_capacity_queued_generations() == 0
