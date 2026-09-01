from __future__ import annotations

import pytest
from fastapi import FastAPI

from omnia_api import main

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
        "serving",
        "stop_listener",
        "dispose_redis",
        "dispose_engine",
    ]
