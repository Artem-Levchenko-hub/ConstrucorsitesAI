"""Publish runtime/deploy lifecycle events to the api's WebSocket fan-out.

Channel contract:

- Channel name:  ``omnia:project:<project_id>``
- Payload shape: ``{"type": "<event_type>", "data": {...}}`` (JSON)

apps/api ``services/ws_hub.py`` already ``psubscribe("omnia:project:*")`` and
forwards every payload to the connected WebSocket clients of that project.
The frontend ``hooks/usePromptStream.ts`` already routes the event types we
emit here (``runtime.started``, ``runtime.stopped``, ``runtime.crashed``,
``deploy.progress``, ``deploy.done``, ``deploy.failed``) into the right
react-query caches. So publishing here = the user sees the change live, no
api callback wiring needed.

Fail-soft (R-10): any Redis hiccup logs and returns. A missing live-update
must NEVER take down a provision/deploy.
"""

from __future__ import annotations

import json
from contextlib import suppress
from typing import Any

import structlog

from omnia_orchestrator.core.config import get_settings

log = structlog.get_logger("omnia_orchestrator.event_publisher")

# Lazy module-level client. Same pattern as hibernate.py — single-process,
# asyncio-only, so no lock needed; first call creates the client, subsequent
# calls reuse it. We never close it explicitly — the systemd unit's lifecycle
# is the connection's lifecycle.
_redis_client: Any = None


def _channel(project_id: str) -> str:
    return f"omnia:project:{project_id}"


async def _get_client() -> Any:
    global _redis_client
    if _redis_client is not None:
        return _redis_client
    try:
        from redis import asyncio as redis_async

        _redis_client = redis_async.from_url(  # type: ignore[no-untyped-call]
            get_settings().redis_url, decode_responses=False
        )
        # Verify connectivity early — a half-open client wastes every publish.
        await _redis_client.ping()
    except Exception as exc:
        log.warning("event_publisher.connect_failed", err=str(exc))
        _redis_client = None
    return _redis_client


async def publish_project_event(
    project_id: str, event_type: str, data: dict[str, Any]
) -> None:
    """Best-effort publish to ``omnia:project:<project_id>``.

    Never raises — a downed Redis or a serialization slip silently degrades to
    "no live update" rather than failing the surrounding flow. Caller decides
    whether the underlying action (provision, stop, deploy) succeeded; this is
    purely the notification channel.
    """
    payload = {"type": event_type, "data": data}
    try:
        client = await _get_client()
        if client is None:
            return
        await client.publish(_channel(project_id), json.dumps(payload, default=str))
        log.info(
            "event_publisher.published",
            project_id=project_id,
            event_type=event_type,
        )
    except Exception as exc:
        log.warning(
            "event_publisher.publish_failed",
            project_id=project_id,
            event_type=event_type,
            err=str(exc),
        )


async def dispose() -> None:
    """Close the Redis client. Called from lifespan shutdown."""
    global _redis_client
    if _redis_client is not None:
        with suppress(Exception):
            await _redis_client.aclose()
        _redis_client = None
