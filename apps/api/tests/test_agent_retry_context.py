"""Retry resolution executes real history queries and the real prompt builder.

Only storage is adapted: an in-memory SQLite schema uses portable column types
and a synchronous session behind the async interface. No provider is contacted.
"""

from datetime import datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import JSON, Column, MetaData, Table, create_engine, delete
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Session

from yleum_api.models.generation_run import GenerationRun
from yleum_api.models.message import Message
from yleum_api.models.snapshot import Snapshot
from yleum_api.services.generation.agent_preparation import classify_agent_turn
from yleum_api.services.generation.agent_prompt import prepare_agent_prompt
from yleum_api.services.generation.contracts import (
    GenerationIds,
    GenerationRuntime,
    ProjectGenerationFacts,
    SourceBaseline,
    StackPrompt,
)


@pytest.fixture
def history():
    engine = create_engine("sqlite://")
    metadata = MetaData()
    for model in (GenerationRun, Message, Snapshot):
        Table(
            model.__tablename__, metadata,
            *(
                Column(
                    column.name,
                    JSON() if isinstance(column.type, JSONB) else column.type,
                    primary_key=column.primary_key,
                )
                for column in model.__table__.columns
            ),
        )
    metadata.create_all(engine)
    project_id, user_id = uuid4(), uuid4()
    session = Session(engine)
    moment = datetime(2026, 9, 28, 10)

    class AsyncSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, *args):
            return session.get(*args)

        async def scalar(self, statement):
            return session.scalar(statement)

        async def scalars(self, statement):
            return session.scalars(statement)

    def add(text, *, status="failed", mode="build", project=None, user=None, at=None):
        nonlocal moment
        moment += timedelta(seconds=1)
        message = Message(
            id=uuid4(), project_id=project or project_id, role="user", content=text,
            created_at=at or moment,
        )
        run = GenerationRun(
            id=uuid4(), project_id=project or project_id, user_id=user or user_id,
            user_message_id=message.id, status=status, response_mode=mode,
            idempotency_key=str(uuid4()), prompt_hash="fixture", created_at=at or moment,
        )
        session.add_all([message, run])
        session.commit()
        return run

    async def classify(run):
        message = session.get(Message, run.user_message_id)
        ids = GenerationIds(run.id, project_id, user_id, message.id, uuid4())
        facts = ProjectGenerationFacts(
            "max_miniapp", "fixture", "Fixture", None, None, False, "ru", False, "", ""
        )
        result = await classify_agent_turn(
            baseline=SourceBaseline(uuid4(), "baseline", {"src/page.tsx": "existing"}),
            factory=AsyncSession,
            ids=ids,
            orchestrate=False,
            project_info=facts,
            prompt_text=message.content,
        )
        plan, _ = await prepare_agent_prompt(
            stack=StackPrompt("", None, None, None, "fixture", False),
            factory=AsyncSession,
            ids=ids,
            project_info=facts,
            prompt_text=result.prompt,
            runtime=GenerationRuntime(),
            orchestrate=False,
            selected_elements=None,
            _is_edit=result.is_edit,
            _is_continue=result.is_continue,
            force_model="fixture-model",
        )
        return result, plan.user, message.content

    session.add(Snapshot(id=uuid4(), project_id=project_id, prompt_text="Original app"))
    session.commit()
    yield SimpleNamespace(add=add, classify=classify, session=session)
    session.close()
    engine.dispose()


@pytest.mark.parametrize(
    "retry", ["попробуй ещё раз", "попробуй ещё рах", "Попробуй еще раз!", "try again"]
)
@pytest.mark.parametrize("status", ["failed", "cancelled"])
@pytest.mark.parametrize("mode", ["build", "edit"])
async def test_retry_passes_full_failed_task_to_model_without_changing_chat(
    history, retry, status, mode
):
    task = "Добавь журнал расчётов. " + "Сохрани существующие результаты. " * 40
    history.add(task, status=status, mode=mode)
    current = history.add(retry, status="running", mode="edit")

    result, model_prompt, original_chat = await history.classify(current)

    assert result.prompt == task
    assert task in model_prompt
    assert original_chat == retry
    assert result.is_edit and not result.is_continue


@pytest.mark.parametrize("original_mode", ["build", "edit"])
async def test_retry_chain_uses_nearest_substantive_failed_task(history, original_mode):
    history.add("Добавь старую задачу")
    task = "Исправь формулу и покажи журнал расчётов"
    history.add(task, mode=original_mode)
    history.add("попробуй ещё раз", mode="edit")
    history.add("попробуй ещё рах", status="cancelled", mode="edit")
    current = history.add("try again", status="running", mode="edit")
    # A later failed request must not affect this run's historical context.
    history.add("Добавь совершенно другую функцию")

    result, model_prompt, _ = await history.classify(current)

    assert result.prompt == task
    assert task in model_prompt
    assert "старую задачу" not in model_prompt
    assert "совершенно другую" not in model_prompt


@pytest.mark.parametrize(
    "status,mode",
    [
        ("completed", "build"), ("running", "build"), ("failed", "chat"),
        ("completed", "edit"), ("running", "edit"), ("failed", "clarify"),
    ],
)
async def test_retry_does_not_reach_past_newer_unrelated_or_unfinished_turn(history, status, mode):
    history.add("Добавь старую задачу")
    history.add("Новая задача", status=status, mode=mode)
    current = history.add("попробуй ещё раз", status="running")

    result, model_prompt, _ = await history.classify(current)

    assert result.prompt == "попробуй ещё раз"
    assert "старую задачу" not in model_prompt


@pytest.mark.parametrize("other_owner", ["project", "user"])
async def test_retry_cannot_recover_another_project_or_users_task(history, other_owner):
    history.add("Private foreign task", **{other_owner: uuid4()})
    current = history.add("попробуй ещё раз", status="running")

    result, model_prompt, _ = await history.classify(current)

    assert result.prompt == "попробуй ещё раз"
    assert "Private foreign task" not in model_prompt


@pytest.mark.parametrize(
    "prompt", ["Добавь экспорт", "Попробуй ещё раз и добавь экспорт", "try again with red buttons"]
)
async def test_explicit_new_request_is_not_replaced_with_failed_history(history, prompt):
    history.add("Добавь старую задачу")
    current = history.add(prompt, status="running")

    result, model_prompt, _ = await history.classify(current)

    assert result.prompt == prompt
    assert prompt in model_prompt
    assert "старую задачу" not in model_prompt


async def test_retry_without_history_is_left_unchanged(history):
    current = history.add("попробуй ещё раз", status="running")
    result, _, _ = await history.classify(current)
    assert result.prompt == "попробуй ещё раз"


async def test_retry_chain_is_bounded(history):
    history.add("Добавь слишком давнюю задачу")
    for _ in range(20):
        history.add("попробуй ещё раз")
    current = history.add("попробуй ещё раз", status="running")

    result, model_prompt, _ = await history.classify(current)

    assert result.prompt == "попробуй ещё раз"
    assert "слишком давнюю" not in model_prompt


async def test_retry_does_not_guess_order_of_equal_timestamps(history):
    failed = history.add("Добавь журнал расчётов")
    current = history.add("попробуй ещё раз", status="running", at=failed.created_at)

    result, model_prompt, _ = await history.classify(current)

    assert result.prompt == "попробуй ещё раз"
    assert "журнал расчётов" not in model_prompt


async def test_retry_rejects_foreign_message_reference_on_own_run(history):
    foreign = history.add("Private foreign task", project=uuid4())
    failed = history.add("Original own task")
    failed.user_message_id = foreign.user_message_id
    history.session.commit()
    current = history.add("попробуй ещё раз", status="running")

    result, model_prompt, _ = await history.classify(current)

    assert result.prompt == "попробуй ещё раз"
    assert "Private foreign task" not in model_prompt


async def test_first_build_continue_still_recovers_original_brief(history):
    history.session.execute(delete(Snapshot))
    history.session.commit()
    task = "Собери приложение с журналом расчётов"
    history.add(task)
    current = history.add("продолжи", status="running")

    result, model_prompt, original_chat = await history.classify(current)

    assert result.prompt == task
    assert task in model_prompt
    assert not result.is_edit and not result.is_continue
    assert original_chat == "продолжи"
