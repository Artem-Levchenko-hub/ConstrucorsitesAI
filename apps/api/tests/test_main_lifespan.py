from __future__ import annotations

import asyncio

import pytest
from fastapi import FastAPI

from omnia_api import main

# How long a test waits for a background task to reach a checkpoint. The
# assertion is that the checkpoint happens at all, not that it is fast: a
# one-second budget failed the release gate on a slow CI runner.
_EVENT_WAIT_SECONDS = 10


pytestmark = pytest.mark.asyncio


async def test_lifespan_recovers_cell_operations_before_serving(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []

    async def recover_generation_runs() -> int:
        events.append("recover_generation_runs")
        return 0

    async def recover_cell_operations() -> int:
        events.append("recover_cell_operations")
        return 1

    async def start_listener() -> None:
        events.append("start_listener")

    async def resume_capacity_queue() -> int:
        events.append("resume_capacity_queue")
        return 0

    async def stop_listener() -> None:
        events.append("stop_listener")

    async def dispose(name: str) -> None:
        events.append(name)

    monkeypatch.setattr(main, "get_engine", lambda: events.append("get_engine"))
    monkeypatch.setattr(
        main,
        "recover_interrupted_generation_runs",
        recover_generation_runs,
    )
    monkeypatch.setattr(
        main,
        "recover_interrupted_cell_operations",
        recover_cell_operations,
    )
    monkeypatch.setattr(main.hub, "start_listener", start_listener)
    monkeypatch.setattr(main, "resume_capacity_queued_generations", resume_capacity_queue)
    monkeypatch.setattr(main.hub, "stop_listener", stop_listener)
    monkeypatch.setattr(main, "dispose_redis", lambda: dispose("dispose_redis"))
    monkeypatch.setattr(main, "dispose_engine", lambda: dispose("dispose_engine"))

    async with main.lifespan(FastAPI()):
        events.append("serving")

    assert events == [
        "get_engine",
        "recover_generation_runs",
        "recover_cell_operations",
        "start_listener",
        "resume_capacity_queue",
        "serving",
        "stop_listener",
        "dispose_redis",
        "dispose_engine",
    ]


async def test_owner_wake_monitor_cannot_block_generation_capacity_recovery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    owner_started = asyncio.Event()
    generation_scanned = asyncio.Event()
    never = asyncio.Event()
    real_sleep = asyncio.sleep

    async def resume_generations() -> int:
        events.append("generations")
        generation_scanned.set()
        return 0

    async def advance_owner_wakes() -> int:
        events.append("owner_wakes")
        owner_started.set()
        await never.wait()
        return 0

    async def yield_immediately(_seconds: float) -> None:
        await real_sleep(0)

    monkeypatch.setattr(main, "resume_capacity_queued_generations", resume_generations)
    monkeypatch.setattr(main, "advance_owner_wake_operations", advance_owner_wakes)
    monkeypatch.setattr(main.asyncio, "sleep", yield_immediately)

    generation_monitor = asyncio.create_task(main._monitor_capacity_queued_generations())
    owner_monitor = asyncio.create_task(main._monitor_owner_wake_operations())
    try:
        await asyncio.wait_for(owner_started.wait(), timeout=_EVENT_WAIT_SECONDS)
        await asyncio.wait_for(generation_scanned.wait(), timeout=_EVENT_WAIT_SECONDS)
    finally:
        generation_monitor.cancel()
        owner_monitor.cancel()
        await asyncio.gather(generation_monitor, owner_monitor, return_exceptions=True)

    assert "owner_wakes" in events
    assert "generations" in events
