from __future__ import annotations

import asyncio
import logging
from collections.abc import Coroutine
from contextlib import suppress
from datetime import UTC, datetime
from typing import Any, Literal, cast
from uuid import UUID, uuid4

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from omnia_api.core.db import get_engine
from omnia_api.core.redis import (
    clear_generation_cancel,
    generation_cancel_requested,
    publish_event,
)
from omnia_api.models.generation_run import GenerationRun
from omnia_api.models.message import Message
from omnia_api.services.generation.lightweight_turns import (
    _run_async_onboarding,
    _run_clarify,
    _run_text_turn,
)
from omnia_api.services.generation_runs import (
    ACTIVE_GENERATION_STATUSES,
    GenerationDispatch,
    apply_cancelled_generation_locked,
    finalize_generation_run,
    load_generation_dispatch,
    set_generation_run_status,
    write_capacity_dispatch_claim,
)
from omnia_api.services.project_cell_capacity import (
    capacity_admission_event,
    clear_capacity_admission_event,
)

_BACKGROUND_TASKS: set[asyncio.Task[None]] = set()


_PROMPT_TASKS: dict[UUID, asyncio.Task[None]] = {}


_CAPACITY_DISPATCH_LEASE_SECONDS = 30


_CAPACITY_DISPATCH_HEARTBEAT_SECONDS = 10


def _capacity_dispatch_lock_key(run_id: UUID) -> int:
    """Return a stable PostgreSQL-compatible signed bigint for diagnostics/tests."""

    raw = ((run_id.int >> 64) ^ run_id.int) & ((1 << 64) - 1)
    return raw - (1 << 64) if raw >= (1 << 63) else raw


async def _try_claim_capacity_dispatch(session: AsyncSession, run_id: UUID) -> bool:
    """Probe the database advisory primitive used by integration race tests."""

    result = await session.execute(
        text("SELECT pg_try_advisory_xact_lock(:lock_key)"),
        {"lock_key": _capacity_dispatch_lock_key(run_id)},
    )
    return bool(result.scalar_one())


def _capacity_dispatch_claim(run: GenerationRun) -> tuple[UUID, datetime] | None:
    state = run.agent_state if isinstance(run.agent_state, dict) else {}
    raw = state.get("capacity_dispatch_claim")
    if not isinstance(raw, dict) or set(raw) != {"token", "expires_at"}:
        return None
    try:
        token = UUID(str(raw["token"]))
        expires_at = datetime.fromisoformat(str(raw["expires_at"]))
    except (TypeError, ValueError):
        return None
    if expires_at.tzinfo is None:
        return None
    return token, expires_at.astimezone(UTC)


def _write_capacity_dispatch_claim(
    run: GenerationRun,
    *,
    token: UUID,
    now: datetime | None = None,
) -> None:
    write_capacity_dispatch_claim(
        run,
        token=token,
        lease_seconds=_CAPACITY_DISPATCH_LEASE_SECONDS,
        now=now,
    )


async def _renew_capacity_dispatch_claim(run_id: UUID, token: UUID) -> str:
    """Renew only a queued lease; identify pre-queue and admitted states."""

    factory = async_sessionmaker(get_engine(), expire_on_commit=False)
    async with factory() as session:
        run = (
            await session.execute(
                select(GenerationRun).where(GenerationRun.id == run_id).with_for_update()
            )
        ).scalar_one_or_none()
        if run is None or run.status not in ACTIVE_GENERATION_STATUSES:
            return "terminal"
        if run.status == "pending":
            return "prequeue"
        if run.status == "running":
            return "admitted"
        claim = _capacity_dispatch_claim(run)
        if claim is None or claim[0] != token:
            return "lost"
        _write_capacity_dispatch_claim(run, token=token)
        await session.commit()
        return "renewed"


async def _wait_for_capacity_dispatch_lease_loss(run_id: UUID, token: UUID) -> str:
    valid_until = asyncio.get_running_loop().time() + _CAPACITY_DISPATCH_LEASE_SECONDS
    entered_queue = False
    while True:
        await asyncio.sleep(_CAPACITY_DISPATCH_HEARTBEAT_SECONDS)
        try:
            state = await _renew_capacity_dispatch_claim(run_id, token)
        except Exception:
            if entered_queue and asyncio.get_running_loop().time() >= valid_until:
                return "lost"
            continue
        if state == "lost":
            return "lost"
        if state in {"admitted", "terminal"}:
            return "closed"
        if state == "renewed":
            entered_queue = True
            valid_until = asyncio.get_running_loop().time() + _CAPACITY_DISPATCH_LEASE_SECONDS


async def _wait_for_generation_cancel(run_id: UUID) -> None:
    while True:
        try:
            if await generation_cancel_requested(run_id):
                return
        except Exception:
            # Redis cancellation is best-effort per poll; a transient miss must
            # not abort an otherwise healthy generation.
            pass
        await asyncio.sleep(0.25)


async def _finalize_cancelled_generation(
    project_id: UUID,
    assistant_message_id: UUID,
    run_id: UUID,
) -> None:
    factory = async_sessionmaker(get_engine(), expire_on_commit=False)
    async with factory() as session:
        run = await session.scalar(
            select(GenerationRun).where(GenerationRun.id == run_id).with_for_update()
        )
        if run is None or run.status not in ACTIVE_GENERATION_STATUSES:
            return
        await apply_cancelled_generation_locked(session, run)
        await session.commit()
    await publish_event(
        project_id,
        "generation.cancelled",
        {
            "run_id": str(run_id),
            "message_id": str(assistant_message_id),
        },
    )


def _generation_cancel_protocol(
    current_status: str,
) -> Literal["terminal_without_signal", "signal_running", "already_requested"]:
    if current_status in {"pending", "queued_for_capacity"}:
        return "terminal_without_signal"
    if current_status == "running":
        return "signal_running"
    return "already_requested"


async def _run_tracked_prompt(
    work: Coroutine[Any, Any, None],
    *,
    run_id: UUID,
    project_id: UUID,
    assistant_message_id: UUID,
    label: str,
    capacity_dispatch_token: UUID | None = None,
) -> None:
    """Run one prompt task while a Redis watcher makes Stop process-safe."""

    if capacity_dispatch_token is None:
        await set_generation_run_status(run_id, "running")
    work_task = asyncio.create_task(work)
    cancel_task = asyncio.create_task(_wait_for_generation_cancel(run_id))
    lease_task = (
        asyncio.create_task(_wait_for_capacity_dispatch_lease_loss(run_id, capacity_dispatch_token))
        if capacity_dispatch_token is not None
        else None
    )
    admission_task = (
        asyncio.create_task(capacity_admission_event(run_id).wait())
        if capacity_dispatch_token is not None
        else None
    )
    try:
        while True:
            waiters: set[asyncio.Task[Any]] = {work_task, cancel_task}
            if lease_task is not None:
                waiters.add(lease_task)
            if admission_task is not None:
                waiters.add(admission_task)
            done, _ = await asyncio.wait(waiters, return_when=asyncio.FIRST_COMPLETED)
            if admission_task is not None and admission_task in done:
                admission_task = None
                if lease_task is not None:
                    lease_task.cancel()
                    with suppress(asyncio.CancelledError):
                        await lease_task
                    lease_task = None
                continue
            if lease_task is not None and lease_task in done:
                lease_result = await lease_task
                lease_task = None
                if lease_result == "lost":
                    work_task.cancel()
                    cancel_task.cancel()
                    with suppress(asyncio.CancelledError):
                        await work_task
                    return
                continue
            break
        if cancel_task in done:
            work_task.cancel()
            try:
                await work_task
            except asyncio.CancelledError:
                pass
            except Exception as exc:
                logging.getLogger("omnia_api.routers.messages").warning(
                    "%s cleanup failed during cancellation",
                    label,
                    exc_info=exc,
                )
            await _finalize_cancelled_generation(project_id, assistant_message_id, run_id)
            return

        cancel_task.cancel()
        with suppress(asyncio.CancelledError):
            await cancel_task
        await work_task
        await finalize_generation_run(run_id)
    except asyncio.CancelledError:
        work_task.cancel()
        cancel_task.cancel()
        with suppress(asyncio.CancelledError):
            await work_task
        await _finalize_cancelled_generation(project_id, assistant_message_id, run_id)
    except Exception as exc:
        logging.getLogger("omnia_api.routers.messages").error("%s failed", label, exc_info=exc)
        await set_generation_run_status(
            run_id,
            "failed",
            error=f"{type(exc).__name__}: {exc}",
        )
        await _emergency_error(
            project_id,
            assistant_message_id,
            f"{type(exc).__name__}: {exc}",
        )
    finally:
        cancel_task.cancel()
        if lease_task is not None:
            lease_task.cancel()
        if admission_task is not None:
            admission_task.cancel()
        clear_capacity_admission_event(run_id)
        with suppress(Exception):
            await clear_generation_cancel(run_id)


def _spawn_tracked_prompt(
    work: Coroutine[Any, Any, None],
    *,
    run_id: UUID,
    project_id: UUID,
    assistant_message_id: UUID,
    label: str,
    capacity_dispatch_token: UUID | None = None,
) -> None:
    task = asyncio.create_task(
        _run_tracked_prompt(
            work,
            run_id=run_id,
            project_id=project_id,
            assistant_message_id=assistant_message_id,
            label=label,
            capacity_dispatch_token=capacity_dispatch_token,
        )
    )
    _BACKGROUND_TASKS.add(task)
    _PROMPT_TASKS[run_id] = task

    def _cleanup(done: asyncio.Task[None]) -> None:
        _BACKGROUND_TASKS.discard(done)
        if _PROMPT_TASKS.get(run_id) is done:
            _PROMPT_TASKS.pop(run_id, None)

    task.add_done_callback(_cleanup)


async def _emergency_error(project_id: UUID, assistant_message_id: UUID, err: str) -> None:
    """Last-resort recovery when ``_process_prompt`` dies before it ever
    published an ``llm.error`` itself.

    Always publishes the WS event (so the frontend's ``apply()`` handler
    flips ``streamingRef`` and shows a toast) AND finalises the
    assistant message in DB with a human-readable error body + zero
    tokens (so the chat row stops looking "still streaming").

    Each step is wrapped — we'd rather log a secondary failure than
    leave the user staring at a stuck spinner.
    """
    import logging as _emerg_log

    _elog = _emerg_log.getLogger("omnia_api.routers.messages")
    try:
        await publish_event(
            project_id,
            "llm.error",
            {"message_id": str(assistant_message_id), "error": err[:500]},
        )
    except Exception as pub_exc:
        _elog.error("emergency publish_event failed: %r", pub_exc)
    try:
        factory = async_sessionmaker(get_engine(), expire_on_commit=False)
        async with factory() as session:
            msg = await session.get(Message, assistant_message_id)
            if msg is not None and msg.tokens_out is None:
                # Keep any partial content the model managed to stream.
                if not msg.content:
                    msg.content = f"[Ошибка: {err[:200]}]"
                msg.tokens_out = 0
                msg.tokens_in = msg.tokens_in or 0
                await session.commit()
    except Exception as db_exc:
        _elog.error("emergency finalize failed: %r", db_exc)


def _spawn_process_prompt(
    *,
    run_id: UUID,
    capacity_dispatch_token: UUID | None = None,
    **kwargs: object,
) -> None:
    """Fire-and-forget _process_prompt with a guaranteed strong reference.

    Any exception that escapes the coroutine is BOTH logged via structlog
    fallback AND surfaced to the frontend via ``llm.error`` + DB finalize
    so the user never sees a stuck "AI читает контекст" spinner — the
    chat row gets an explicit error body and ``streamingRef`` unlocks.
    """
    project_id: UUID = kwargs["project_id"]  # type: ignore[assignment]
    assistant_message_id: UUID = kwargs["assistant_message_id"]  # type: ignore[assignment]
    typed_kwargs = cast(dict[str, Any], kwargs)
    from omnia_api.services.generation.lifecycle import _process_prompt

    _spawn_tracked_prompt(
        _process_prompt(
            run_id=run_id,
            capacity_dispatch_token=capacity_dispatch_token,
            **typed_kwargs,
        ),
        run_id=run_id,
        project_id=project_id,
        assistant_message_id=assistant_message_id,
        label="_process_prompt",
        capacity_dispatch_token=capacity_dispatch_token,
    )


async def resume_capacity_queued_generations() -> int:
    factory = async_sessionmaker(get_engine(), expire_on_commit=False)
    async with factory() as session:
        runs = list(
            (
                await session.execute(
                    select(GenerationRun)
                    .where(
                        GenerationRun.status == "queued_for_capacity",
                        GenerationRun.execution_backend == "api",
                    )
                    .order_by(GenerationRun.created_at, GenerationRun.id)
                    .with_for_update(skip_locked=True)
                )
            )
            .scalars()
            .all()
        )
        dispatches: list[tuple[GenerationRun, GenerationDispatch, UUID]] = []
        now = datetime.now(UTC)
        for run in runs:
            if run.id in _PROMPT_TASKS:
                continue
            if not await _try_claim_capacity_dispatch(session, run.id):
                continue
            claim = _capacity_dispatch_claim(run)
            if claim is not None and claim[1] > now:
                continue
            try:
                dispatch = load_generation_dispatch(run)
                user_message = await session.get(Message, dispatch.user_message_id)
                assistant_message = await session.get(Message, dispatch.assistant_message_id)
                if (
                    run.assistant_message_id != dispatch.assistant_message_id
                    or run.user_message_id != dispatch.user_message_id
                    or user_message is None
                    or assistant_message is None
                    or user_message.project_id != dispatch.project_id
                    or assistant_message.project_id != dispatch.project_id
                    or user_message.role != "user"
                    or assistant_message.role != "assistant"
                ):
                    raise ValueError("generation dispatch message ownership mismatch")
            except ValueError as exc:
                run.status = "failed"
                run.error = f"invalid queued dispatch: {exc}"[:2000]
                run.finished_at = datetime.now(UTC)
                continue
            claim_token = uuid4()
            _write_capacity_dispatch_claim(run, token=claim_token, now=now)
            dispatches.append((run, dispatch, claim_token))
        await session.commit()
        for run, dispatch, claim_token in dispatches:
            _spawn_process_prompt(
                run_id=run.id,
                capacity_dispatch_token=claim_token,
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
        return len(dispatches)


def _spawn_clarify(
    project_id: UUID,
    assistant_message_id: UUID,
    prompt: str,
    *,
    run_id: UUID,
    language: str = "ru",
) -> None:
    """Fire-and-forget _run_clarify with a strong ref + error finalize (mirrors
    _spawn_process_prompt, so a clarify failure never hangs the UI spinner)."""
    _spawn_tracked_prompt(
        _run_clarify(project_id, assistant_message_id, prompt, language=language),
        run_id=run_id,
        project_id=project_id,
        assistant_message_id=assistant_message_id,
        label="_run_clarify",
    )


def _spawn_text_turn(
    project_id: UUID,
    assistant_message_id: UUID,
    text: str,
    *,
    run_id: UUID,
) -> None:
    """Fire-and-forget _run_text_turn with a strong ref + error finalize (so a
    publish hiccup never hangs the UI spinner; mirrors _spawn_clarify)."""
    _spawn_tracked_prompt(
        _run_text_turn(project_id, assistant_message_id, text),
        run_id=run_id,
        project_id=project_id,
        assistant_message_id=assistant_message_id,
        label="_run_text_turn",
    )


def _spawn_async_onboarding(
    project_id: UUID,
    assistant_message_id: UUID,
    prompt: str,
    language: str,
    *,
    run_id: UUID,
) -> None:
    """Fire-and-forget _run_async_onboarding with a strong ref + error finalize (so
    a plan/publish hiccup never hangs the UI spinner; mirrors _spawn_text_turn)."""
    _spawn_tracked_prompt(
        _run_async_onboarding(project_id, assistant_message_id, prompt, language),
        run_id=run_id,
        project_id=project_id,
        assistant_message_id=assistant_message_id,
        label="_run_async_onboarding",
    )
