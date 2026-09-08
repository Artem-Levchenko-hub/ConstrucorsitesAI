"""Independent durable MAX dispatcher. An API restart never owns this process.

Only an unstarted dispatch may execute. A claimed orphan has unknown effects and
is failed explicitly, never replayed as a new prompt. Session advisory locks
protect live executors from both duplicate delivery and another worker scanner.
"""

from __future__ import annotations

import asyncio
import json
import logging
from contextlib import suppress
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import exists, or_, select, text, update
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession, async_sessionmaker

from omnia_api.core.config import get_settings
from omnia_api.core.db import get_engine
from omnia_api.core.redis import get_redis
from omnia_api.models.generation_run import GenerationRun
from omnia_api.models.message import Message
from omnia_api.models.project import Project
from omnia_api.models.project_cell import ProjectCellOperation
from omnia_api.services.generation_execution_context import execution_run_id
from omnia_api.services.generation_runs import (
    ACTIVE_GENERATION_STATUSES,
    apply_cancelled_generation_locked,
    load_generation_dispatch,
)

log = logging.getLogger(__name__)
HEARTBEAT_KEY = "omnia:health:generation-worker"
_LOCK_NAMESPACE = 1195724366


def _lock_key(run_id: UUID) -> int:
    value = int.from_bytes(run_id.bytes[-4:], "big", signed=True)
    return value


async def _ownership_monitor(connection: AsyncConnection, run_id: UUID) -> None:
    factory = async_sessionmaker(get_engine(), expire_on_commit=False)
    while True:
        # A failed ownership connection stops execution; it must not reconnect
        # and assume the advisory lock survived.
        await connection.execute(text("SELECT 1"))
        await connection.commit()
        async with factory() as session:
            row = (
                await session.execute(
                    select(GenerationRun.status, GenerationRun.error).where(
                        GenerationRun.id == run_id
                    )
                )
            ).one_or_none()
        if row is None or row.status in {"cancel_requested", "cancelled"}:
            return
        if row.status == "failed" and str(row.error).startswith("generation deadline exceeded;"):
            return
        # Completed/ordinary failed runs may still be releasing their Cell.
        # Retain ownership until the coroutine finishes its physical cleanup.
        await asyncio.sleep(1)


async def _abandon_operations(session: AsyncSession, run_id: UUID) -> None:
    await session.execute(
        update(ProjectCellOperation)
        .where(
            ProjectCellOperation.execution_run_id == run_id,
            ProjectCellOperation.status == "running",
        )
        .values(
            status="indeterminate",
            error="generation_executor_interrupted",
            finished_at=datetime.now(UTC),
        )
    )


async def _fail_orphan(run_id: UUID, message: str) -> None:
    from omnia_api.routers.messages import _emergency_error

    factory = async_sessionmaker(get_engine(), expire_on_commit=False)
    async with factory() as session:
        run = await session.get(GenerationRun, run_id, with_for_update=True)
        if run is None or run.status not in ACTIVE_GENERATION_STATUSES:
            return
        run.status = "failed"
        run.error = message
        run.finished_at = datetime.now(UTC)
        project_id, assistant_id = run.project_id, run.assistant_message_id
        await _abandon_operations(session, run_id)
        await session.commit()
    if assistant_id is not None:
        await _emergency_error(project_id, assistant_id, message)


async def execute_dispatch(run_id: UUID) -> bool:
    from omnia_api.routers.messages import (
        _process_prompt,
        _run_tracked_prompt,
    )

    engine = get_engine()
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.connect() as ownership:
        locked = await ownership.scalar(
            text("SELECT pg_try_advisory_lock(:ns, :key)"),
            {"ns": _LOCK_NAMESPACE, "key": _lock_key(run_id)},
        )
        await ownership.commit()
        if not locked:
            return False
        work: asyncio.Task[None] | None = None
        monitor: asyncio.Task[None] | None = None
        context_token = execution_run_id.set(run_id)
        try:
            async with factory() as session:
                run = await session.get(GenerationRun, run_id, with_for_update=True)
                if run is None or run.execution_backend != "worker":
                    return False
                if run.status not in ACTIVE_GENERATION_STATUSES:
                    await _abandon_operations(session, run_id)
                    await session.commit()
                    return False
                if run.status == "cancel_requested":
                    await apply_cancelled_generation_locked(session, run)
                    await session.commit()
                    return False
                if run.execution_started_at is not None:
                    orphan = True
                    dispatch = None
                else:
                    orphan = False
                    dispatch = load_generation_dispatch(run)
                    project = await session.get(Project, run.project_id)
                    user_message = await session.get(Message, dispatch.user_message_id)
                    assistant = await session.get(Message, dispatch.assistant_message_id)
                    if (
                        project is None
                        or project.owner_id != run.user_id
                        or run.user_message_id != dispatch.user_message_id
                        or run.assistant_message_id != dispatch.assistant_message_id
                        or user_message is None
                        or user_message.project_id != run.project_id
                        or user_message.role != "user"
                        or assistant is None
                        or assistant.project_id != run.project_id
                        or assistant.role != "assistant"
                    ):
                        raise ValueError("generation dispatch message ownership mismatch")
                    run.execution_started_at = datetime.now(UTC)
                await session.commit()
            if orphan:
                await _fail_orphan(
                    run_id,
                    "Generation executor stopped before completion; "
                    "unknown effects were not replayed",
                )
                return False
            assert dispatch is not None
            kwargs = dict(
                run_id=run_id,
                project_id=dispatch.project_id,
                user_id=dispatch.user_id,
                user_message_id=dispatch.user_message_id,
                assistant_message_id=dispatch.assistant_message_id,
                current_snapshot_id=dispatch.current_snapshot_id,
                prompt_text=dispatch.prompt_text,
                model_id=dispatch.model_id,
                force_model=dispatch.force_model,
                is_free=dispatch.is_free,
                free_business_id=dispatch.free_business_id,
                orchestrate=dispatch.orchestrate,
                selected_elements=dispatch.selected_elements,
            )
            capacity_token = uuid4()
            work = asyncio.create_task(
                _run_tracked_prompt(
                    _process_prompt(**kwargs, capacity_dispatch_token=capacity_token),  # type: ignore[arg-type]
                    run_id=run_id,
                    project_id=dispatch.project_id,
                    assistant_message_id=dispatch.assistant_message_id,
                    label="generation-worker",
                    capacity_dispatch_token=capacity_token,
                )
            )
            monitor = asyncio.create_task(_ownership_monitor(ownership, run_id))
            done, _ = await asyncio.wait({work, monitor}, return_when=asyncio.FIRST_COMPLETED)
            if monitor in done:
                await monitor
                # The database cancellation/deadline is authoritative even if a
                # Redis notification was lost. Cancel the actual running task.
                work.cancel()
            with suppress(asyncio.CancelledError):
                await work
            return True
        except Exception:
            log.exception("generation dispatcher failed", extra={"run_id": str(run_id)})
            await _fail_orphan(
                run_id, "Generation executor lost ownership or could not load its durable dispatch"
            )
            return False
        finally:
            for task in (work, monitor):
                if task is not None and not task.done():
                    task.cancel()
                    with suppress(asyncio.CancelledError, Exception):
                        await task
            execution_run_id.reset(context_token)
            with suppress(Exception):
                await ownership.execute(
                    text("SELECT pg_advisory_unlock(:ns, :key)"),
                    {"ns": _LOCK_NAMESPACE, "key": _lock_key(run_id)},
                )
                await ownership.commit()


async def run_forever() -> None:
    factory = async_sessionmaker(get_engine(), expire_on_commit=False)
    active: dict[UUID, asyncio.Task[bool]] = {}
    while True:
        try:
            for run_id, task in list(active.items()):
                if task.done():
                    # Drop the completed task before observing its exception.
                    # A pre-claim DB failure must not poison every later scan.
                    active.pop(run_id)
                    task.result()
            async with factory() as session:
                candidates = list(
                    (
                        await session.scalars(
                            select(GenerationRun.id)
                            .where(
                                GenerationRun.execution_backend == "worker",
                                or_(
                                    GenerationRun.status.in_(ACTIVE_GENERATION_STATUSES),
                                    exists().where(
                                        ProjectCellOperation.execution_run_id == GenerationRun.id,
                                        ProjectCellOperation.status == "running",
                                    ),
                                ),
                            )
                            .order_by(GenerationRun.created_at)
                            .limit(100)
                        )
                    ).all()
                )
            for run_id in candidates:
                if run_id not in active and len(active) < 8:
                    active[run_id] = asyncio.create_task(execute_dispatch(run_id))
            await get_redis().set(
                HEARTBEAT_KEY,
                json.dumps(
                    {
                        "release_sha": get_settings().omnia_release_sha,
                        "at": datetime.now(UTC).isoformat(),
                    }
                ),
                ex=30,
            )
        except Exception:
            log.exception("generation dispatcher scan failed")
        await asyncio.sleep(2)


if __name__ == "__main__":
    asyncio.run(run_forever())
