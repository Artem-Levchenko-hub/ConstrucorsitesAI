from __future__ import annotations

import asyncio
import inspect
from typing import Any
from uuid import uuid4

import pytest

from omnia_api.routers import messages
from omnia_api.services import generation_continuity, queue
from omnia_api.workers import generation, preview, run


def test_worker_topology_isolates_generation_from_preview_head_of_line() -> None:
    assert run._WORKER_QUEUES == (
        queue.GENERATION_QUEUE_NAME,
        queue.QUEUE_NAME,
    )
    assert len(set(run._WORKER_QUEUES)) == 2


def test_preview_inner_wall_clock_precedes_rq_horse_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def stuck(_snapshot_id: str) -> None:
        await asyncio.Future()

    monkeypatch.setattr(preview, "_render_async", stuck)
    monkeypatch.setattr(preview, "PREVIEW_PIPELINE_TIMEOUT_SECONDS", 0)

    with pytest.raises(TimeoutError):
        preview.render_preview(str(uuid4()))

    assert preview.PREVIEW_PIPELINE_TIMEOUT_SECONDS < queue.PREVIEW_JOB_TIMEOUT_SECONDS


async def test_repeated_watchdog_enqueue_reservation_only_queues_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_id = uuid4()
    reservations = iter(["epoch-1", None])
    queued: list[tuple[object, ...]] = []

    async def reserve(
        _run_id: object, *, delay_seconds: int = 0
    ) -> str | None:
        assert delay_seconds == 0
        return next(reservations)

    def enqueue(*args: object, **kwargs: object) -> None:
        queued.append((*args, kwargs))

    monkeypatch.setattr(generation_continuity, "reserve_enqueue", reserve)
    monkeypatch.setattr(queue, "enqueue_generation_run", enqueue)

    assert await generation_continuity.enqueue_run_durably(run_id) is True
    assert await generation_continuity.enqueue_run_durably(run_id) is False
    assert len(queued) == 1
    assert queued[0][:2] == (run_id, "epoch-1")


async def test_lost_enqueue_ack_clears_only_its_reservation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_id = uuid4()
    cleared: list[tuple[object, str]] = []

    async def reserve(_run_id: object, *, delay_seconds: int = 0) -> str:
        assert delay_seconds == 0
        return "lost-ack-token"

    async def clear(target: object, token: str) -> None:
        cleared.append((target, token))

    def enqueue(*_args: object, **_kwargs: object) -> None:
        raise ConnectionError("ack lost")

    monkeypatch.setattr(generation_continuity, "reserve_enqueue", reserve)
    monkeypatch.setattr(generation_continuity, "clear_enqueue_reservation", clear)
    monkeypatch.setattr(queue, "enqueue_generation_run", enqueue)

    with pytest.raises(ConnectionError, match="ack lost"):
        await generation_continuity.enqueue_run_durably(run_id)
    assert cleared == [(run_id, "lost-ack-token")]


def test_predeploy_duplicate_job_without_token_is_harmless(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    called = False

    def forbidden_run(_coro: Any) -> None:
        nonlocal called
        called = True

    monkeypatch.setattr(generation.asyncio, "run", forbidden_run)

    generation.run_generation_job(str(uuid4()))

    assert called is False


def test_worker_shutdown_reaps_and_kills_uncooperative_children() -> None:
    class Process:
        def __init__(self, *, remains_alive: bool) -> None:
            self.remains_alive = remains_alive
            self.terminated = 0
            self.killed = 0
            self.joined: list[int] = []
            self.closed = 0

        def is_alive(self) -> bool:
            return self.remains_alive

        def terminate(self) -> None:
            self.terminated += 1

        def kill(self) -> None:
            self.killed += 1
            self.remains_alive = False

        def join(self, timeout: int) -> None:
            self.joined.append(timeout)

        def close(self) -> None:
            self.closed += 1

    graceful = Process(remains_alive=False)
    stuck = Process(remains_alive=True)

    run._shutdown_workers({"graceful": graceful, "stuck": stuck})  # type: ignore[arg-type]

    assert graceful.terminated == 0
    assert graceful.killed == 0
    assert graceful.joined == [10]
    assert graceful.closed == 1
    assert stuck.terminated == 1
    assert stuck.killed == 1
    assert stuck.joined == [10, 5]
    assert stuck.closed == 1


def test_worker_tree_uses_direct_process_signal_off_posix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Process:
        pid = 101

        def __init__(self) -> None:
            self.terminated = 0
            self.killed = 0

        def terminate(self) -> None:
            self.terminated += 1

        def kill(self) -> None:
            self.killed += 1

    process = Process()
    monkeypatch.setattr(run.os, "name", "nt")

    run._signal_worker_tree(process, force=False)  # type: ignore[arg-type]
    run._signal_worker_tree(process, force=True)  # type: ignore[arg-type]

    assert process.terminated == 1
    assert process.killed == 1


def test_continuation_control_flow_cannot_be_swallowed_by_process_prompt() -> None:
    source = inspect.getsource(messages._process_prompt)
    durable_except = source.index("except GenerationContinuationRequired:")
    generic_except = source.index("except Exception as e:", durable_except)

    assert durable_except < generic_except
    assert "raise" in source[durable_except:generic_except]


async def test_continuation_never_finalizes_or_clears_checkpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_id = uuid4()
    project_id = uuid4()
    message_id = uuid4()
    finalized: list[object] = []
    cleared: list[object] = []
    enqueued: list[tuple[object, int]] = []

    class RunRow:
        def __init__(self) -> None:
            self.agent_state = {"execution_envelope": {"version": 1}}

    class Session:
        async def get(self, _model: object, _key: object) -> RunRow:
            return RunRow()

    class Context:
        async def __aenter__(self) -> Session:
            return Session()

        async def __aexit__(self, *_args: object) -> None:
            return None

    class Factory:
        def __call__(self) -> Context:
            return Context()

    async def work() -> None:
        raise generation_continuity.GenerationContinuationRequired(
            "no_ai_write", delay_seconds=7
        )

    async def wait_cancel(_run_id: object) -> None:
        await asyncio.Future()

    async def schedule(
        _run_id: object, _reason: str
    ) -> generation_continuity.ContinuationDecision:
        return generation_continuity.ContinuationDecision(
            True, "internal_repair", 5, "continue"
        )

    async def enqueue(target: object, *, delay_seconds: int = 0) -> bool:
        enqueued.append((target, delay_seconds))
        return True

    async def finalize(target: object) -> str:
        finalized.append(target)
        return "failed"

    async def clear(target: object) -> None:
        cleared.append(target)

    async def noop(*_args: object, **_kwargs: object) -> None:
        return None

    monkeypatch.setattr(messages, "get_engine", lambda: object())
    monkeypatch.setattr(messages, "async_sessionmaker", lambda *_a, **_kw: Factory())
    monkeypatch.setattr(messages, "set_generation_run_status", noop)
    monkeypatch.setattr(messages, "_wait_for_generation_cancel", wait_cancel)
    monkeypatch.setattr(messages, "publish_event", noop)
    monkeypatch.setattr(messages, "finalize_generation_run", finalize)
    monkeypatch.setattr(
        messages.orchestrator_client,
        "bind_hot_reload_tracker",
        lambda *_a, **_kw: object(),
    )
    monkeypatch.setattr(
        messages.orchestrator_client,
        "reset_hot_reload_tracker",
        lambda *_a, **_kw: None,
    )
    monkeypatch.setattr(generation_continuity, "schedule_continuation", schedule)
    monkeypatch.setattr(generation_continuity, "enqueue_run_durably", enqueue)
    monkeypatch.setattr(generation_continuity, "clear_native_checkpoint", clear)

    await messages._run_tracked_prompt(
        work(),
        run_id=run_id,
        project_id=project_id,
        assistant_message_id=message_id,
        label="continuation-regression",
    )

    assert enqueued == [(run_id, 7)]
    assert finalized == []
    assert cleared == []
