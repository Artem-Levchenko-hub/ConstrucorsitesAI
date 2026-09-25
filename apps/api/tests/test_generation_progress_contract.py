"""GenerationProgress contracts with real SQL rows and only publication replaced."""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from yleum_api.models.generation_event import GenerationEvent
from yleum_api.models.generation_run import GenerationRun
from yleum_api.models.message import Message
from yleum_api.models.project import Project
from yleum_api.models.user import User
from yleum_api.services.generation import progress

pytestmark = pytest.mark.asyncio


async def _new_progress(factory: async_sessionmaker[AsyncSession]) -> progress.GenerationProgress:
    async with factory() as session:
        owner = User(email=f"progress-{uuid4().hex}@example.com", password_hash="fixture")
        session.add(owner)
        await session.flush()
        project = Project(
            owner_id=owner.id,
            name="Progress contract",
            slug=f"progress-{uuid4().hex}",
            template="blank",
        )
        session.add(project)
        await session.flush()
        message = Message(project_id=project.id, role="assistant", content="")
        session.add(message)
        await session.flush()
        run = GenerationRun(
            project_id=project.id,
            user_id=owner.id,
            assistant_message_id=message.id,
            idempotency_key=f"progress-{uuid4().hex}",
            prompt_hash="a" * 64,
            status="running",
            response_mode="build",
        )
        session.add(run)
        await session.commit()
        return progress.GenerationProgress(factory, run.id, project.id, message.id)


async def _read_progress(recorder: progress.GenerationProgress):
    async with recorder.factory() as session:
        events = list(
            await session.scalars(
                select(GenerationEvent)
                .where(GenerationEvent.generation_run_id == recorder.run_id)
                .order_by(GenerationEvent.seq)
            )
        )
        message = await session.get(Message, recorder.assistant_message_id)
        return events, message.agent_steps


async def test_mixed_progress_keeps_wire_payload_order_and_independent_durable_sequence(
    test_engine,
    monkeypatch,
):
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    recorder = await _new_progress(factory)
    published = []

    async def publish(project_id, event_type, payload):
        published.append((project_id, event_type, payload))

    monkeypatch.setattr(progress, "publish_event", publish)
    await recorder.emit_agent_event(
        "agent.step",
        {
            "step": 37,
            "action": "write_file",
            "human": "Записываю страницу",
            "path": "src/app/page.tsx",
            "detail": "Page saved",
            "ok": True,
        },
    )
    await recorder.record_generation_event("capacity.queued", {"position": 2})
    await recorder.emit_agent_event(
        "agent.retry",
        {
            "step": 5,
            "action": "build",
            "attempt": 2,
            "detail": "Build retry",
            "ok": False,
        },
    )
    # Durable order belongs to the run, not a recorder's local transcript or
    # model step number. A later recorder continues it; another run starts at 1.
    resumed = progress.GenerationProgress(
        factory,
        recorder.run_id,
        recorder.project_id,
        recorder.assistant_message_id,
    )
    await resumed.record_generation_event("preview.ready", {"path": "/"})
    other = await _new_progress(factory)
    await other.record_generation_event("capacity.queued", {"position": 1})

    first_step = {
        "step": 37,
        "kind": "step",
        "action": "Записываю страницу",
        "tool": "write_file",
        "path": "src/app/page.tsx",
        "detail": "Page saved",
        "ok": True,
    }
    retry_step = {
        "step": 5,
        "kind": "retry",
        "action": "повторяю запрос (#2)",
        "tool": "build",
        "path": "",
        "detail": "Build retry",
        "ok": False,
    }
    message_id = str(recorder.assistant_message_id)
    expected_payloads = [
        {"message_id": message_id, **first_step},
        {"message_id": message_id, "position": 2},
        {"message_id": message_id, **retry_step},
        {"message_id": message_id, "path": "/"},
    ]
    events, transcript = await _read_progress(recorder)
    other_events, other_transcript = await _read_progress(other)
    assert transcript == [first_step, retry_step]
    assert [event.seq for event in events] == [1, 2, 3, 4]
    assert [event.event_type for event in events] == [
        "agent.step",
        "capacity.queued",
        "agent.step",
        "preview.ready",
    ]
    assert [event.payload for event in events] == expected_payloads
    assert all(event.message_id == recorder.assistant_message_id for event in events)
    assert all(event.project_id == recorder.project_id for event in events)
    assert [event.seq for event in other_events] == [1]
    assert other_transcript is None
    assert other_events[0].payload == {
        "message_id": str(other.assistant_message_id),
        "position": 1,
    }
    assert [project_id for project_id, _, _ in published] == [recorder.project_id] * 4 + [
        other.project_id,
    ]
    assert all(kind == "generation.event" for _, kind, _ in published)
    assert [payload for _, _, payload in published] == [
        {
            "event_id": str(event.id),
            "run_id": str(event.generation_run_id),
            "seq": event.seq,
            "type": event.event_type,
            "data": event.payload,
        }
        for event in [*events, *other_events]
    ]


async def test_transcript_stops_at_first_200_steps_while_durable_events_continue(
    test_engine,
    monkeypatch,
):
    recorder = await _new_progress(async_sessionmaker(test_engine, expire_on_commit=False))
    published = []

    async def publish(_project_id, event_type, payload):
        published.append((event_type, payload))

    monkeypatch.setattr(progress, "publish_event", publish)
    expected_steps = [
        {
            "step": number,
            "kind": "step",
            "action": "Write page",
            "tool": "write_file",
            "path": f"src/page-{number}.tsx",
            "detail": "Saved",
            "ok": True,
        }
        for number in range(1, 202)
    ]
    for step in expected_steps:
        await recorder.record_agent_step(step)
    await recorder.record_generation_event("preview.ready", {"path": "/"})

    events, transcript = await _read_progress(recorder)
    assert transcript == expected_steps[:200]
    assert len(events) == len(published) == 202
    assert [event.seq for event in events] == list(range(1, 203))
    assert [event.event_type for event in events] == ["agent.step"] * 201 + ["preview.ready"]
    assert [event.payload for event in events[:201]] == [
        {"message_id": str(recorder.assistant_message_id), **step} for step in expected_steps
    ]
    assert events[-1].payload == {
        "message_id": str(recorder.assistant_message_id),
        "path": "/",
    }
    assert all(kind == "generation.event" for kind, _ in published)
    assert [payload["seq"] for _, payload in published] == list(range(1, 203))
    assert published[200][1]["data"]["step"] == 201


async def test_publish_failure_observes_committed_event_and_transcript_before_raising(
    test_engine,
    monkeypatch,
):
    recorder = await _new_progress(async_sessionmaker(test_engine, expire_on_commit=False))
    step = {
        "step": 1,
        "kind": "step",
        "action": "Write page",
        "tool": "write_file",
        "path": "src/app/page.tsx",
        "detail": "Saved",
        "ok": True,
    }
    visible = []

    async def unavailable_publisher(project_id, event_type, payload):
        assert project_id == recorder.project_id and event_type == "generation.event"
        # Independent SQL session opened INSIDE the failing external callback.
        # A read after record() returns would not establish commit-before-publish.
        async with recorder.factory() as session:
            event = await session.get(GenerationEvent, UUID(payload["event_id"]))
            message = await session.get(Message, recorder.assistant_message_id)
            assert event is not None and event.seq == 1
            assert event.generation_run_id == recorder.run_id
            assert event.message_id == recorder.assistant_message_id
            assert event.payload == {"message_id": str(message.id), **step}
            assert message.agent_steps == [step]
            visible.append(event.id)
        raise RuntimeError("synthetic publisher unavailable")

    monkeypatch.setattr(progress, "publish_event", unavailable_publisher)
    await recorder.record_agent_step(step)  # Publication failure must be swallowed.
    events, transcript = await _read_progress(recorder)
    assert visible == [events[0].id]
    assert len(events) == 1 and transcript == [step]
