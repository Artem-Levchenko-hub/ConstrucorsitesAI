from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from omnia_api.core.deps import get_current_user
from omnia_api.core.errors import ApiError
from omnia_api.main import app
from omnia_api.models.generation_run import GenerationRun
from omnia_api.models.message import Message
from omnia_api.models.project import Project
from omnia_api.models.snapshot import Snapshot
from omnia_api.models.user import User
from omnia_api.routers import messages
from omnia_api.services.generation_runs import (
    _acquire_generation_lock,
    finalize_generation_run,
    latest_failed_agent_state,
    prompt_hash,
    reconcile_completed_build_runs,
    recover_interrupted_generation_runs,
    reserve_generation_run,
    save_generation_agent_state,
    set_generation_run_error,
)

pytestmark = pytest.mark.asyncio


async def test_product_spec_is_part_of_idempotency_hash() -> None:
    prompt = "Собери магазин"
    first = {"purpose": "Каталог", "screens": ["Главная", "Корзина"]}
    same_reordered = {"screens": ["Главная", "Корзина"], "purpose": "Каталог"}
    changed = {"purpose": "Каталог", "screens": ["Главная", "Избранное"]}

    assert prompt_hash(prompt, first) == prompt_hash(prompt, same_reordered)
    assert prompt_hash(prompt, first) != prompt_hash(prompt, changed)
    assert prompt_hash(prompt) != prompt_hash(prompt, first)


class _LockResult:
    def __init__(self, acquired: bool) -> None:
        self.acquired = acquired

    def scalar_one(self) -> bool:
        return self.acquired


class _LockSession:
    def __init__(self, acquired: bool) -> None:
        self.acquired = acquired
        self.calls: list[tuple[object, dict[str, str]]] = []

    async def execute(self, statement: object, params: dict[str, str]) -> _LockResult:
        self.calls.append((statement, params))
        return _LockResult(self.acquired)


async def test_generation_lock_uses_non_blocking_advisory_lock() -> None:
    project_id = uuid.uuid4()
    session = _LockSession(True)

    await _acquire_generation_lock(session, project_id)  # type: ignore[arg-type]

    assert len(session.calls) == 1
    statement, params = session.calls[0]
    assert "pg_try_advisory_xact_lock" in str(statement)
    assert params == {"project_id": str(project_id)}


async def test_generation_lock_reports_retryable_project_busy_conflict() -> None:
    session = _LockSession(False)

    with pytest.raises(ApiError) as blocked:
        await _acquire_generation_lock(session, uuid.uuid4())  # type: ignore[arg-type]

    assert blocked.value.code == "conflict"
    assert blocked.value.status_code == 409
    assert blocked.value.details == {"reason": "project_busy"}


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


async def test_same_key_rejects_changed_product_spec(db_session: AsyncSession) -> None:
    owner, project = await _owner_and_project(db_session)
    first_spec = {"purpose": "Каталог", "screens": ["Главная", "Корзина"]}

    run, replayed = await reserve_generation_run(
        db_session,
        project_id=project.id,
        user_id=owner.id,
        idempotency_key="product-spec-11111111",
        prompt="Собери приложение",
        product_spec=first_spec,
    )
    await db_session.commit()
    assert replayed is False

    with pytest.raises(ApiError) as reused:
        await reserve_generation_run(
            db_session,
            project_id=project.id,
            user_id=owner.id,
            idempotency_key="product-spec-11111111",
            prompt="Собери приложение",
            product_spec={"purpose": "Каталог", "screens": ["Главная", "Избранное"]},
        )

    assert reused.value.code == "conflict"
    assert reused.value.details == {"run_id": str(run.id)}


async def test_primary_generation_failure_is_not_overwritten(
    db_session: AsyncSession,
) -> None:
    owner, project = await _owner_and_project(db_session)
    run = GenerationRun(
        project_id=project.id,
        user_id=owner.id,
        idempotency_key="primary-failure",
        prompt_hash="hash",
        status="running",
    )
    db_session.add(run)
    await db_session.commit()

    first = await set_generation_run_error(
        run.id,
        "agent_stopped:provider_rejected_red",
        session=db_session,
    )
    primary = await set_generation_run_error(
        run.id,
        "final_verification_failed",
        session=db_session,
    )
    await db_session.refresh(run)

    assert first == "agent_stopped:provider_rejected_red"
    assert primary == "agent_stopped:provider_rejected_red"
    assert run.error == "agent_stopped:provider_rejected_red"

    replaced = await set_generation_run_error(
        run.id,
        "administrative_repair",
        preserve_existing=False,
        session=db_session,
    )
    await db_session.refresh(run)
    assert replaced == "administrative_repair"
    assert run.error == "administrative_repair"


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

    async def _not_deployed(_project_id: uuid.UUID) -> dict[str, object]:
        return {}

    app.dependency_overrides[get_current_user] = _current_user
    monkeypatch.setattr(messages, "get_settings", lambda: settings)
    monkeypatch.setattr(messages, "_spawn_process_prompt", _spawn)
    monkeypatch.setattr(messages, "get_redis", lambda: _NoopRedis())
    monkeypatch.setattr(messages.orchestrator_client, "get_deploy", _not_deployed)
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


async def test_tracked_prompt_deadline_always_terminalizes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_id = uuid.uuid4()
    project_id = uuid.uuid4()
    message_id = uuid.uuid4()
    statuses: list[tuple[str, str | None]] = []
    emergency: list[str] = []

    async def _work() -> None:
        await asyncio.Future()

    async def _never_cancel(_run_id: uuid.UUID) -> None:
        await asyncio.Future()

    async def _status(_run_id: uuid.UUID, new_status: str, *, error: str | None = None) -> None:
        statuses.append((new_status, error))

    async def _error(_project_id: uuid.UUID, _message_id: uuid.UUID, err: str) -> None:
        emergency.append(err)

    async def _clear(_run_id: uuid.UUID) -> None:
        return None

    monkeypatch.setattr(messages, "set_generation_run_status", _status)
    monkeypatch.setattr(messages, "_wait_for_generation_cancel", _never_cancel)
    monkeypatch.setattr(messages, "_emergency_error", _error)
    monkeypatch.setattr(messages, "clear_generation_cancel", _clear)
    monkeypatch.setattr(
        messages,
        "get_settings",
        lambda: SimpleNamespace(agent_builder_max_runtime_seconds=-29),
    )

    await messages._run_tracked_prompt(
        _work(),
        run_id=run_id,
        project_id=project_id,
        assistant_message_id=message_id,
        label="deadline-test",
    )

    assert statuses == [
        ("running", None),
        ("failed", "generation_deadline_exceeded"),
    ]
    assert emergency and "безопасно остановлена" in emergency[0]


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


async def test_failed_run_keeps_observable_checkpoint_for_retry(
    db_session: AsyncSession,
) -> None:
    owner, project = await _owner_and_project(db_session)
    run = GenerationRun(
        project_id=project.id,
        user_id=owner.id,
        idempotency_key="checkpoint-recovery",
        prompt_hash="hash",
        status="running",
    )
    db_session.add(run)
    await db_session.commit()

    state: dict[str, object] = {
        "objective": "Собрать MAX-приложение",
        "steps": [{"id": "step-1", "title": "Собрать экран", "status": "completed"}],
        "last_tool": "build",
        "last_summary": "Build clean",
        "next_action": "Проверить runtime",
    }
    await save_generation_agent_state(run.id, state, db_session)
    assert await recover_interrupted_generation_runs(db_session) == 1

    recovered = await latest_failed_agent_state(
        db_session,
        project_id=project.id,
    )

    assert recovered == state
