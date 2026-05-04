"""In-process реестр WebSocket-соединений + слушатель Redis pubsub.

Один процесс api держит свои WS у себя. Когда worker публикует событие в Redis,
listener получает его и доставляет всем своим WS, открытым на этом project_id.
Если api поднят в нескольких репликах — каждая реплика подписана на тот же канал
и доставляет событие своим клиентам."""

from __future__ import annotations

import asyncio
import json
from collections import defaultdict
from typing import Any
from uuid import UUID

from fastapi import WebSocket

from omnia_api.core.redis import get_redis


class WebSocketHub:
    def __init__(self) -> None:
        self._connections: dict[UUID, set[WebSocket]] = defaultdict(set)
        self._lock = asyncio.Lock()
        self._listener_task: asyncio.Task[None] | None = None

    async def connect(self, project_id: UUID, ws: WebSocket) -> None:
        async with self._lock:
            self._connections[project_id].add(ws)

    async def disconnect(self, project_id: UUID, ws: WebSocket) -> None:
        async with self._lock:
            self._connections[project_id].discard(ws)
            if not self._connections[project_id]:
                self._connections.pop(project_id, None)

    async def broadcast_local(
        self, project_id: UUID, message: dict[str, Any]
    ) -> None:
        async with self._lock:
            recipients = list(self._connections.get(project_id, set()))
        for ws in recipients:
            try:
                await ws.send_json(message)
            except Exception:
                # disconnect случится по receive_json в роутере
                pass

    async def start_listener(self) -> None:
        if self._listener_task is not None:
            return
        self._listener_task = asyncio.create_task(self._listen())

    async def stop_listener(self) -> None:
        if self._listener_task is None:
            return
        self._listener_task.cancel()
        try:
            await self._listener_task
        except asyncio.CancelledError:
            pass
        self._listener_task = None

    async def _listen(self) -> None:
        client = get_redis()
        pubsub = client.pubsub()
        await pubsub.psubscribe("omnia:project:*")
        try:
            async for msg in pubsub.listen():
                if msg.get("type") not in {"pmessage", "message"}:
                    continue
                channel = msg.get("channel") or ""
                if not channel.startswith("omnia:project:"):
                    continue
                project_id_str = channel.split("omnia:project:", 1)[1]
                try:
                    project_id = UUID(project_id_str)
                except ValueError:
                    continue
                try:
                    payload = json.loads(msg["data"])
                except (json.JSONDecodeError, TypeError, KeyError):
                    continue
                await self.broadcast_local(project_id, payload)
        finally:
            try:
                await pubsub.punsubscribe()
            finally:
                await pubsub.aclose()


hub = WebSocketHub()
