from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Cookie, Query, WebSocket, WebSocketDisconnect, status
from sqlalchemy.ext.asyncio import async_sessionmaker

from omnia_api.core.db import get_engine
from omnia_api.core.redis import get_active_stream, get_stream_state
from omnia_api.core.security import decode_access_token
from omnia_api.models.project import Project
from omnia_api.services.ws_hub import hub

router = APIRouter(prefix="/api/ws", tags=["ws"])


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
) -> None:
    auth_token = omnia_session or token
    if not auth_token:
        await ws.close(code=status.WS_1008_POLICY_VIOLATION)
        return
    user_id = decode_access_token(auth_token)
    if user_id is None:
        await ws.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    factory = async_sessionmaker(get_engine(), expire_on_commit=False)
    async with factory() as session:
        project = await session.get(Project, project_id)
        if project is None or project.owner_id != user_id:
            await ws.close(code=status.WS_1008_POLICY_VIOLATION)
            return

    await ws.accept()
    # Resync FIRST, then register for live deltas. In this order a reconnecting
    # client gets the current buffer before any live delta lands — no stray
    # pre-sync chunk to clobber. Any residual gap (deltas published in the tiny
    # window before `hub.connect`) is healed by the client requesting `resync`
    # on seq-gap detection — handled in the loop below.
    await _send_stream_sync(ws, project_id)
    await hub.connect(project_id, ws)
    try:
        while True:
            # Client→server control frames: {"type":"ping"} keep-alive (no-op)
            # and {"type":"resync"} — replay the current buffer when the client
            # detected a missed delta after reconnect.
            msg = await ws.receive_json()
            if isinstance(msg, dict) and msg.get("type") == "resync":
                await _send_stream_sync(ws, project_id)
    except WebSocketDisconnect:
        pass
    finally:
        await hub.disconnect(project_id, ws)
