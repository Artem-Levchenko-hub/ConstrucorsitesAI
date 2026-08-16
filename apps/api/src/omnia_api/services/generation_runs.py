from __future__ import annotations

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

    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:project_id))"),
        {"project_id": str(project_id)},
    )

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
        run.status = "failed"
        run.error = "API process restarted before generation completed"
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
    """Release executions that cannot survive an API-process restart.

    Prompt coroutines live in the API event loop. In the current one-process
    deployment none can still be running when a fresh process starts, so an
    active DB row at startup is an interrupted execution, not real work.
    Finalising it prevents both a permanent single-flight lock and a chat row
    that looks as if it were streaming forever.
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
