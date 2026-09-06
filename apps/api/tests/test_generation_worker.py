import asyncio
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from omnia_api.models.generation_run import GenerationRun
from omnia_api.models.message import Message
from omnia_api.models.project import Project
from omnia_api.models.user import User
from omnia_api.routers import messages
from omnia_api.services.generation_runs import (
    GenerationDispatch,
    recover_interrupted_generation_runs,
    store_generation_dispatch,
)
from omnia_api.workers import generation

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


async def test_live_worker_survives_api_recovery_and_duplicate_delivery(
    db_session,
    test_engine,
    monkeypatch,
):
    run = await _queued_dispatch(db_session)
    run.execution_backend = "worker"
    await db_session.commit()
    monkeypatch.setattr(generation, "get_engine", lambda: test_engine)
    monkeypatch.setattr(messages, "get_engine", lambda: test_engine)
    entered, finish = asyncio.Event(), asyncio.Event()
    executions = []
    factory = async_sessionmaker(test_engine, expire_on_commit=False)

    async def work(**kwargs):
        executions.append(kwargs["run_id"])
        entered.set()
        await finish.wait()
        async with factory() as session:
            row = await session.get(GenerationRun, run.id)
            row.status = "completed"
            row.finished_at = datetime.now(UTC)
            await session.commit()

    async def tracked(work, **kwargs):
        await work

    monkeypatch.setattr(messages, "_process_prompt", work)
    monkeypatch.setattr(messages, "_run_tracked_prompt", tracked)
    task = asyncio.create_task(generation.execute_dispatch(run.id))
    try:
        await asyncio.wait_for(entered.wait(), 5)
        assert not await generation.execute_dispatch(run.id)
        async with factory() as api_session:
            assert await recover_interrupted_generation_runs(api_session) == 0
        assert await messages.resume_capacity_queued_generations() == 0
        finish.set()
        assert await asyncio.wait_for(task, 5)
        await db_session.refresh(run)
        assert run.status == "completed"
        assert executions == [run.id]
    finally:
        finish.set()
        await task


async def test_claimed_orphan_is_not_replayed(db_session, test_engine, monkeypatch):
    run = await _queued_dispatch(db_session)
    run.execution_backend = "worker"
    run.execution_started_at = datetime.now(UTC)
    await db_session.commit()
    monkeypatch.setattr(generation, "get_engine", lambda: test_engine)
    monkeypatch.setattr(messages, "get_engine", lambda: test_engine)
    assert not await generation.execute_dispatch(run.id)
    await db_session.refresh(run)
    assert run.status == "failed"
    assert "unknown effects were not replayed" in run.error


async def test_cancel_before_dispatch_never_executes(db_session, test_engine, monkeypatch):
    run = await _queued_dispatch(db_session)
    run.execution_backend = "worker"
    run.status = "cancel_requested"
    await db_session.commit()
    monkeypatch.setattr(generation, "get_engine", lambda: test_engine)
    monkeypatch.setattr(messages, "get_engine", lambda: test_engine)
    executions = []

    async def work(**kwargs):
        executions.append(kwargs)

    async def tracked(work, **kwargs):
        await work

    monkeypatch.setattr(messages, "_process_prompt", work)
    monkeypatch.setattr(messages, "_run_tracked_prompt", tracked)
    await generation.execute_dispatch(run.id)
    assert executions == []
    await db_session.refresh(run)
    assert run.status == "cancelled"


async def test_terminal_run_cleanup_waits_for_execution_lock(db_session, test_engine, monkeypatch):
    from sqlalchemy import text

    from omnia_api.models.project_cell import ProjectCellOperation, ProjectCellWorkspace

    run = await _queued_dispatch(db_session)
    run.execution_backend = "worker"
    run.execution_started_at = datetime.now(UTC)
    run.status = "failed"
    run.error = "original failure"
    run.finished_at = datetime.now(UTC)
    workspace = ProjectCellWorkspace(
        project_id=run.project_id, owner_id=run.user_id, provider="docker", state="ready",
    )
    db_session.add(workspace)
    await db_session.flush()
    operation = ProjectCellOperation(
        workspace_id=workspace.id, generation_run_id=run.id, execution_run_id=run.id,
        kind="release", status="running", request_digest="a" * 64,
        idempotency_key="terminal-release",
    )
    db_session.add(operation)
    await db_session.commit()
    monkeypatch.setattr(generation, "get_engine", lambda: test_engine)
    params = {"ns": generation._LOCK_NAMESPACE, "key": generation._lock_key(run.id)}
    async with test_engine.connect() as owner:
        await owner.execute(text("SELECT pg_advisory_lock(:ns, :key)"), params)
        try:
            assert not await generation.execute_dispatch(run.id)
            await db_session.refresh(operation)
            assert operation.status == "running"
        finally:
            await owner.execute(text("SELECT pg_advisory_unlock(:ns, :key)"), params)
    assert not await generation.execute_dispatch(run.id)
    await db_session.refresh(operation)
    await db_session.refresh(run)
    assert operation.status == "indeterminate"
    assert run.status == "failed"
    assert run.error == "original failure"


async def test_database_deadline_cancels_real_task_without_rewriting_failure(
    db_session, test_engine, monkeypatch,
):
    run = await _queued_dispatch(db_session)
    run.execution_backend = "worker"
    await db_session.commit()
    monkeypatch.setattr(generation, "get_engine", lambda: test_engine)
    monkeypatch.setattr(messages, "get_engine", lambda: test_engine)
    entered, cancelled = asyncio.Event(), asyncio.Event()

    async def work(**kwargs):
        entered.set()
        try:
            await asyncio.Future()
        finally:
            cancelled.set()

    async def no_redis_signal(*args):
        await asyncio.Future()

    async def clear(*args):
        pass

    monkeypatch.setattr(messages, "_process_prompt", work)
    monkeypatch.setattr(messages, "_wait_for_generation_cancel", no_redis_signal)
    monkeypatch.setattr(messages, "clear_generation_cancel", clear)
    task = asyncio.create_task(generation.execute_dispatch(run.id))
    try:
        await asyncio.wait_for(entered.wait(), 5)
        await db_session.refresh(run)
        run.status = "failed"
        run.error = "generation deadline exceeded; phase=agent"
        run.finished_at = datetime.now(UTC)
        await db_session.commit()
        await asyncio.wait_for(task, 5)
        assert cancelled.is_set()
        await db_session.refresh(run)
        assert run.status == "failed"
        assert run.error == "generation deadline exceeded; phase=agent"
    finally:
        if not task.done():
            task.cancel()
            await task


async def test_dispatcher_retries_unclaimed_work_after_connection_failure(
    db_session, test_engine, monkeypatch,
):
    run = await _queued_dispatch(db_session)
    run.execution_backend = "worker"
    await db_session.commit()
    monkeypatch.setattr(generation, "get_engine", lambda: test_engine)
    recovered = asyncio.Event()
    attempts = 0

    class Redis:
        async def set(self, *args, **kwargs):
            pass

    async def execute(run_id):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise ConnectionError("database unavailable before claiming")
        recovered.set()
        return True

    monkeypatch.setattr(generation, "get_redis", Redis)
    monkeypatch.setattr(generation, "execute_dispatch", execute)
    worker = asyncio.create_task(generation.run_forever())
    try:
        await asyncio.wait_for(recovered.wait(), 7)
        assert attempts == 2
    finally:
        worker.cancel()
        with pytest.raises(asyncio.CancelledError):
            await worker
