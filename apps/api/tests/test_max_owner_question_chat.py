import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker

from yleum_api.core.config import get_settings
from yleum_api.core.db import get_session
from yleum_api.core.security import create_access_token
from yleum_api.main import app
from yleum_api.models.account import AuthSession
from yleum_api.models.billing import PRO_PLAN_ID, Subscription
from yleum_api.models.generation_billing import GenerationBillingIntent, GenerationBillingPolicy
from yleum_api.models.generation_credits import GenerationCreditReservation
from yleum_api.models.generation_run import GenerationRun
from yleum_api.models.message import Message
from yleum_api.models.project import Project
from yleum_api.models.snapshot import Snapshot
from yleum_api.models.user import User
from yleum_api.schemas.message import PromptResponse
from yleum_api.services import max_owner_diagnosis, repo
from yleum_api.services.accounts import create_account
from yleum_api.services.discovery import ASK, DiscoveryResult
from yleum_api.services.generation import acceptance
from yleum_api.services.generation_runs import prompt_hash

QUESTION = "Почему приложение не отвечает?"
ANSWER = "По имеющимся данным причину сбоя подтвердить не удалось."


class _NoopRedis:
    async def publish(self, *_args):
        pass


@pytest.fixture
def diagnosis_answer(monkeypatch):
    async def read_answer(_session, _project, _user_id, _prompt):
        return ANSWER

    monkeypatch.setattr(max_owner_diagnosis, "answer_owner_question", read_answer)


@pytest.fixture
async def workspace(db_session, test_engine, monkeypatch):
    owner = await create_account(
        db_session, email="max-question@example.test", password_hash="unused",
        email_verified_at=None, consent=None,
    )
    owner.free_generations_used = 100
    auth = AuthSession(user_id=owner.id, expires_at=datetime.now(UTC) + timedelta(days=1))
    project = Project(owner_id=owner.id, name="MAX chat", slug="max-chat", template="max_miniapp")
    db_session.add_all([auth, project])
    await db_session.commit()

    session_factory = async_sessionmaker(test_engine, expire_on_commit=False)

    async def request_session():
        async with session_factory() as session:
            yield session

    monkeypatch.setitem(app.dependency_overrides, get_session, request_session)
    token = create_access_token(owner.id, session_id=auth.id, session_version=owner.session_version)
    headers = {"Origin": get_settings().web_base_url, "Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver", headers=headers,
    ) as client:
        yield client, project, owner


async def test_question_is_saved_as_chat_while_build_is_running(workspace, db_session):
    client, project, owner = workspace
    run = GenerationRun(
        id=uuid4(), project_id=project.id, user_id=owner.id, status="running",
        idempotency_key="active-build-key", prompt_hash="active-build-hash",
    )
    db_session.add(run)
    await db_session.commit()
    url = f"/api/projects/{project.id}"
    prompt = "Сколько ждать сборку?"

    response = await client.post(
        f"{url}/chat/question", json={"prompt": prompt, "idempotency_key": "first-question"},
    )

    assert response.status_code == 200, response.text
    answer = response.json()
    assert answer["answered"] is True
    assert "идёт сборка" in answer["answer"]

    history = (await client.get(f"{url}/messages")).json()
    assert [(row["id"], row["role"], row["content"]) for row in history] == [
        (answer["user_message_id"], "user", prompt),
        (answer["assistant_message_id"], "assistant", answer["answer"]),
    ]
    assert all(row["chat_question"] is True and row["generation_status"] is None for row in history)
    assert history[1]["tokens_out"] == 0
    assert history[1]["snapshot_id"] is None
    active = (await client.get(f"{url}/generation")).json()
    assert active["id"] == str(run.id) and active["status"] == "running"
    assert await db_session.scalar(select(func.count()).select_from(GenerationRun)) == 1
    assert await db_session.scalar(select(func.count()).select_from(
        GenerationCreditReservation,
    )) == 0
    assert await db_session.scalar(select(func.count()).select_from(GenerationBillingPolicy)) == 0
    await db_session.refresh(run)
    await db_session.refresh(owner)
    assert run.status == "running"
    assert owner.free_chat_messages_used == 0


async def test_legacy_prompt_refuses_new_question_before_creating_run(workspace, db_session):
    client, project, _owner = workspace

    response = await client.post(
        f"/api/projects/{project.id}/prompt", json={"prompt": QUESTION},
    )

    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "project_question_required"
    assert await db_session.scalar(select(func.count()).select_from(GenerationRun)) == 0
    assert await db_session.scalar(select(func.count()).select_from(Message)) == 0


async def test_owner_can_read_saved_setup_from_chat_without_a_paid_edit(workspace, db_session):
    client, project, _owner = workspace
    guide = (
        "# Настройка расписания владельцем\n\n"
        "1. Откройте Каталог и нажмите «Добавить позицию».\n"
        "2. Выберите раздел «Расписание» и укажите дату в названии.\n"
        "3. Например, 2040-01-15T14:30+03:00.\n"
    )
    commit = repo.init_from_files(project.id, {"WORKSHOP.md": guide}, "Workshop setup")
    snapshot = Snapshot(project_id=project.id, commit_sha=commit)
    db_session.add(snapshot)
    await db_session.flush()
    project.current_snapshot_id = snapshot.id
    await db_session.commit()

    response = await client.post(f"/api/projects/{project.id}/chat/question", json={
        "prompt": "Как мне как владельцу открыть ещё одно свободное время в расписании?",
        "idempotency_key": "workshop-owner-setup",
    })

    assert response.status_code == 200, response.text
    answer = response.json()
    assert answer["answered"] and "2040-01-15T14:30+03:00" in answer["answer"]
    assert "Расписание" in answer["answer"] and "AITunnel" not in answer["answer"]
    assert await db_session.scalar(select(func.count()).select_from(GenerationRun)) == 0
    assert await db_session.scalar(select(func.count()).select_from(
        GenerationCreditReservation,
    )) == 0
    assert await db_session.scalar(select(func.count()).select_from(GenerationBillingIntent)) == 0


async def test_exact_question_replay_keeps_messages_and_conflicts_with_edits(
    workspace, db_session, diagnosis_answer, monkeypatch,
):
    client, project, _owner = workspace
    url = f"/api/projects/{project.id}"
    payload = {"prompt": QUESTION, "idempotency_key": "report-replay-key"}
    first = await client.post(f"{url}/chat/question", json=payload)
    assert first.status_code == 200, first.text

    async def unavailable(*_args):
        raise RuntimeError("The same question must use the saved answer")

    monkeypatch.setattr(max_owner_diagnosis, "answer_owner_question", unavailable)
    replay = await client.post(f"{url}/chat/question", json=payload)
    assert replay.status_code == 200 and replay.json() == first.json()

    for path, prompt in (
        ("chat/question", "Что случилось с каталогом?"),
        ("chat/question", "Почини сортировку каталога."),
        ("prompt", "Почини сортировку каталога."),
    ):
        conflict = await client.post(f"{url}/{path}", json={**payload, "prompt": prompt})
        assert conflict.status_code == 409, conflict.text
        assert conflict.json()["error"]["code"] == "idempotency_conflict"
    assert await db_session.scalar(select(func.count()).select_from(GenerationRun)) == 0
    assert await db_session.scalar(select(func.count()).select_from(Message)) == 2


async def test_legacy_build_replays_by_key_when_its_text_now_looks_like_a_question(
    workspace, db_session,
):
    client, project, owner = workspace
    url = f"/api/projects/{project.id}"
    key = "legacy-accepted-key"
    accepted = PromptResponse(run_id=uuid4(), message_id=uuid4(), run_status="completed")
    user_message = Message(project_id=project.id, role="user", content=QUESTION)
    assistant_message = Message(
        id=accepted.message_id, project_id=project.id, role="assistant", content="Earlier result",
        tokens_out=0,
    )
    db_session.add_all([user_message, assistant_message])
    await db_session.flush()
    db_session.add(GenerationRun(
        id=accepted.run_id, project_id=project.id, user_id=owner.id,
        status="completed", idempotency_key=key, prompt_hash=prompt_hash(QUESTION),
        user_message_id=user_message.id, assistant_message_id=assistant_message.id,
        response_payload=accepted.model_dump(mode="json"),
    ))
    await db_session.commit()

    payload = {"prompt": QUESTION, "idempotency_key": key}
    response = await client.post(f"{url}/prompt", json=payload)
    assert response.status_code == 202, response.text
    assert response.json() == accepted.model_copy(update={"replayed": True}).model_dump(mode="json")

    conflict = await client.post(f"{url}/chat/question", json=payload)
    assert conflict.status_code == 409, conflict.text
    assert conflict.json()["error"]["code"] == "idempotency_conflict"
    assert await db_session.scalar(select(func.count()).select_from(Message)) == 2


async def test_edit_preflight_preserves_standard_admission_and_its_separate_limit(
    workspace, db_session, monkeypatch, diagnosis_answer,
):
    client, project, owner = workspace
    owner.free_generations_used = 0
    await db_session.commit()
    settings = get_settings().model_copy(update={
        "prompt_rate_limit": "1/minute", "use_generation_worker": False,
        "use_progressive_discovery": False, "use_clarify_interview": False,
    })
    monkeypatch.setattr("yleum_api.core.ratelimit.get_settings", lambda: settings)
    monkeypatch.setattr(acceptance, "get_settings", lambda: settings)
    spawned = []
    monkeypatch.setattr(
        acceptance, "_spawn_process_prompt", lambda **kwargs: spawned.append(kwargs),
    )

    monkeypatch.setattr(acceptance, "get_redis", _NoopRedis)
    payload = {"prompt": "Почини сортировку каталога.", "idempotency_key": "edit-fallback"}
    url = f"/api/projects/{project.id}"
    declined = await client.post(f"{url}/chat/question", json=payload)
    assert declined.status_code == 200 and declined.json() == {"answered": False}
    question = {"prompt": QUESTION, "idempotency_key": "free-chat-first"}
    answered = await client.post(f"{url}/chat/question", json=question)
    assert answered.status_code == 200 and answered.json()["answered"]
    replay = await client.post(f"{url}/chat/question", json=question)
    assert replay.json() == answered.json()
    throttled = await client.post(f"{url}/chat/question", json={
        **question, "idempotency_key": "free-chat-second",
    })
    assert throttled.status_code == 429 and throttled.json()["error"]["code"] == "rate_limited"
    after_limit = await client.post(f"{url}/chat/question", json=payload)
    assert after_limit.status_code == 200 and after_limit.json() == {"answered": False}
    assert await db_session.scalar(select(func.count()).select_from(GenerationRun)) == 0
    assert await db_session.scalar(select(func.count()).select_from(Message)) == 2

    edit = await client.post(f"{url}/prompt", json=payload)
    assert edit.status_code == 202, edit.text
    assert len(spawned) == 1


@pytest.mark.parametrize("progressive", [False, True])
async def test_first_build_interview_ignores_prior_help_messages(
    workspace, db_session, diagnosis_answer, monkeypatch, progressive,
):
    client, project, owner = workspace
    sub = await db_session.scalar(select(Subscription).where(Subscription.user_id == owner.id))
    sub.plan_id = PRO_PLAN_ID
    now = datetime.now(UTC)
    sub.current_period_start = now - timedelta(days=1)
    sub.current_period_end = now + timedelta(days=28)
    await db_session.commit()
    settings = get_settings().model_copy(update={
        "use_generation_worker": False, "use_progressive_discovery": progressive,
        "use_batch_discovery": False, "use_clarify_interview": True, "unlimited_generations": True,
    })
    monkeypatch.setattr("yleum_api.core.config.get_settings", lambda: settings)
    monkeypatch.setattr(acceptance, "get_settings", lambda: settings)
    monkeypatch.setattr(acceptance, "get_redis", _NoopRedis)
    monkeypatch.setattr(acceptance, "_spawn_text_turn", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(acceptance, "_spawn_clarify", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(acceptance, "_spawn_process_prompt", lambda **_kwargs: None)

    async def discover(history, _prompt, *, asked_count, **_kwargs):
        assert history == [] and asked_count == 0
        return DiscoveryResult(
            action=ASK, message="Кто будет пользоваться каталогом?", brief="", stack="nextjs",
        )

    monkeypatch.setattr(acceptance, "run_discovery", discover)
    url = f"/api/projects/{project.id}"
    help_response = await client.post(f"{url}/chat/question", json={
        "prompt": QUESTION, "idempotency_key": "help-before-first-build",
    })
    assert help_response.status_code == 200
    response = await client.post(f"{url}/prompt", json={
        "prompt": "Создай каталог мебели", "idempotency_key": "first-build-interview",
    })
    assert response.status_code == 202, response.text
    assert response.json()["mode"] == "clarify"


async def test_history_can_keep_the_build_turn_after_owner_questions_fill_the_page(
    workspace, db_session,
):
    client, project, _owner = workspace
    start = datetime.now(UTC)
    for index, (content, is_question) in enumerate((
        ("Добавь корзину", False), ("Работа остановилась", False),
        ("Что случилось?", True), ("Проверь настройки", True),
    )):
        db_session.add(Message(
            project_id=project.id, role="assistant" if index % 2 else "user",
            content=content, chat_question=is_question, created_at=start + timedelta(seconds=index),
        ))
    await db_session.commit()
    url = f"/api/projects/{project.id}/messages"
    ordinary = await client.get(url, params={"limit": 2})
    assert [msg["chat_question"] for msg in ordinary.json()] == [True, True]
    contextual = await client.get(url, params={"limit": 2, "include_generation_context": True})
    assert [msg["content"] for msg in contextual.json()] == [
        "Добавь корзину", "Работа остановилась", "Что случилось?", "Проверь настройки",
    ]


async def test_question_diagnosis_timeout_preserves_retry_without_a_generation(
    workspace, db_session, monkeypatch,
):
    from yleum_api.services import max_owner_question_chat

    client, project, _owner = workspace

    async def unavailable(*_args):
        await asyncio.Future()

    monkeypatch.setattr(max_owner_diagnosis, "answer_owner_question", unavailable)
    monkeypatch.setattr(max_owner_question_chat, "DIAGNOSIS_TIMEOUT_SECONDS", 0.05)
    payload = {"prompt": QUESTION, "idempotency_key": "retry-after-timeout"}
    url = f"/api/projects/{project.id}/chat/question"
    error = await client.post(url, json=payload)
    assert error.status_code == 503, error.text
    assert error.json()["error"]["code"] == "advice_unavailable"
    assert await db_session.scalar(select(func.count()).select_from(GenerationRun)) == 0
    assert await db_session.scalar(select(func.count()).select_from(Message)) == 0

    async def read_answer(*_args):
        return ANSWER

    monkeypatch.setattr(max_owner_diagnosis, "answer_owner_question", read_answer)
    retry = await client.post(url, json=payload)
    assert retry.status_code == 200 and retry.json()["answer"] == ANSWER


async def test_question_access_and_credential_rejection_leave_no_chat(workspace, db_session):
    client, project, _owner = workspace
    foreign = User()
    db_session.add(foreign)
    await db_session.flush()
    other_project = Project(owner_id=foreign.id, name="Foreign", slug="unrelated", template="blank")
    db_session.add(other_project)
    await db_session.commit()
    payload = {"prompt": QUESTION, "idempotency_key": "access-control-key"}

    not_owned = await client.post(f"/api/projects/{other_project.id}/chat/question", json=payload)
    assert not_owned.status_code == 404 and not_owned.json()["error"]["code"] == "not_found"
    project.template = "blank"
    await db_session.commit()
    url = f"/api/projects/{project.id}/chat/question"
    wrong_template = await client.post(url, json=payload)
    assert wrong_template.status_code == 409
    assert wrong_template.json()["error"]["code"] == "max_project_required"
    unauthenticated = await client.post(url, headers={"Authorization": ""}, json=payload)
    assert unauthenticated.status_code == 401

    project.template = "max_miniapp"
    await db_session.commit()
    fixture_token = "sk-" + "SyntheticSecretNotReal00000001"
    rejected = await client.post(url, json={**payload, "prompt": f"{QUESTION} {fixture_token}"})
    assert rejected.status_code == 400, rejected.text
    assert rejected.json()["error"]["code"] == "integration_credentials_invalid"
    assert fixture_token not in rejected.text
    assert await db_session.scalar(select(func.count()).select_from(Message)) == 0
    assert await db_session.scalar(select(func.count()).select_from(GenerationRun)) == 0


async def test_diagnoses_do_not_lock_generation_or_exhaust_the_connection_pool(
    workspace, db_session, test_engine, monkeypatch,
):
    client, first_project, owner = workspace
    second_project = Project(owner_id=owner.id, name="MAX 2", slug="max-2", template="max_miniapp")
    third_project = Project(owner_id=owner.id, name="MAX 3", slug="max-3", template="max_miniapp")
    db_session.add_all([second_project, third_project])
    await db_session.commit()
    pending_projects = {first_project.id, second_project.id}
    first_started = asyncio.Event()
    both_started = asyncio.Event()
    finish = asyncio.Event()

    async def read_answer(_session, project, _user_id, _prompt):
        if project.id in pending_projects:
            pending_projects.remove(project.id)
            if project.id == first_project.id:
                first_started.set()
            if not pending_projects:
                both_started.set()
            await finish.wait()
        return ANSWER

    monkeypatch.setattr(max_owner_diagnosis, "answer_owner_question", read_answer)

    async def ask(project):
        return await client.post(f"/api/projects/{project.id}/chat/question", json={
            "prompt": QUESTION, "idempotency_key": "pending-concurrent-question",
        })

    tasks = [asyncio.create_task(ask(first_project))]
    try:
        await asyncio.wait_for(first_started.wait(), timeout=5)
        duplicate = await ask(first_project)
        assert duplicate.status_code == 409, duplicate.text
        assert duplicate.json()["error"]["details"]["reason"] == "chat_question_busy"
        tasks.append(asyncio.create_task(ask(second_project)))
        await asyncio.wait_for(both_started.wait(), timeout=5)
        async with test_engine.begin() as connection:
            assert await connection.scalar(
                text("SELECT pg_try_advisory_xact_lock(hashtext(:project_id))"),
                {"project_id": str(first_project.id)},
            ) is True
        busy = await ask(third_project)
        assert busy.status_code == 429, busy.text
        assert busy.json()["error"]["details"]["retryable"] is True
    finally:
        finish.set()
        completed = await asyncio.gather(*tasks)

    assert all(response.status_code == 200 for response in completed)
    replay = await ask(first_project)
    assert replay.status_code == 200 and replay.json() == completed[0].json()
    retry = await ask(third_project)
    assert retry.status_code == 200 and retry.json()["answer"] == ANSWER
