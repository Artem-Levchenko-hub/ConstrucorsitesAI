from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from omnia_api.models.generation_run import GenerationRun
from omnia_api.models.project import Project
from omnia_api.models.user import User
from omnia_api.routers.ws import _send_generation_replay, _SequencedSocket
from omnia_api.services.generation_events import (
    append_generation_event,
    generation_event_envelope,
    generation_event_high_water,
)


class _Socket:
    def __init__(self) -> None:
        self.messages: list[dict[str, Any]] = []

    async def send_json(self, message: dict[str, Any]) -> None:
        self.messages.append(message)


async def _run(session: AsyncSession) -> GenerationRun:
    owner = User(
        email=f"ws-replay-{uuid.uuid4().hex}@example.com",
        password_hash="x",
    )
    session.add(owner)
    await session.flush()
    project = Project(
        owner_id=owner.id,
        name="Replay",
        slug=f"ws-replay-{uuid.uuid4().hex}",
        template="blank",
    )
    session.add(project)
    await session.flush()
    run = GenerationRun(
        project_id=project.id,
        user_id=owner.id,
        idempotency_key=f"ws-replay:{uuid.uuid4().hex}",
        prompt_hash="a" * 64,
        status="running",
        agent_state={},
    )
    session.add(run)
    await session.flush()
    return run


async def test_replay_subscribe_gap_fill_delivers_every_sequence_once(
    db_session: AsyncSession,
    test_engine,
) -> None:
    run = await _run(db_session)
    for marker in range(1, 131):
        await append_generation_event(
            db_session,
            run_id=run.id,
            project_id=run.project_id,
            message_id=None,
            event_type="generation.phase",
            payload={"marker": marker},
        )
    await db_session.commit()

    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    raw_socket = _Socket()
    socket = _SequencedSocket(raw_socket, run.id, last_seq=51)  # type: ignore[arg-type]
    async with factory() as session:
        high_water = await generation_event_high_water(session, run_id=run.id)
    assert high_water == 130
    await _send_generation_replay(
        socket,
        factory,
        run_id=run.id,
        after_seq=51,
        high_water=high_water,
    )

    async with factory() as session:
        event = await append_generation_event(
            session,
            run_id=run.id,
            project_id=run.project_id,
            message_id=None,
            event_type="tool.heartbeat",
            payload={"operation_id": "op-1"},
        )
        await session.commit()
        await session.refresh(event)
    live = {"type": "generation.event", "data": generation_event_envelope(event)}
    await socket.send_json(live)

    await _send_generation_replay(
        socket,
        factory,
        run_id=run.id,
        after_seq=high_water,
        high_water=131,
    )
    await socket.replay_complete(131)
    await socket.resume()

    sequences = [
        message["data"]["seq"]
        for message in raw_socket.messages
        if message["type"] == "generation.event"
    ]
    assert sequences == list(range(52, 132))
    assert raw_socket.messages[-1] == {
        "type": "generation.replay.complete",
        "data": {"run_id": str(run.id), "high_water": 131},
    }


async def test_sequenced_socket_drops_duplicate_live_delivery() -> None:
    run_id = uuid.uuid4()
    raw_socket = _Socket()
    socket = _SequencedSocket(raw_socket, run_id, last_seq=130)  # type: ignore[arg-type]
    duplicate = {
        "type": "generation.event",
        "data": {
            "event_id": str(uuid.uuid4()),
            "run_id": str(run_id),
            "seq": 130,
            "type": "tool.heartbeat",
            "data": {},
        },
    }

    await socket.resume()
    await socket.send_json(duplicate)

    assert raw_socket.messages == []
