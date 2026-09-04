from __future__ import annotations

import asyncio
import json
import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from omnia_api.models.generation_run import GenerationRun
from omnia_api.models.project import Project
from omnia_api.models.user import User
from omnia_api.services.generation_events import (
    append_generation_event,
    persist_and_publish_generation_event,
    replay_generation_events,
)

pytestmark = pytest.mark.asyncio


async def _new_user(session: AsyncSession, label: str) -> User:
    user = User(email=f"events-{label}-{uuid.uuid4().hex}@example.com", password_hash="x")
    session.add(user)
    await session.flush()
    return user


async def _new_project(session: AsyncSession, owner: User) -> Project:
    project = Project(
        owner_id=owner.id,
        name="Events test",
        slug=f"events-{uuid.uuid4().hex}",
        template="blank",
    )
    session.add(project)
    await session.flush()
    return project


async def _new_run(session: AsyncSession, owner: User, project: Project) -> GenerationRun:
    run = GenerationRun(
        project_id=project.id,
        user_id=owner.id,
        idempotency_key=f"events-run:{uuid.uuid4().hex}",
        prompt_hash="a" * 64,
        status="running",
        agent_state={},
    )
    session.add(run)
    await session.flush()
    return run


async def test_generation_event_sequence_is_gap_free_per_run(db_session: AsyncSession) -> None:
    owner = await _new_user(db_session, "owner")
    project = await _new_project(db_session, owner)
    run = await _new_run(db_session, owner, project)

    first = await append_generation_event(
        db_session,
        run_id=run.id,
        project_id=project.id,
        message_id=None,
        event_type="generation.phase",
        payload={"phase": "edit"},
    )
    second = await append_generation_event(
        db_session,
        run_id=run.id,
        project_id=project.id,
        message_id=None,
        event_type="generation.phase",
        payload={"phase": "fast_check"},
    )

    assert (first.seq, second.seq) == (1, 2)
    replayed = await replay_generation_events(db_session, run_id=run.id, after_seq=0)
    assert [event.seq for event in replayed] == [1, 2]


async def test_event_is_committed_before_publish_failure(
    test_engine,
) -> None:
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    async with factory() as session:
        owner = await _new_user(session, "publisher")
        project = await _new_project(session, owner)
        run = await _new_run(session, owner, project)
        await session.commit()

    async def broken_publisher(
        _project_id: str | object,
        _event_type: str,
        _data: dict[str, object],
    ) -> None:
        raise RuntimeError("redis down")

    with pytest.raises(RuntimeError, match="redis down"):
        await persist_and_publish_generation_event(
            session_factory=factory,
            run_id=run.id,
            project_id=project.id,
            message_id=None,
            event_type="tool.started",
            payload={"operation_id": "op-1", "phase": "full_build"},
            publisher=broken_publisher,
        )

    async with factory() as check_session:
        replayed = await replay_generation_events(
            check_session,
            run_id=run.id,
            after_seq=0,
        )
        assert len(replayed) == 1
        assert replayed[0].event_type == "tool.started"


async def test_event_payload_is_redacted_and_bounded(db_session: AsyncSession) -> None:
    owner = await _new_user(db_session, "redaction")
    project = await _new_project(db_session, owner)
    run = await _new_run(db_session, owner, project)

    event = await append_generation_event(
        db_session,
        run_id=run.id,
        project_id=project.id,
        message_id=None,
        event_type="tool.finished",
        payload={
            "authorization": "Bearer private-value",
            "detail": "DATABASE_URL=postgresql://app:db-password@db/app\n" + ("x" * 50_000),
        },
    )
    serialized = json.dumps(event.payload, ensure_ascii=False).encode("utf-8")

    assert b"private-value" not in serialized
    assert b"db-password" not in serialized
    assert len(serialized) <= 16_384


async def test_event_rejects_wrong_run_project(db_session: AsyncSession) -> None:
    owner = await _new_user(db_session, "wrong-project")
    project = await _new_project(db_session, owner)
    other_project = await _new_project(db_session, owner)
    run = await _new_run(db_session, owner, project)

    with pytest.raises(ValueError, match="project does not match"):
        await append_generation_event(
            db_session,
            run_id=run.id,
            project_id=other_project.id,
            message_id=None,
            event_type="generation.phase",
            payload={"phase": "edit"},
        )


async def test_concurrent_event_sequence_allocation_is_unique_and_gap_free(test_engine) -> None:
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    async with factory() as setup:
        owner = await _new_user(setup, "concurrent")
        project = await _new_project(setup, owner)
        run = await _new_run(setup, owner, project)
        await setup.commit()
        run_id = run.id
        project_id = project.id

    async def append_one(marker: int) -> int:
        async with factory() as session:
            event = await append_generation_event(
                session,
                run_id=run_id,
                project_id=project_id,
                message_id=None,
                event_type="generation.phase",
                payload={"marker": marker},
            )
            await session.commit()
            return event.seq

    sequences = await asyncio.gather(*(append_one(marker) for marker in range(8)))
    assert sorted(sequences) == list(range(1, 9))
