from __future__ import annotations

import asyncio
import hashlib
from datetime import UTC, datetime
from uuid import UUID

from fastapi import status
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from omnia_api.core.errors import ApiError
from omnia_api.models.generation_run import GenerationRun
from omnia_api.models.message import Message

ACTIVE_GENERATION_STATUSES = ("pending", "running", "cancel_requested")


def prompt_hash(prompt: str) -> str:
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()


async def _acquire_generation_lock(session: AsyncSession, project_id: UUID) -> None:
    """Acquire the project slot without waiting for the DB command timeout.

    Runtime startup and snapshot/config reconciliation use the same transaction
    lock while they call the orchestrator. A blocking advisory lock turns that
    normal overlap into an unhandled asyncpg ``TimeoutError``. Fail as an
    explicit retryable conflict instead; the first MAX build is submitted before
    its preview starts, so this path only covers genuine concurrent activity.
    """

    acquired = (
        await session.execute(
            text("SELECT pg_try_advisory_xact_lock(hashtext(:project_id))"),
            {"project_id": str(project_id)},
        )
    ).scalar_one()
    if not acquired:
        raise ApiError(
            "conflict",
            "Проект ещё подготавливается. Повторите отправку через несколько секунд.",
            status.HTTP_409_CONFLICT,
            details={"reason": "project_busy"},
        )


async def reserve_generation_run(
    session: AsyncSession,
    *,
    project_id: UUID,
    user_id: UUID,
    idempotency_key: str,
    prompt: str,
) -> tuple[GenerationRun, bool]:
    """Atomically reserve the only active execution slot for a project.

    The transaction-scoped advisory lock serialises the check+insert across API
    processes. A retry with the same key replays the original run. A different
    request while work is active is rejected instead of racing the same repo.
    """

    await _acquire_generation_lock(session, project_id)

    existing = (
        await session.execute(
            select(GenerationRun).where(
                GenerationRun.project_id == project_id,
                GenerationRun.idempotency_key == idempotency_key,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        if existing.prompt_hash != prompt_hash(prompt):
            raise ApiError(
                "conflict",
                "idempotency key was already used for another prompt",
                status.HTTP_409_CONFLICT,
                details={"run_id": str(existing.id)},
            )
        return existing, True

    active = (
        await session.execute(
            select(GenerationRun)
            .where(
                GenerationRun.project_id == project_id,
                GenerationRun.status.in_(ACTIVE_GENERATION_STATUSES),
            )
            .order_by(GenerationRun.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()

    # The assistant row is finalised before llm.done is published. Close this
    # tiny lifecycle gap here so a queued prompt arriving on llm.done is accepted
    # even if the task callback has not updated generation_runs yet.
    if active is not None and active.assistant_message_id is not None:
        assistant = await session.get(Message, active.assistant_message_id)
        if assistant is not None and assistant.tokens_out is not None:
            build_failed = active.response_mode == "build" and assistant.snapshot_id is None
            active.status = "failed" if build_failed else "completed"
            if build_failed and not active.error:
                active.error = "build finished without a committed snapshot"
            active.finished_at = datetime.now(UTC)
            await session.flush()
            active = None

    if active is not None:
        raise ApiError(
            "conflict",
            "generation already in progress",
            status.HTTP_409_CONFLICT,
            details={
                "active_run_id": str(active.id),
                "active_message_id": (
                    str(active.assistant_message_id) if active.assistant_message_id else None
                ),
                "active_status": active.status,
            },
        )

    run = GenerationRun(
        project_id=project_id,
        user_id=user_id,
        idempotency_key=idempotency_key,
        prompt_hash=prompt_hash(prompt),
        status="pending",
    )
    session.add(run)
    await session.flush()
    return run, False


async def _recover_interrupted_generation_runs(session: AsyncSession) -> int:
    runs = list(
        (
            await session.execute(
                select(GenerationRun)
                .where(GenerationRun.status.in_(ACTIVE_GENERATION_STATUSES))
                .with_for_update()
            )
        )
        .scalars()
        .all()
    )
    if not runs:
        return 0

    now = datetime.now(UTC)
    marker = "[Генерация прервана перезапуском сервера — отправьте запрос повторно]"
    for run in runs:
        run_state = run.agent_state or {}
        continuity = run_state.get("continuity")
        envelope = run_state.get("execution_envelope")
        durable = isinstance(continuity, dict) and isinstance(envelope, dict)
        cleanup_paths_raw = run_state.get("cleanup_paths")
        cleanup_required = bool(run_state.get("cleanup_required"))
        # Durable running work owns its private live checkpoint and must not be
        # rolled back by an API-only restart. Legacy interrupted coroutines and
        # explicit cancellations still restore the canonical snapshot first.
        should_cleanup = run.status == "cancel_requested" or not durable
        if should_cleanup and cleanup_required and isinstance(cleanup_paths_raw, list):
            cleanup_paths = {
                path
                for path in cleanup_paths_raw
                if isinstance(path, str) and path and len(path) <= 500
            }
            if cleanup_paths:
                try:
                    from omnia_api.models.project import Project
                    from omnia_api.models.snapshot import Snapshot
                    from omnia_api.services import orchestrator_client
                    from omnia_api.services import repo as repo_svc

                    await session.execute(
                        text("SELECT pg_advisory_xact_lock(hashtext(:project_id))"),
                        {"project_id": str(run.project_id)},
                    )
                    project = await session.get(Project, run.project_id)
                    if project is None or project.current_snapshot_id is None:
                        raise RuntimeError("canonical project snapshot is unavailable")
                    snapshot = await session.get(Snapshot, project.current_snapshot_id)
                    if snapshot is None:
                        raise RuntimeError("canonical snapshot is unavailable")
                    canonical = await asyncio.to_thread(
                        repo_svc.read_files,
                        run.project_id,
                        snapshot.commit_sha,
                    )
                    patch = {path: canonical.get(path, "") for path in cleanup_paths}
                    await orchestrator_client.hot_reload_exact(
                        run.project_id,
                        project.slug,
                        patch,
                    )
                    project.runtime_sync_required = False
                    project.runtime_sync_paths = []
                except Exception:
                    # Fail closed: keep cancel_requested inside the partial
                    # unique index until a later restart/recovery can restore
                    # the canonical files.
                    run.status = "cancel_requested"
                    run.error = "runtime_cleanup_pending_after_restart"
                    continue
                run.status = "cancelled"
                run.error = None
                run.finished_at = now
                if run.assistant_message_id is not None:
                    message = await session.get(Message, run.assistant_message_id)
                    if message is not None and message.tokens_out is None:
                        cancel_marker = "[Отменено пользователем]"
                        if cancel_marker not in (message.content or ""):
                            message.content = (
                                f"{message.content.rstrip()}\n\n{cancel_marker}".strip()
                            )
                        message.tokens_in = message.tokens_in or 0
                        message.tokens_out = 0
                continue
        # Durable MAX/RQ runs survive API and worker restarts. Clear only their
        # process lease; the watchdog re-enqueues the same run/message and the
        # partial runtime paths remain private until contract_green.
        if durable:
            assert isinstance(continuity, dict)
            lease_expires_at: datetime | None = None
            raw_lease_expiry = continuity.get("lease_expires_at")
            if isinstance(raw_lease_expiry, str) and raw_lease_expiry:
                try:
                    lease_expires_at = datetime.fromisoformat(raw_lease_expiry)
                    if lease_expires_at.tzinfo is None:
                        lease_expires_at = lease_expires_at.replace(tzinfo=UTC)
                except ValueError:
                    lease_expires_at = None
            # API and worker are separate processes in production. An API
            # restart must not steal a still-heartbeating worker lease; the
            # watchdog reclaims only expired/absent owners.
            if lease_expires_at is not None and lease_expires_at > now:
                continue
            state = dict(run.agent_state or {})
            continuity = dict(continuity)
            continuity.update(
                {
                    "status": "queued",
                    "lease_owner": None,
                    "lease_expires_at": None,
                    "classification": "process_restart_recovery",
                    "retryable": True,
                    "action": "Продолжить тот же run с последнего durable checkpoint.",
                }
            )
            state["continuity"] = continuity
            run.agent_state = state
            run.status = "pending"
            run.error = None
            run.finished_at = None
            continue
        run.status = "failed"
        run.error = "api_process_restarted"
        run.finished_at = now
        if run.assistant_message_id is None:
            continue
        message = await session.get(Message, run.assistant_message_id)
        if message is None or message.tokens_out is not None:
            continue
        if marker not in (message.content or ""):
            message.content = f"{message.content.rstrip()}\n\n{marker}".strip()
        message.tokens_in = message.tokens_in or 0
        message.tokens_out = 0

    await session.commit()
    return len(runs)


async def recover_interrupted_generation_runs(
    session: AsyncSession | None = None,
) -> int:
    """Recover durable queued runs and terminalise only legacy coroutines.

    RQ-owned MAX runs keep their run/message/checkpoint and are reclaimed by the
    watchdog. Historical fire-and-forget rows lack an execution envelope and
    still fail honestly so they cannot occupy the project slot forever.
    """

    if session is not None:
        return await _recover_interrupted_generation_runs(session)

    from omnia_api.core.db import get_engine

    factory = async_sessionmaker(get_engine(), expire_on_commit=False)
    async with factory() as own_session:
        return await _recover_interrupted_generation_runs(own_session)


async def set_generation_run_status(
    run_id: UUID,
    new_status: str,
    *,
    error: str | None = None,
) -> None:
    """Update lifecycle state from a fire-and-forget task in its own session."""

    from omnia_api.core.db import get_engine

    factory = async_sessionmaker(get_engine(), expire_on_commit=False)
    async with factory() as session:
        run = await session.get(GenerationRun, run_id)
        if run is None:
            return
        now = datetime.now(UTC)
        run.status = new_status
        if new_status == "running" and run.started_at is None:
            run.started_at = now
        if new_status in {"cancelled", "completed", "failed"}:
            run.finished_at = now
        if error is not None:
            run.error = error[:2000]
        await session.commit()


async def set_generation_run_error(
    run_id: UUID,
    error_code: str,
    *,
    preserve_existing: bool = True,
    session: AsyncSession | None = None,
) -> str | None:
    """Persist a stable terminal reason while work is still being reconciled.

    The run remains active until snapshot/rollback reconciliation finishes. This
    avoids releasing the project lock while partial runtime files may still be
    visible, while ensuring the later generic finalizer cannot erase the cause.
    """

    async def _persist(db: AsyncSession) -> str | None:
        run = await db.get(GenerationRun, run_id)
        if run is None:
            return None
        if not preserve_existing or not run.error:
            run.error = error_code[:2000]
        primary_error = run.error
        await db.commit()
        return primary_error

    if session is not None:
        return await _persist(session)

    from omnia_api.core.db import get_engine

    factory = async_sessionmaker(get_engine(), expire_on_commit=False)
    async with factory() as own_session:
        return await _persist(own_session)


async def save_generation_agent_state(
    run_id: UUID,
    state: dict[str, object],
    session: AsyncSession | None = None,
) -> None:
    """Persist a bounded, observable agent checkpoint from the background task."""

    def _with_runtime_state(
        previous: dict[str, object] | None,
        incoming: dict[str, object],
    ) -> dict[str, object]:
        result = dict(incoming)
        for key in (
            "continuity",
            "execution_envelope",
            "cleanup_required",
            "cleanup_paths",
        ):
            if key not in result and previous and key in previous:
                result[key] = previous[key]
        return result

    if session is not None:
        run = await session.get(GenerationRun, run_id)
        if run is None:
            return
        run.agent_state = _with_runtime_state(run.agent_state, state)
        await session.commit()
        return

    from omnia_api.core.db import get_engine

    factory = async_sessionmaker(get_engine(), expire_on_commit=False)
    async with factory() as session:
        run = await session.get(GenerationRun, run_id)
        if run is None:
            return
        run.agent_state = _with_runtime_state(run.agent_state, state)
        await session.commit()


async def merge_generation_agent_state(
    run_id: UUID,
    state: dict[str, object],
) -> None:
    """Merge a durable runtime-safety checkpoint without erasing the public plan."""

    from omnia_api.core.db import get_engine

    factory = async_sessionmaker(get_engine(), expire_on_commit=False)
    async with factory() as session:
        run = await session.get(GenerationRun, run_id, with_for_update=True)
        if run is None:
            return
        run.agent_state = {**(run.agent_state or {}), **state}
        await session.commit()


async def latest_failed_agent_state(
    session: AsyncSession,
    *,
    project_id: UUID,
    exclude_run_id: UUID | None = None,
) -> dict[str, object] | None:
    """Return the newest failed checkpoint that a user retry can continue."""

    statement = (
        select(GenerationRun)
        .where(
            GenerationRun.project_id == project_id,
            GenerationRun.status == "failed",
        )
        .order_by(GenerationRun.created_at.desc())
        .limit(1)
    )
    if exclude_run_id is not None:
        statement = statement.where(GenerationRun.id != exclude_run_id)
    run = (await session.execute(statement)).scalar_one_or_none()
    if run is None or not run.agent_state:
        return None
    return dict(run.agent_state)


async def _finalize_generation_run(session: AsyncSession, run_id: UUID) -> str:
    run = await session.get(GenerationRun, run_id)
    if run is None:
        return "failed"
    message = (
        await session.get(Message, run.assistant_message_id)
        if run.assistant_message_id is not None
        else None
    )
    build_failed = run.response_mode == "build" and (message is None or message.snapshot_id is None)
    run.status = "failed" if build_failed else "completed"
    run.finished_at = datetime.now(UTC)
    if build_failed and not run.error:
        run.error = "build finished without a committed snapshot"
    if run.agent_state:
        run.agent_state = {
            **run.agent_state,
            "cleanup_required": False,
            "cleanup_paths": [],
        }
    await session.commit()
    return run.status


async def finalize_generation_run(
    run_id: UUID,
    session: AsyncSession | None = None,
) -> str:
    """Finish a run according to its product outcome, not coroutine survival.

    A build coroutine can terminate normally after the agent reports a red build
    or an unavailable container.  Historically that was recorded as
    ``completed`` even though no snapshot existed.  Build turns are successful
    only when their assistant message points at a committed snapshot; clarify
    turns and legitimate edit no-ops remain successful without one.
    """

    if session is not None:
        return await _finalize_generation_run(session, run_id)

    from omnia_api.core.db import get_engine

    factory = async_sessionmaker(get_engine(), expire_on_commit=False)
    async with factory() as own_session:
        return await _finalize_generation_run(own_session, run_id)


async def _reconcile_completed_build_runs(session: AsyncSession) -> int:
    runs = list(
        (
            await session.execute(
                select(GenerationRun).where(
                    GenerationRun.status == "completed",
                    GenerationRun.response_mode == "build",
                )
            )
        )
        .scalars()
        .all()
    )
    changed = 0
    for run in runs:
        message = (
            await session.get(Message, run.assistant_message_id)
            if run.assistant_message_id is not None
            else None
        )
        if message is not None and message.snapshot_id is not None:
            continue
        run.status = "failed"
        run.error = run.error or "build finished without a committed snapshot"
        run.finished_at = run.finished_at or datetime.now(UTC)
        changed += 1
    await session.commit()
    return changed


async def reconcile_completed_build_runs(
    session: AsyncSession | None = None,
) -> int:
    """Correct historical builds that were labelled complete without a snapshot."""

    if session is not None:
        return await _reconcile_completed_build_runs(session)

    from omnia_api.core.db import get_engine

    factory = async_sessionmaker(get_engine(), expire_on_commit=False)
    async with factory() as own_session:
        return await _reconcile_completed_build_runs(own_session)
