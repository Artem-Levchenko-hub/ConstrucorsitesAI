"""Cross-process invalidations with bounded per-socket queues and no private bodies."""

from __future__ import annotations

import asyncio
import json
from contextlib import suppress
from typing import Any
from uuid import UUID

from yleum_api.core.redis import get_redis
from yleum_api.models.support import SupportTicket


class SupportHub:
    def __init__(self) -> None:
        self._subscribers: dict[str, set[asyncio.Queue[dict[str, Any]]]] = {}
        self._task: asyncio.Task[None] | None = None

    def subscribe(self, channel: str) -> asyncio.Queue[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=64)
        self._subscribers.setdefault(channel, set()).add(queue)
        return queue

    def unsubscribe(self, channel: str, queue: asyncio.Queue[dict[str, Any]]) -> None:
        queues = self._subscribers.get(channel)
        if queues is not None:
            queues.discard(queue)
            if not queues:
                self._subscribers.pop(channel, None)

    async def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._listen())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    def dispatch(self, channel: str, event: dict[str, Any]) -> None:
        for queue in tuple(self._subscribers.get(channel, ())):
            if queue.full():
                queue.get_nowait()
            queue.put_nowait(event)

    async def _listen(self) -> None:
        while True:
            try:
                async with get_redis().pubsub() as pubsub:
                    await pubsub.psubscribe("omnia:support:*")
                    async for message in pubsub.listen():
                        if message.get("type") not in {"pmessage", "message"}:
                            continue
                        channel = message.get("channel", "")
                        if isinstance(channel, bytes):
                            channel = channel.decode()
                        try:
                            event = json.loads(message["data"])
                            if (
                                not isinstance(event, dict)
                                or event.get("type") != "support.changed"
                            ):
                                continue
                            UUID(event["ticket_id"])
                        except (ValueError, KeyError, TypeError):
                            continue
                        self.dispatch(channel.removeprefix("omnia:support:"), event)
            except Exception:
                # REST + ready/reconnect refresh is authoritative during Redis outage.
                await asyncio.sleep(3)


support_hub = SupportHub()


async def publish_support_change(
    ticket: SupportTicket, *, reason: str, author: str | None = None
) -> None:
    event: dict[str, Any] = {
        "type": "support.changed",
        "ticket_id": str(ticket.id),
        "version": ticket.version,
        "reason": reason,
    }
    if author:
        event["author"] = author
    channels = ["admins", f"user:{ticket.owner_id}" if ticket.owner_id else f"guest:{ticket.id}"]
    for channel in channels:
        try:
            await asyncio.wait_for(
                get_redis().publish(f"omnia:support:{channel}", json.dumps(event)), timeout=1
            )
        except Exception:
            # Commit already succeeded. Retrying the HTTP request must remain idempotent.
            pass
