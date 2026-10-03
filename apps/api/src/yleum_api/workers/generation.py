"""Independent durable MAX dispatcher. An API restart never owns this process.

Only an unstarted dispatch may execute. A claimed orphan has unknown effects and
is failed explicitly, never replayed as a new prompt. Session advisory locks
protect live executors from both duplicate delivery and another worker scanner.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import Select, and_, exists, func, or_, select, text, update
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession, async_sessionmaker

from yleum_api.core.config import get_settings
from yleum_api.core.db import (
    DATABASE_POOL_CAPACITY,
    GENERATION_CONNECTIONS_PER_RUN,
    GENERATION_QUERY_CONNECTION_RESERVE,
    get_engine,
)
from yleum_api.core.redis import get_redis, publish_event
from yleum_api.models.generation_run import GenerationRun
from yleum_api.models.message import Message
from yleum_api.models.project import Project
from yleum_api.models.project_cell import ProjectCellOperation, ProjectCellWorkspace
from yleum_api.services.generation_execution_context import execution_run_id
from yleum_api.services.generation_runs import (
    ACTIVE_GENERATION_STATUSES,
    apply_cancelled_generation_locked,
    load_generation_dispatch,
    retry_terminal_adaptation_notifications,
    terminalize_generation_run_locked,
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
    from yleum_api.services.generation.supervisor import _emergency_error

    factory = async_sessionmaker(get_engine(), expire_on_commit=False)
    async with factory() as session:
        run = await session.get(GenerationRun, run_id, with_for_update=True)
        if run is None or run.status not in ACTIVE_GENERATION_STATUSES:
            return
        # A durable Stop request wins a race with ownership loss. Other active
        # runs are infrastructure failures, never a fabricated user cancellation.
        cancelled = run.status == "cancel_requested"
        if cancelled:
            await apply_cancelled_generation_locked(session, run)
        else:
            await terminalize_generation_run_locked(
                session,
                run,
                status="failed",
                error=message,
            )
        project_id, assistant_id = run.project_id, run.assistant_message_id
        await _abandon_operations(session, run_id)
        await session.commit()
    if assistant_id is not None:
        if cancelled:
            await publish_event(project_id, "generation.cancelled", {
                "run_id": str(run_id), "message_id": str(assistant_id),
            })
        else:
            await _emergency_error(project_id, assistant_id, message)


async def execute_dispatch(run_id: UUID) -> bool:
    from yleum_api.services.generation.lifecycle import _process_prompt
    from yleum_api.services.generation.supervisor import (
        _OWNERSHIP_LOST_CANCEL_REASON,
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
                from yleum_api.services.restorations import (
                    adaptation_activation_handoff_pending,
                )

                if await adaptation_activation_handoff_pending(factory, run_id):
                    return False
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
            # Losing ownership must stop physical work before any database write:
            # orphan persistence may itself block on the unavailable database.
            if work is not None:
                work.cancel(_OWNERSHIP_LOST_CANCEL_REASON)
                with suppress(asyncio.CancelledError, Exception):
                    await work
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


def current_dispatch_limit() -> int:
    """Budget ownership + one work checkout per run, plus shared query reserve.

    Increasing this cap requires a new connection budget, not just a setting:
    the work transaction can outlive a query during bootstrap/reconciliation.
    """

    configured = max(1, int(get_settings().generation_worker_max_concurrent))
    budget = (DATABASE_POOL_CAPACITY - GENERATION_QUERY_CONNECTION_RESERVE) // (
        GENERATION_CONNECTIONS_PER_RUN
    )
    return min(configured, budget)


def dispatch_heartbeat_payload(
    *,
    active: int,
    limit: int,
    configured_limit: int | None = None,
    running: int | None = None,
    waiting_capacity: int | None = None,
    other: int | None = None,
) -> str:
    """Slots include waiters; separate DB-state counts explain their occupancy.

    Counts are a scan-time snapshot, not evidence of concurrent model calls.
    Missing fields from older workers remain unknown to compatible readers.
    """

    return json.dumps(
        {
            "release_sha": get_settings().omnia_release_sha,
            "at": datetime.now(UTC).isoformat(),
            "active": active,
            "limit": limit,
            **{
                key: value
                for key, value in {
                    "configured_limit": configured_limit,
                    "running": running,
                    "waiting_capacity": waiting_capacity,
                    "other": other,
                }.items()
                if value is not None
            },
        }
    )


def select_runs_to_start(
    candidates: Sequence[UUID],
    active: Mapping[UUID, object],
    limit: int,
) -> list[UUID]:
    """Какие сборки начать в этот заход.

    Порядок кандидатов — по времени создания, то есть кто раньше нажал, тот
    раньше и пойдёт. Уже идущие пропускаем: повторный запуск той же сборки
    означал бы двойные эффекты. Предел общий, а не на пользователя.
    """

    room = limit - len(active)
    if room <= 0:
        return []
    starting: list[UUID] = []
    for run_id in candidates:
        if len(starting) >= room:
            break
        if run_id not in active:
            starting.append(run_id)
    return starting


@dataclass(frozen=True)
class HostDispatch:
    run_id: UUID
    host: str | None
    status: str = "pending"
    admitted: bool = False
    maintenance: bool = False


def _host_key(host: str | None) -> str:
    # A missing durable workspace is one unknown bucket, never guessed as core.
    return "" if host is None else "h:" + host


def select_host_runs_to_start(
    candidates: Sequence[HostDispatch],
    active: Mapping[UUID, object],
    active_info: Mapping[UUID, HostDispatch],
    limit: int,
    *,
    maintenance: Sequence[HostDispatch] = (),
    after_host: str | None = None,
    prefer_maintenance: bool = True,
) -> list[HostDispatch]:
    """Reserve one provisional dispatch per real host before creating any tasks.

    Admitted work still consumes the global connection budget. This is a local
    worker admission bound, not a host running quota or a cluster-wide lock.
    """
    room = limit - len(active)
    if room <= 0:
        return []
    occupied = {
        _host_key(info.host)
        for run_id in active
        if not (info := active_info.get(run_id, HostDispatch(run_id, None))).admitted
        and not info.maintenance
    }
    cleanup = [item for item in maintenance if item.run_id not in active][:1]
    selected = list(cleanup) if room > 1 or prefer_maintenance else []
    # READ COMMITTED queries can observe a run move from unstarted to cleanup.
    reserved_ids = {item.run_id for item in maintenance}
    heads: dict[str, HostDispatch] = {}
    for item in candidates:
        if item.run_id not in active and item.run_id not in reserved_ids:
            heads.setdefault(_host_key(item.host), item)
    hosts = sorted(heads)
    if after_host is not None:
        hosts = [host for host in hosts if host > after_host] + [
            host for host in hosts if host <= after_host
        ]
    for host in hosts:
        if len(selected) >= room:
            break
        if host not in occupied:
            selected.append(heads[host])
            occupied.add(host)
    # Prefer fresh on alternating single-slot scans, but never waste a slot
    # when that lane is empty or its hosts are still awaiting admission.
    return selected or cleanup


def _unstarted_candidates_query(active: Sequence[UUID]) -> Select[Any]:
    # Rank within each durable host BEFORE the bounded outer window: hundreds
    # of older requests on A must not hide the first request on B.
    ranked = (
        select(
            GenerationRun.id.label("run_id"),
            ProjectCellWorkspace.orchestrator.label("host"),
            GenerationRun.created_at.label("created_at"),
            func.row_number()
            .over(
                partition_by=ProjectCellWorkspace.orchestrator,
                order_by=(GenerationRun.created_at, GenerationRun.id),
            )
            .label("host_position"),
        )
        .outerjoin(
            ProjectCellWorkspace, ProjectCellWorkspace.project_id == GenerationRun.project_id
        )
        .where(
            GenerationRun.execution_backend == "worker",
            GenerationRun.status.in_(ACTIVE_GENERATION_STATUSES),
            GenerationRun.status != "cancel_requested",
            GenerationRun.execution_started_at.is_(None),
            GenerationRun.id.not_in(active),
        )
        .subquery()
    )
    return (
        select(ranked.c.run_id, ranked.c.host)
        .where(ranked.c.host_position == 1)
        .order_by(ranked.c.created_at, ranked.c.run_id)
        .limit(100)
    )


def _maintenance_candidates_query(active: Sequence[UUID]) -> Select[Any]:
    # Ownership and unknown effects are still decided by execute_dispatch.
    return (
        select(GenerationRun.id, ProjectCellWorkspace.orchestrator)
        .outerjoin(
            ProjectCellWorkspace,
            ProjectCellWorkspace.project_id == GenerationRun.project_id,
        )
        .where(
            GenerationRun.execution_backend == "worker",
            GenerationRun.id.not_in(active),
            or_(
                GenerationRun.status == "cancel_requested",
                and_(
                    GenerationRun.status.in_(ACTIVE_GENERATION_STATUSES),
                    GenerationRun.execution_started_at.is_not(None),
                ),
                and_(
                    GenerationRun.status.not_in(ACTIVE_GENERATION_STATUSES),
                    exists().where(
                        ProjectCellOperation.execution_run_id == GenerationRun.id,
                        ProjectCellOperation.status == "running",
                    ),
                ),
            ),
        )
        .order_by(GenerationRun.created_at, GenerationRun.id)
        .limit(100)
    )


async def _load_dispatch_scan(
    session: AsyncSession, active: Sequence[UUID]
) -> tuple[dict[UUID, HostDispatch], list[HostDispatch], list[HostDispatch]]:
    info = {}
    if active:
        rows = (
            await session.execute(
                select(
                    GenerationRun.id,
                    GenerationRun.status,
                    GenerationRun.agent_state["capacity_admitted_dispatch_token"],
                    ProjectCellWorkspace.orchestrator,
                )
                .outerjoin(
                    ProjectCellWorkspace,
                    ProjectCellWorkspace.project_id == GenerationRun.project_id,
                )
                .where(GenerationRun.id.in_(active))
            )
        ).all()
        for run_id, status, token, host in rows:
            admitted = False
            if status == "running" and token is not None:
                # Match generation_runs.capacity_admitted_dispatch_token's UUID
                # semantics on the raw JSON value; don't cast JSON to SQL text.
                try:
                    UUID(str(token))
                    admitted = True
                except (TypeError, ValueError):
                    pass
            info[run_id] = HostDispatch(run_id, host, status, admitted)
    candidates = [
        HostDispatch(run_id, host)
        for run_id, host in (await session.execute(_unstarted_candidates_query(active))).all()
    ]
    maintenance = [
        HostDispatch(run_id, host, maintenance=True)
        for run_id, host in (await session.execute(_maintenance_candidates_query(active))).all()
    ]
    return info, candidates, maintenance


async def _run_dispatch_forever() -> None:
    factory = async_sessionmaker(get_engine(), expire_on_commit=False)
    active: dict[UUID, asyncio.Task[bool]] = {}
    active_info: dict[UUID, HostDispatch] = {}
    after_host: str | None = None
    after_maintenance: UUID | None = None
    prefer_maintenance = True
    while True:
        try:
            for run_id, task in list(active.items()):
                if task.done():
                    active.pop(run_id)
                    active_info.pop(run_id, None)
                    task.result()
            async with factory() as session:
                observed, candidates, maintenance = await _load_dispatch_scan(session, list(active))
            for run_id in active:
                previous = active_info.get(run_id, HostDispatch(run_id, None))
                active_info[run_id] = replace(
                    observed.get(run_id, HostDispatch(run_id, previous.host, status="unknown")),
                    maintenance=previous.maintenance,
                )
            # Avoid a still-owned orphan at the head monopolizing cleanup scans.
            maintenance_ids = [item.run_id for item in maintenance]
            if after_maintenance in maintenance_ids:
                pivot = maintenance_ids.index(after_maintenance) + 1
                maintenance = maintenance[pivot:] + maintenance[:pivot]
            limit = current_dispatch_limit()
            starting = select_host_runs_to_start(
                candidates,
                active,
                active_info,
                limit,
                maintenance=maintenance,
                after_host=after_host,
                prefer_maintenance=prefer_maintenance,
            )
            if starting:
                # One persistently lock-busy cleanup must not starve fresh work
                # when only one connection-budget slot is available.
                prefer_maintenance = not starting[0].maintenance
            for item in starting:
                active_info[item.run_id] = item
                active[item.run_id] = asyncio.create_task(execute_dispatch(item.run_id))
                if item.maintenance:
                    after_maintenance = item.run_id
                else:
                    after_host = _host_key(item.host)
            running = sum(info.status == "running" for info in active_info.values())
            waiting = sum(info.status == "queued_for_capacity" for info in active_info.values())
            await get_redis().set(
                HEARTBEAT_KEY,
                dispatch_heartbeat_payload(
                    active=len(active),
                    limit=limit,
                    configured_limit=int(get_settings().generation_worker_max_concurrent),
                    running=running,
                    waiting_capacity=waiting,
                    other=len(active) - running - waiting,
                ),
                ex=30,
            )
        except Exception:
            log.exception("generation dispatcher scan failed")
        await asyncio.sleep(2)


async def _run_terminal_notification_forever() -> None:
    factory = async_sessionmaker(get_engine(), expire_on_commit=False)
    while True:
        try:
            async with factory() as session:
                await retry_terminal_adaptation_notifications(session)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("generation adaptation terminal notification scan failed")
        await asyncio.sleep(2)


async def run_forever() -> None:
    async with asyncio.TaskGroup() as tasks:
        tasks.create_task(_run_dispatch_forever())
        tasks.create_task(_run_terminal_notification_forever())


if __name__ == "__main__":
    asyncio.run(run_forever())
