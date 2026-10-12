"""Productive adaptation and ordinary repairs use time, not a fixed round count."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from tests.test_max_finalization_progress import loop as loop
from yleum_api.services import max_finalization as finalization
from yleum_api.services.generation_deadline import is_restoration_adaptation
from yleum_api.services.max_finalization import MaxFinalizationStatus


@pytest.fixture(params=[False, True], ids=["ordinary", "adaptation"])
def repair_loop(loop, request):
    coordinator, files, builds, run = loop
    run.id = uuid4()
    if request.param:
        run.agent_state = {"restoration_adaptation": {"adaptation_run_id": str(run.id)}}
    assert is_restoration_adaptation(run) is request.param
    return coordinator, files, builds, run


async def test_eight_productive_repairs_finish_with_compiled_source(repair_loop):
    coordinator, files, builds, _ = repair_loop
    names = [f"required_value_{index}" for index in range(8)]
    files["src/app.py"] = "\n".join(f"{name} =" for name in names)
    feedback = []

    async def repair(detail):
        feedback.append(detail)
        name = names[len(feedback) - 1]
        files["src/app.py"] = files["src/app.py"].replace(f"{name} =", f"{name} = 1")

    outcome = await coordinator.finalize_with_repair(prompt="Fix syntax", repair=repair)

    assert outcome.status is MaxFinalizationStatus.COMPLETE
    assert len(feedback) == 8
    assert len(builds) == 9
    assert all("invalid syntax" in detail for detail in feedback)
    for snapshot in builds[:-1]:
        with pytest.raises(SyntaxError):
            compile(snapshot["src/app.py"], "src/app.py", "exec")
    compile(builds[-1]["src/app.py"], "src/app.py", "exec")
    assert builds[-1] == files


@pytest.mark.parametrize("cycle", [False, True], ids=["unchanged", "a-b-a"])
async def test_repeated_source_cannot_buy_another_build(repair_loop, cycle):
    coordinator, files, builds, _ = repair_loop
    initial = files["src/app.py"]
    repairs = []

    async def repair(detail):
        repairs.append(detail)
        if cycle:
            files["src/app.py"] = initial + "# distinct B\n" if len(repairs) == 1 else initial

    outcome = await coordinator.finalize_with_repair(prompt="Fix syntax", repair=repair)

    assert outcome.status is MaxFinalizationStatus.NEEDS_EDIT
    assert len(repairs) == (2 if cycle else 1)
    assert len(builds) == len(repairs)
    assert files["src/app.py"] == initial


async def test_deadline_is_rechecked_after_productive_repair(repair_loop, monkeypatch):
    coordinator, files, builds, _ = repair_loop
    now = datetime.now(UTC)
    deadline = SimpleNamespace(at=now + timedelta(hours=1))
    monkeypatch.setattr(finalization, "generation_deadline", lambda run: deadline)
    repairs = []

    async def repair(detail):
        repairs.append(detail)
        files["src/app.py"] = files["src/app.py"].replace("first =", "first = 1")
        deadline.at = now - timedelta(seconds=1)

    with pytest.raises(TimeoutError, match="before source repair"):
        await coordinator.finalize_with_repair(prompt="Fix syntax", repair=repair)

    assert len(repairs) == 1
    assert len(builds) == 2
    assert builds[0] != builds[1]


async def test_cancel_requested_between_repairs_prevents_next_editor_call(repair_loop):
    coordinator, files, builds, run = repair_loop
    repairs = []

    async def repair(detail):
        repairs.append(detail)
        files["src/app.py"] = files["src/app.py"].replace("first =", "first = 1")
        run.status = "cancel_requested"

    with pytest.raises(asyncio.CancelledError):
        await coordinator.finalize_with_repair(prompt="Fix syntax", repair=repair)

    assert len(repairs) == 1
    assert len(builds) == 2


async def test_editor_cancellation_propagates_without_retry(repair_loop):
    coordinator, _, builds, _ = repair_loop
    repair = AsyncMock(side_effect=asyncio.CancelledError)

    with pytest.raises(asyncio.CancelledError):
        await coordinator.finalize_with_repair(prompt="Fix syntax", repair=repair)

    repair.assert_awaited_once()
    assert len(builds) == 1
