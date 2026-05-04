from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Cookie, Query, WebSocket, WebSocketDisconnect, status
from sqlalchemy.ext.asyncio import async_sessionmaker

from omnia_api.core.db import get_engine
from omnia_api.core.security import decode_access_token
from omnia_api.models.project import Project
from omnia_api.services.ws_hub import hub

router = APIRouter(prefix="/api/ws", tags=["ws"])


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
    await hub.connect(project_id, ws)
    try:
        while True:
            # Single client→server message: { "type": "ping" } для keep-alive.
            await ws.receive_json()
    except WebSocketDisconnect:
        pass
    finally:
        await hub.disconnect(project_id, ws)
