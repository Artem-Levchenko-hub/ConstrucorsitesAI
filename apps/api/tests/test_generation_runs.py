from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from omnia_api.core.deps import get_current_user
from omnia_api.core.errors import ApiError
from omnia_api.main import app
from omnia_api.models.generation_run import GenerationRun
from omnia_api.models.message import Message
from omnia_api.models.project import Project
from omnia_api.models.project_cell import ProjectCellOperation, ProjectCellWorkspace
from omnia_api.models.project_memory import ProjectMemoryRevision
from omnia_api.models.snapshot import Snapshot
from omnia_api.models.user import User
from omnia_api.routers import messages
from omnia_api.services.generation_runs import (
    _finalize_generation_run,
    finalize_generation_run,
    reconcile_completed_build_runs,
    recover_interrupted_generation_runs,
    reserve_generation_run,
)


async def test_queued_for_capacity_is_a_durable_active_generation_status() -> None:
    from omnia_api.services.generation_runs import ACTIVE_GENERATION_STATUSES

    assert "queued_for_capacity" in ACTIVE_GENERATION_STATUSES
    constraint = next(
        item
        for item in GenerationRun.__table__.constraints
        if "status IN" in str(getattr(item, "sqltext", ""))
    )
    assert "queued_for_capacity" in str(constraint.sqltext)


async def test_generation_dispatch_requires_exact_owned_json_shape() -> None:
    from omnia_api.services.generation_runs import GenerationDispatch, load_generation_dispatch

    run = GenerationRun(
        project_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        idempotency_key=f"dispatch-{uuid.uuid4().hex}",
        prompt_hash="a" * 64,
        status="queued_for_capacity",
    )
    run.id = uuid.uuid4()
    dispatch = GenerationDispatch(
        schema_version=1,
        project_id=run.project_id,
        user_id=run.user_id,
        user_message_id=uuid.uuid4(),
        assistant_message_id=uuid.uuid4(),
        current_snapshot_id=None,
        prompt_text="build the app",
        model_id="google/gemini-2.5-pro",
        force_model=None,
        is_free=False,
        free_business_id=None,
        orchestrate=True,
        selected_elements=[],
    )
    run.agent_state = {"dispatch": dispatch.to_json(), "steps": []}

    assert load_generation_dispatch(run) == dispatch
    run.agent_state = {"dispatch": dispatch.to_json() | {"extra": True}}
    with pytest.raises(ValueError, match="exact keys"):
        load_generation_dispatch(run)
    run.agent_state = {"dispatch": dispatch.to_json() | {"user_id": str(uuid.uuid4())}}
    with pytest.raises(ValueError, match="ownership"):
        load_generation_dispatch(run)

pytestmark = pytest.mark.asyncio


async def _owner_and_project(
    session: AsyncSession,
) -> tuple[User, Project]:
    owner = User(email=f"runs-{uuid.uuid4().hex[:8]}@example.com", password_hash="x")
    session.add(owner)
    await session.flush()
    project = Project(
        owner_id=owner.id,
        name="Single flight",
        slug=f"single-flight-{uuid.uuid4().hex[:8]}",
        template="blank",
    )
    session.add(project)
    await session.commit()
    return owner, project


async def test_same_idempotency_key_replays_and_other_key_is_blocked(
    db_session: AsyncSession,
) -> None:
    owner, project = await _owner_and_project(db_session)

    first, replayed = await reserve_generation_run(
        db_session,
        project_id=project.id,
        user_id=owner.id,
        idempotency_key="submit-11111111",
        prompt="Собери магазин",
    )
    await db_session.commit()
    assert replayed is False

    replay, replayed = await reserve_generation_run(
        db_session,
        project_id=project.id,
        user_id=owner.id,
        idempotency_key="submit-11111111",
        prompt="Собери магазин",
    )
    assert replayed is True
    assert replay.id == first.id

    with pytest.raises(ApiError) as reused:
        await reserve_generation_run(
            db_session,
            project_id=project.id,
            user_id=owner.id,
            idempotency_key="submit-11111111",
            prompt="Другой текст с тем же ключом",
        )
    assert reused.value.code == "conflict"
    assert reused.value.details == {"run_id": str(first.id)}

    with pytest.raises(ApiError) as blocked:
        await reserve_generation_run(
            db_session,
            project_id=project.id,
            user_id=owner.id,
            idempotency_key="submit-22222222",
            prompt="Второй конкурентный запуск",
        )
    assert blocked.value.code == "conflict"
    assert blocked.value.details == {
        "active_run_id": str(first.id),
        "active_message_id": None,
        "active_status": "pending",
    }


async def test_final_assistant_closes_lifecycle_gap_for_queued_prompt(
    db_session: AsyncSession,
) -> None:
    owner, project = await _owner_and_project(db_session)
    first, _ = await reserve_generation_run(
        db_session,
        project_id=project.id,
        user_id=owner.id,
        idempotency_key="submit-33333333",
        prompt="Первая",
    )
    assistant = Message(
        project_id=project.id,
        role="assistant",
        content="Готово",
        tokens_in=1,
        tokens_out=1,
    )
    db_session.add(assistant)
    await db_session.flush()
    first.assistant_message_id = assistant.id
    first.status = "running"
    await db_session.commit()

    second, replayed = await reserve_generation_run(
        db_session,
        project_id=project.id,
        user_id=owner.id,
        idempotency_key="submit-44444444",
        prompt="Вторая после llm.done",
    )
    await db_session.commit()

    assert replayed is False
    assert second.id != first.id
    await db_session.refresh(first)
    assert first.status == "completed"
    memory = await db_session.scalar(
        select(ProjectMemoryRevision).where(ProjectMemoryRevision.run_id == first.id)
    )
    assert memory is not None
    assert memory.version == 1


async def test_cancel_endpoint_marks_active_run_and_signals_redis(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner, project = await _owner_and_project(db_session)
    run = GenerationRun(
        project_id=project.id,
        user_id=owner.id,
        idempotency_key="submit-55555555",
        prompt_hash="hash",
        status="running",
    )
    db_session.add(run)
    await db_session.commit()

    async def _current_user() -> User:
        return owner

    signalled: list[uuid.UUID] = []

    async def _signal(run_id: uuid.UUID) -> None:
        signalled.append(run_id)

    async def _publish(*_args: object, **_kwargs: object) -> None:
        return None

    app.dependency_overrides[get_current_user] = _current_user
    monkeypatch.setattr(messages, "request_generation_cancel", _signal)
    monkeypatch.setattr(messages, "publish_event", _publish)
    try:
        response = await client.post(f"/api/projects/{project.id}/generation/cancel")
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    assert response.status_code == 202
    assert response.json()["id"] == str(run.id)
    assert response.json()["status"] == "cancel_requested"
    assert signalled == [run.id]


async def test_cancel_endpoint_terminalizes_queued_run_before_any_signal_or_finalizer(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner, project = await _owner_and_project(db_session)
    assistant = Message(project_id=project.id, role="assistant", content="")
    db_session.add(assistant)
    await db_session.flush()
    run = GenerationRun(
        project_id=project.id,
        user_id=owner.id,
        assistant_message_id=assistant.id,
        idempotency_key="submit-queued-cancel",
        prompt_hash="a" * 64,
        status="queued_for_capacity",
    )
    db_session.add(run)
    await db_session.flush()
    workspace = ProjectCellWorkspace(
        project_id=project.id,
        owner_id=owner.id,
        provider="docker_owner_canary",
        state="provisioning",
        generation_run_id=run.id,
    )
    db_session.add(workspace)
    await db_session.flush()
    operation = ProjectCellOperation(
        workspace_id=workspace.id,
        generation_run_id=run.id,
        idempotency_key="queued-cancel-operation",
        request_digest="b" * 64,
        fencing_epoch=1,
        kind="ensure",
        status="waiting_capacity",
        request_payload={"profile_version": "docker-owner-cell-resources-v1"},
    )
    db_session.add(operation)
    await db_session.flush()
    assistant_id = assistant.id
    run_id = run.id
    workspace_id = workspace.id
    operation_id = operation.id
    await db_session.commit()

    async def _current_user() -> User:
        return owner

    signalled: list[uuid.UUID] = []

    async def _unexpected_signal(run_id: uuid.UUID) -> None:
        signalled.append(run_id)

    async def _publish(*_args: object, **_kwargs: object) -> None:
        return None

    app.dependency_overrides[get_current_user] = _current_user
    monkeypatch.setattr(messages, "request_generation_cancel", _unexpected_signal)
    monkeypatch.setattr(messages, "publish_event", _publish)
    try:
        response = await client.post(f"/api/projects/{project.id}/generation/cancel")
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    assert response.status_code == 202
    assert response.json()["status"] == "cancelled"
    db_session.expire_all()
    cancelled_run = await db_session.get(GenerationRun, run_id)
    cancelled_message = await db_session.get(Message, assistant_id)
    cancelled_workspace = await db_session.get(ProjectCellWorkspace, workspace_id)
    cancelled_operation = await db_session.get(ProjectCellOperation, operation_id)
    assert cancelled_run is not None and cancelled_run.status == "cancelled"
    assert cancelled_message is not None and cancelled_message.tokens_out == 0
    assert "[Отменено пользователем]" in cancelled_message.content
    assert cancelled_workspace is not None
    assert cancelled_workspace.generation_run_id == run_id
    assert cancelled_operation is not None and cancelled_operation.status == "cancelled"
    assert signalled == []
    assert await recover_interrupted_generation_runs(db_session) == 0


async def test_prompt_endpoint_replays_same_submit_without_second_spawn(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner, project = await _owner_and_project(db_session)

    async def _current_user() -> User:
        return owner

    settings = SimpleNamespace(
        unlimited_generations=False,
        force_model=None,
        use_progressive_discovery=False,
        use_clarify_interview=False,
        use_auto_stack_routing=False,
        use_followup_appification=False,
        use_result_type_router=False,
    )
    spawned: list[dict[str, object]] = []

    def _spawn(**kwargs: object) -> None:
        spawned.append(kwargs)

    app.dependency_overrides[get_current_user] = _current_user
    monkeypatch.setattr(messages, "get_settings", lambda: settings)
    monkeypatch.setattr(messages, "_spawn_process_prompt", _spawn)
    monkeypatch.setattr(messages, "get_redis", lambda: _NoopRedis())
    payload = {
        "prompt": "Собери статический сайт",
        "skip_clarify": True,
        "idempotency_key": "submit-66666666",
    }
    try:
        first = await client.post(f"/api/projects/{project.id}/prompt", json=payload)
        replay = await client.post(f"/api/projects/{project.id}/prompt", json=payload)
        blocked = await client.post(
            f"/api/projects/{project.id}/prompt",
            json={**payload, "idempotency_key": "submit-77777777"},
        )
        latest = await client.get(f"/api/projects/{project.id}/generation")
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    assert first.status_code == 202
    assert replay.status_code == 202
    assert replay.json()["run_id"] == first.json()["run_id"]
    assert replay.json()["message_id"] == first.json()["message_id"]
    assert first.json()["replayed"] is False
    assert replay.json()["replayed"] is True
    assert replay.json()["run_status"] == "pending"
    assert first.json()["run_id"]
    assert len(spawned) == 1
    assert blocked.status_code == 409
    assert latest.status_code == 200
    assert latest.json()["id"] == first.json()["run_id"]
    assert latest.json()["status"] == "pending"

    rows = (
        await db_session.execute(
            Message.__table__.select()
            .where(Message.project_id == project.id)
            .order_by(Message.created_at)
        )
    ).all()
    assert len(rows) == 2


async def test_failed_first_build_explanation_does_not_spawn_another_build(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner, project = await _owner_and_project(db_session)
    failed_message = Message(
        project_id=project.id,
        role="assistant",
        content="Сборка остановлена: главный экран не создан.",
        tokens_in=0,
        tokens_out=0,
    )
    db_session.add(failed_message)
    await db_session.flush()
    failed_run = GenerationRun(
        project_id=project.id,
        user_id=owner.id,
        assistant_message_id=failed_message.id,
        idempotency_key="failed-first-build",
        prompt_hash="hash",
        status="failed",
        response_mode="build",
        error="build finished without a committed snapshot",
    )
    db_session.add(failed_run)
    await db_session.commit()

    async def _current_user() -> User:
        return owner

    settings = SimpleNamespace(
        unlimited_generations=False,
        force_model=None,
        use_progressive_discovery=False,
        use_clarify_interview=False,
        use_auto_stack_routing=False,
        use_followup_appification=False,
        use_result_type_router=False,
    )
    build_spawns: list[dict[str, object]] = []
    text_spawns: list[tuple[str, uuid.UUID]] = []

    def _spawn_build(**kwargs: object) -> None:
        build_spawns.append(kwargs)

    def _spawn_text(
        _project_id: uuid.UUID,
        _message_id: uuid.UUID,
        text: str,
        *,
        run_id: uuid.UUID,
    ) -> None:
        text_spawns.append((text, run_id))

    app.dependency_overrides[get_current_user] = _current_user
    monkeypatch.setattr(messages, "get_settings", lambda: settings)
    monkeypatch.setattr(messages, "_spawn_process_prompt", _spawn_build)
    monkeypatch.setattr(messages, "_spawn_text_turn", _spawn_text)
    monkeypatch.setattr(messages, "get_redis", lambda: _NoopRedis())
    try:
        response = await client.post(
            f"/api/projects/{project.id}/prompt",
            json={
                "prompt": "Почему прошлая сборка сломалась?",
                "idempotency_key": "explain-failed-build",
            },
        )
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    assert response.status_code == 202
    assert response.json()["mode"] == "clarify"
    assert build_spawns == []
    assert len(text_spawns) == 1
    assert "Новую сборку не запускаю" in text_spawns[0][0]
    assert "без рабочей версии" in text_spawns[0][0]


class _NoopRedis:
    async def publish(self, *_args: object, **_kwargs: object) -> int:
        return 0


async def test_tracked_prompt_cancels_the_actual_work(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_id = uuid.uuid4()
    project_id = uuid.uuid4()
    message_id = uuid.uuid4()
    work_started = asyncio.Event()
    work_cancelled = asyncio.Event()
    statuses: list[str] = []
    finalised: list[uuid.UUID] = []

    async def _work() -> None:
        work_started.set()
        try:
            await asyncio.Future()
        finally:
            work_cancelled.set()

    async def _status(_run_id: uuid.UUID, new_status: str, **_kwargs: object) -> None:
        statuses.append(new_status)

    async def _wait(_run_id: uuid.UUID) -> None:
        await work_started.wait()

    async def _finalise(
        _project_id: uuid.UUID,
        _message_id: uuid.UUID,
        finished_run_id: uuid.UUID,
    ) -> None:
        finalised.append(finished_run_id)

    async def _clear(_run_id: uuid.UUID) -> None:
        return None

    monkeypatch.setattr(messages, "set_generation_run_status", _status)
    monkeypatch.setattr(messages, "_wait_for_generation_cancel", _wait)
    monkeypatch.setattr(messages, "_finalize_cancelled_generation", _finalise)
    monkeypatch.setattr(messages, "clear_generation_cancel", _clear)

    await messages._run_tracked_prompt(
        _work(),
        run_id=run_id,
        project_id=project_id,
        assistant_message_id=message_id,
        label="test",
    )

    assert statuses == ["running"]
    assert work_cancelled.is_set()
    assert finalised == [run_id]


async def test_tracked_prompt_uses_product_outcome_finalizer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_id = uuid.uuid4()
    project_id = uuid.uuid4()
    message_id = uuid.uuid4()
    statuses: list[str] = []
    finalized: list[uuid.UUID] = []

    async def _work() -> None:
        return None

    async def _status(_run_id: uuid.UUID, new_status: str, **_: object) -> None:
        statuses.append(new_status)

    async def _finalize(finished_run_id: uuid.UUID) -> str:
        finalized.append(finished_run_id)
        return "completed"

    async def _never_cancel(_run_id: uuid.UUID) -> None:
        await asyncio.Future()

    async def _clear(_run_id: uuid.UUID) -> None:
        return None

    monkeypatch.setattr(messages, "set_generation_run_status", _status)
    monkeypatch.setattr(messages, "finalize_generation_run", _finalize)
    monkeypatch.setattr(messages, "_wait_for_generation_cancel", _never_cancel)
    monkeypatch.setattr(messages, "clear_generation_cancel", _clear)

    await messages._run_tracked_prompt(
        _work(),
        run_id=run_id,
        project_id=project_id,
        assistant_message_id=message_id,
        label="test",
    )

    assert statuses == ["running"]
    assert finalized == [run_id]


async def test_product_outcome_finalizer_never_overwrites_cancelled_build(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    finished_at = object()
    run = SimpleNamespace(
        status="cancelled",
        assistant_message_id=None,
        response_mode="build",
        finished_at=finished_at,
        error=None,
    )
    session = AsyncMock(spec=AsyncSession)
    session.get.return_value = run
    session.scalar.return_value = run
    compile_memory = AsyncMock()
    monkeypatch.setattr(
        "omnia_api.services.generation_runs.compile_terminal_run_memory",
        compile_memory,
    )

    result = await _finalize_generation_run(session, uuid.uuid4())

    assert result == "cancelled"
    assert run.status == "cancelled"
    assert run.finished_at is finished_at
    assert run.error is None
    compile_memory.assert_not_awaited()
    session.commit.assert_awaited_once()


async def test_build_without_snapshot_is_failed_product_outcome(
    db_session: AsyncSession,
) -> None:
    owner, project = await _owner_and_project(db_session)
    assistant = Message(
        project_id=project.id,
        role="assistant",
        content="Сборка прервана",
        tokens_in=0,
        tokens_out=0,
    )
    db_session.add(assistant)
    await db_session.flush()
    run = GenerationRun(
        project_id=project.id,
        user_id=owner.id,
        assistant_message_id=assistant.id,
        idempotency_key="build-no-snapshot",
        prompt_hash="hash",
        status="running",
        response_mode="build",
    )
    db_session.add(run)
    await db_session.commit()

    assert await finalize_generation_run(run.id, db_session) == "failed"

    await db_session.refresh(run)
    assert run.status == "failed"
    assert run.finished_at is not None
    assert run.error == "build finished without a committed snapshot"


async def test_build_with_snapshot_is_completed_product_outcome(
    db_session: AsyncSession,
) -> None:
    owner, project = await _owner_and_project(db_session)
    snapshot = Snapshot(
        project_id=project.id,
        commit_sha="a" * 40,
        prompt_text="Собери приложение",
        model_id="test",
    )
    db_session.add(snapshot)
    await db_session.flush()
    assistant = Message(
        project_id=project.id,
        role="assistant",
        content="Готово",
        snapshot_id=snapshot.id,
        tokens_in=1,
        tokens_out=1,
    )
    db_session.add(assistant)
    await db_session.flush()
    run = GenerationRun(
        project_id=project.id,
        user_id=owner.id,
        assistant_message_id=assistant.id,
        idempotency_key="build-with-snapshot",
        prompt_hash="hash",
        status="running",
        response_mode="build",
    )
    db_session.add(run)
    await db_session.commit()

    assert await finalize_generation_run(run.id, db_session) == "completed"

    await db_session.refresh(run)
    assert run.status == "completed"
    assert run.finished_at is not None


async def test_reconcile_historical_completed_build_without_snapshot(
    db_session: AsyncSession,
) -> None:
    owner, project = await _owner_and_project(db_session)
    assistant = Message(
        project_id=project.id,
        role="assistant",
        content="Агент остановился без результата",
        tokens_in=0,
        tokens_out=0,
    )
    db_session.add(assistant)
    await db_session.flush()
    run = GenerationRun(
        project_id=project.id,
        user_id=owner.id,
        assistant_message_id=assistant.id,
        idempotency_key="historical-false-complete",
        prompt_hash="hash",
        status="completed",
        response_mode="build",
    )
    db_session.add(run)
    await db_session.commit()

    assert await reconcile_completed_build_runs(db_session) == 1

    await db_session.refresh(run)
    assert run.status == "failed"
    assert run.error == "build finished without a committed snapshot"


async def test_startup_recovery_releases_interrupted_run(
    db_session: AsyncSession,
) -> None:
    owner, project = await _owner_and_project(db_session)
    assistant = Message(
        project_id=project.id,
        role="assistant",
        content="Частичный ответ",
    )
    db_session.add(assistant)
    await db_session.flush()
    run = GenerationRun(
        project_id=project.id,
        user_id=owner.id,
        assistant_message_id=assistant.id,
        idempotency_key="submit-88888888",
        prompt_hash="hash",
        status="running",
    )
    db_session.add(run)
    await db_session.commit()

    assert await recover_interrupted_generation_runs(db_session) == 1

    await db_session.refresh(run)
    await db_session.refresh(assistant)
    assert run.status == "failed"
    assert run.finished_at is not None
    assert assistant.tokens_out == 0
    assert "прервана перезапуском сервера" in assistant.content

    replacement, replayed = await reserve_generation_run(
        db_session,
        project_id=project.id,
        user_id=owner.id,
        idempotency_key="submit-99999999",
        prompt="Повтор после рестарта",
    )
    assert replayed is False
    assert replacement.id != run.id
