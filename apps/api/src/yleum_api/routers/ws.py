from __future__ import annotations

import asyncio
from contextlib import suppress
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Cookie, Query, WebSocket, WebSocketDisconnect, status
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from yleum_api.core.config import get_settings
from yleum_api.core.db import get_engine
from yleum_api.core.deps import resolve_session_user
from yleum_api.core.redis import get_active_stream, get_stream_state
from yleum_api.core.request_origin import has_session_cookie, request_origin_allowed
from yleum_api.models.generation_run import GenerationRun
from yleum_api.models.project import Project
from yleum_api.services.generation_events import (
    generation_event_envelope,
    generation_event_high_water,
    replay_generation_events,
)
from yleum_api.services.ws_hub import hub

router = APIRouter(prefix="/api/ws", tags=["ws"])
_REPLAY_BATCH_SIZE = 200
_AUTH_RECHECK_SECONDS = 15.0
_AUTH_CHECK_TIMEOUT_SECONDS = 5.0


def _durable_sequence(message: object, run_id: UUID) -> int | None:
    if not isinstance(message, dict) or message.get("type") != "generation.event":
        return None
    envelope = message.get("data")
    if not isinstance(envelope, dict) or envelope.get("run_id") != str(run_id):
        return None
    seq = envelope.get("seq")
    return seq if type(seq) is int and seq > 0 else None


class _SequencedSocket:
    """Pause Redis delivery while DB replay closes its subscribe race."""

    def __init__(self, ws: WebSocket, run_id: UUID, *, last_seq: int) -> None:
        self.ws = ws
        self.run_id = run_id
        self.last_seq = last_seq
        self._paused = True
        self._buffer: list[dict[str, Any]] = []
        self._lock = asyncio.Lock()

    async def send_json(self, message: Any) -> None:
        async with self._lock:
            seq = _durable_sequence(message, self.run_id)
            if seq is not None and seq <= self.last_seq:
                return
            if self._paused:
                if isinstance(message, dict):
                    self._buffer.append(message)
                return
            if seq is not None:
                self.last_seq = seq
            await self.ws.send_json(message)

    async def send_replay(self, message: dict[str, Any], *, force: bool = False) -> None:
        async with self._lock:
            seq = _durable_sequence(message, self.run_id)
            if seq is None or (seq <= self.last_seq and not force):
                return
            await self.ws.send_json(message)
            self.last_seq = max(self.last_seq, seq)

    async def replay_complete(self, high_water: int) -> None:
        async with self._lock:
            await self.ws.send_json(
                {
                    "type": "generation.replay.complete",
                    "data": {"run_id": str(self.run_id), "high_water": high_water},
                }
            )

    async def resume(self) -> None:
        async with self._lock:
            self._paused = False
            buffered = self._buffer
            self._buffer = []
            buffered.sort(key=lambda item: _durable_sequence(item, self.run_id) or 2**63)
            for message in buffered:
                seq = _durable_sequence(message, self.run_id)
                if seq is not None and seq <= self.last_seq:
                    continue
                if seq is not None:
                    self.last_seq = seq
                await self.ws.send_json(message)


async def _send_generation_replay(
    socket: _SequencedSocket,
    factory: async_sessionmaker[AsyncSession],
    *,
    run_id: UUID,
    after_seq: int,
    high_water: int,
    force: bool = False,
) -> int:
    cursor = after_seq
    while cursor < high_water:
        async with factory() as session:
            events = await replay_generation_events(
                session,
                run_id=run_id,
                after_seq=cursor,
                high_water=high_water,
                limit=_REPLAY_BATCH_SIZE,
            )
        if not events:
            break
        for event in events:
            await socket.send_replay(
                {"type": "generation.event", "data": generation_event_envelope(event)},
                force=force,
            )
        cursor = events[-1].seq
    return cursor


async def _send_stream_sync(ws: WebSocket, project_id: UUID) -> None:
    """Отдать клиенту текущее состояние незавершённого стрима одним кадром.

    Главное лекарство от «F5 → realtime сдох»: pub/sub эфемерен, прошлые дельты
    потеряны, поэтому при (пере)подключении отдаём кумулятивный буфер из Redis.
    Дальше клиент дедупит живые дельты по `seq`. Нет активного стрима — тихо
    выходим (обычное подключение без генерации).
    """
    active = await get_active_stream(project_id)
    if not active:
        return
    state = await get_stream_state(active)
    if not state:
        return
    try:
        await ws.send_json(
            {
                "type": "stream.sync",
                "data": {
                    "message_id": state.get("message_id", active),
                    "content": state.get("content", ""),
                    "seq": state.get("seq", 0),
                },
            }
        )
    except Exception:
        pass


@router.websocket("/projects/{project_id}")
async def project_socket(
    ws: WebSocket,
    project_id: UUID,
    omnia_session: Annotated[str | None, Cookie()] = None,
    token: Annotated[str | None, Query()] = None,
    run_id: Annotated[UUID | None, Query()] = None,
    after_seq: Annotated[int, Query(ge=0)] = 0,
) -> None:
    cookie_auth = bool(omnia_session) or has_session_cookie(ws.headers)
    if (cookie_auth or "origin" in ws.headers) and not request_origin_allowed(ws.headers):
        await ws.close(code=status.WS_1008_POLICY_VIOLATION)
        return
    auth_token = omnia_session or token
    if not auth_token:
        await ws.close(code=status.WS_1008_POLICY_VIOLATION)
        return
    factory = async_sessionmaker(get_engine(), expire_on_commit=False)
    replay_run_id = run_id if get_settings().use_generation_event_replay else None
    high_water = 0
    async with factory() as session:
        user = await resolve_session_user(session, auth_token, None)
        if user is None:
            await ws.close(code=status.WS_1008_POLICY_VIOLATION)
            return
        user_id = user.id
        project = await session.get(Project, project_id)
        if project is None or project.owner_id != user_id:
            await ws.close(code=status.WS_1008_POLICY_VIOLATION)
            return
        if replay_run_id is not None:
            run = await session.get(GenerationRun, replay_run_id)
            if run is None or run.project_id != project_id or run.user_id != user_id:
                await ws.close(code=status.WS_1008_POLICY_VIOLATION)
                return
            high_water = await generation_event_high_water(session, run_id=replay_run_id)

    await ws.accept()
    connected_socket = (
        _SequencedSocket(ws, replay_run_id, last_seq=after_seq) if replay_run_id is not None else ws
    )
    # Authority supervision starts before setup, replay or hub subscription.
    # None of those operations may postpone revocation while waiting on I/O.
    authority = asyncio.create_task(
        _monitor_socket_authority(factory, auth_token, user_id),
        name="project-socket-authority",
    )
    events = asyncio.create_task(
        _serve_project_events(
            ws,
            project_id,
            factory=factory,
            replay_run_id=replay_run_id,
            after_seq=after_seq,
            high_water=high_water,
            connected_socket=connected_socket,
        ),
        name="project-socket-events",
    )
    close_code = None
    try:
        done, _pending = await asyncio.wait(
            (authority, events), return_when=asyncio.FIRST_COMPLETED
        )
        if authority in done:
            close_code = authority.result()
        else:
            await events
    except WebSocketDisconnect:
        pass
    finally:
        authority.cancel()
        events.cancel()
        try:
            await hub.disconnect(project_id, connected_socket)
            # Close before joining cancelled I/O cleanup: a stalled dependency
            # must not keep a revoked subscriber connected during teardown.
            if close_code is not None:
                with suppress(WebSocketDisconnect, RuntimeError):
                    await ws.close(code=close_code)
        finally:
            await asyncio.gather(authority, events, return_exceptions=True)


async def _monitor_socket_authority(
    factory: async_sessionmaker[AsyncSession],
    auth_token: str,
    user_id: UUID,
) -> int:
    while True:
        await asyncio.sleep(_AUTH_RECHECK_SECONDS)
        try:
            async with asyncio.timeout(_AUTH_CHECK_TIMEOUT_SECONDS):
                async with factory() as session:
                    current = await resolve_session_user(session, auth_token, None)
            if current is None or current.id != user_id:
                return status.WS_1008_POLICY_VIOLATION
        except Exception:
            # Driver transport errors need not inherit SQLAlchemyError. Any
            # unavailable authority fails closed; cancellation still propagates.
            return status.WS_1013_TRY_AGAIN_LATER


async def _serve_project_events(
    ws: WebSocket,
    project_id: UUID,
    *,
    factory: async_sessionmaker[AsyncSession],
    replay_run_id: UUID | None,
    after_seq: int,
    high_water: int,
    connected_socket: Any,
) -> None:
    if replay_run_id is None:
        await _send_stream_sync(ws, project_id)
        await hub.connect(project_id, ws)
    else:
        await _send_generation_replay(
            connected_socket,
            factory,
            run_id=replay_run_id,
            after_seq=after_seq,
            high_water=high_water,
        )
        await hub.connect(project_id, connected_socket)
        async with factory() as session:
            gap_high_water = await generation_event_high_water(session, run_id=replay_run_id)
        await _send_generation_replay(
            connected_socket,
            factory,
            run_id=replay_run_id,
            after_seq=high_water,
            high_water=gap_high_water,
        )
        await connected_socket.replay_complete(gap_high_water)
        await connected_socket.resume()
    while True:
        # Client→server control frames: {"type":"ping"} keep-alive (no-op)
        # and {"type":"resync"} replay after a missed delta. Authority is
        # checked independently even while receive, DB, Redis or send stalls.
        msg = await ws.receive_json()
        if (
            replay_run_id is not None
            and isinstance(msg, dict)
            and msg.get("type") == "generation.resync"
        ):
            requested_run = msg.get("run_id")
            requested_after = msg.get("after_seq")
            if requested_run != str(replay_run_id) or type(requested_after) is not int:
                continue
            async with factory() as session:
                replay_high_water = await generation_event_high_water(session, run_id=replay_run_id)
            await _send_generation_replay(
                connected_socket,
                factory,
                run_id=replay_run_id,
                after_seq=max(0, requested_after),
                high_water=replay_high_water,
                force=True,
            )
            await connected_socket.replay_complete(replay_high_water)
        elif isinstance(msg, dict) and msg.get("type") == "resync":
            await _send_stream_sync(ws, project_id)
