"""RQ owner for one durable MAX generation run."""

from __future__ import annotations

import asyncio
import contextlib
import socket
from typing import Any, cast
from uuid import UUID, uuid4

from omnia_api.core.db import dispose_engine
from omnia_api.core.redis import dispose_redis
from omnia_api.services.generation_continuity import (
    claim_run,
    heartbeat_forever,
    release_lease,
)


async def _run(run_id: UUID, owner: str, enqueue_token: str) -> None:
    envelope = await claim_run(run_id, owner, enqueue_token)
    if envelope is None:
        return
    heartbeat = asyncio.create_task(heartbeat_forever(run_id, owner))
    try:
        # Lazy import avoids loading the FastAPI router in idle RQ workers and
        # keeps generation_continuity independently unit-testable.
        from omnia_api.routers.messages import _process_prompt, _run_tracked_prompt

        project_id = UUID(str(envelope["project_id"]))
        assistant_message_id = UUID(str(envelope["assistant_message_id"]))
        await _run_tracked_prompt(
            _process_prompt(
                run_id=run_id,
                project_id=project_id,
                assistant_message_id=assistant_message_id,
                user_id=UUID(str(envelope["user_id"])),
                user_message_id=UUID(str(envelope["user_message_id"])),
                current_snapshot_id=(
                    UUID(str(envelope["current_snapshot_id"]))
                    if envelope.get("current_snapshot_id")
                    else None
                ),
                prompt_text=str(envelope["prompt_text"]),
                model_id=str(envelope["model_id"]),
                force_model=(
                    str(envelope["force_model"])
                    if envelope.get("force_model") is not None
                    else None
                ),
                is_free=bool(envelope.get("is_free")),
                max_demo_reserved=bool(envelope.get("max_demo_reserved")),
                orchestrate=bool(envelope.get("orchestrate", True)),
                selected_elements=(
                    cast(list[dict[str, Any]], envelope["selected_elements"])
                    if isinstance(envelope.get("selected_elements"), list)
                    else None
                ),
            ),
            run_id=run_id,
            project_id=project_id,
            assistant_message_id=assistant_message_id,
            label="durable_max_generation",
        )
    finally:
        heartbeat.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await heartbeat
        try:
            await release_lease(run_id, owner)
        finally:
            # RQ invokes this sync entrypoint with a fresh ``asyncio.run`` loop
            # per job. Never carry asyncpg/redis connections into the next loop.
            await dispose_redis()
            await dispose_engine()


def run_generation_job(run_id: str, enqueue_token: str | None = None) -> None:
    """Synchronous RQ entrypoint; a stale duplicate exits at the DB lease."""

    # Pre-deploy duplicate backlog jobs have no token and are deliberately
    # harmless. The watchdog will reserve/enqueue one current token.
    if not enqueue_token:
        return
    parsed = UUID(run_id)
    owner = f"{socket.gethostname()}:{uuid4()}"
    asyncio.run(_run(parsed, owner, enqueue_token))


__all__ = ["run_generation_job"]
