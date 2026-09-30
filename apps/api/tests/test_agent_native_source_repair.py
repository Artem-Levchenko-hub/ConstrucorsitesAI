"""A green quick check cannot discharge an outstanding source repair."""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from yleum_api.core.config import get_settings
from yleum_api.services import agent_native

from .test_agent_native_truncation import turn


@pytest.fixture
def coordinated(monkeypatch):
    monkeypatch.setenv("USE_MAX_FINALIZATION_COORDINATOR", "true")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


BASELINE = {
    "src/app/page.tsx": "existing product",
    "drizzle/0002_product.sql": "CREATE TABLE product(id int REFERENCES missing(id));",
}


async def test_green_fast_check_does_not_complete_unchanged_repair(monkeypatch, coordinated):
    calls = 0

    async def provider(*args, **kwargs):
        nonlocal calls
        calls += 1
        return turn(("build", {})) if calls == 1 else turn(("done", {"summary": "ready"}))

    async def execute(action):
        assert action.name == "build"
        return {"ok": True}

    monkeypatch.setattr(agent_native, "_call_messages", provider)
    result = await agent_native.run_native_build(
        system="MAX VERIFICATION OVERRIDE", task="Repair pending SQL after 42P01",
        execute=execute, portable_cell=True, initial_files=BASELINE,
        completion_check=lambda files, evidence: None, max_steps=5,
        source_repair=True,
    )
    assert not result.done
    assert not result.needs_finalization
    assert calls <= 4


@pytest.mark.parametrize("first_turn_build", [False, True])
async def test_prose_repair_turns_force_writes_then_stop(
    monkeypatch, coordinated, first_turn_build,
):
    calls = []

    async def provider(*args, **kwargs):
        calls.append(kwargs)
        if first_turn_build and len(calls) == 1:
            return turn(("build", {}))
        return {"stop_reason": "end_turn", "content": [{"type": "text", "text": "Ready"}]}

    async def execute(action):
        assert first_turn_build and action.name == "build"
        return {"ok": True}

    monkeypatch.setattr(agent_native, "_call_messages", provider)
    result = await agent_native.run_native_build(
        system="MAX VERIFICATION OVERRIDE", task="Repair pending SQL",
        execute=execute, portable_cell=True, initial_files=BASELINE,
        completion_check=lambda files, evidence: None, source_repair=True, max_steps=10,
    )
    assert not result.done and not result.needs_finalization
    assert len(calls) == 4
    assert [call["tool_choice"] for call in calls] == [None, None, {"type": "any"}, {"type": "any"}]
    for call in calls[2:]:
        assert {tool["name"] for tool in call["tools"]} == {"write_file", "edit_file"}


@pytest.mark.parametrize("path", ["drizzle/0002_product.sql", ".omnia/cell.json"])
@pytest.mark.parametrize("discovery", ["bash", "prose"])
async def test_repair_limits_exploration_then_accepts_changed_candidate(
    monkeypatch, coordinated, path, discovery,
):
    calls = []
    actions = []

    async def provider(*args, **kwargs):
        calls.append(kwargs)
        if len(calls) <= 2:
            if discovery == "prose":
                return {"stop_reason": "end_turn", "content": [{"type": "text", "text": "Ready"}]}
            return turn(("bash", {"cmd": "inspect only"}))
        if len(calls) == 3:
            assert {tool["name"] for tool in kwargs["tools"]} == {"write_file", "edit_file"}
            assert kwargs["tool_choice"] == {"type": "any"}
            assert "SOURCE REPAIR REQUIRED" in str(args[2])
            return turn(("write_file", {"path": path, "content": "repaired source"}))
        assert "bash" in {tool["name"] for tool in kwargs["tools"]}
        return turn(("done", {"summary": "candidate repaired"}))

    async def execute(action):
        actions.append(action.name)
        return {"ok": True}

    monkeypatch.setattr(agent_native, "_call_messages", provider)
    result = await agent_native.run_native_build(
        system="MAX VERIFICATION OVERRIDE", task="Repair the reported source error",
        execute=execute, portable_cell=True, initial_files=BASELINE,
        completion_check=lambda files, evidence: None, max_steps=8,
        source_repair=True, allow_max_bash=True,
    )
    assert result.done
    assert result.files[path] == "repaired source"
    assert actions == (["bash", "bash"] if discovery == "bash" else []) + ["write_file", "build"]
    assert len(calls) == 4


@pytest.mark.parametrize("action", [
    ("write_file", {"path": "drizzle/0002_product.sql",
                    "content": BASELINE["drizzle/0002_product.sql"]}),
    ("write_file", {"path": "notes.md", "content": "I checked the error"}),
    ("bash", {"cmd": "keep exploring"}),
])
async def test_noop_or_notes_cannot_unlock_repair(monkeypatch, coordinated, action):
    calls = 0
    executions = []

    async def provider(*args, **kwargs):
        nonlocal calls
        calls += 1
        return turn(action)

    async def execute(action):
        executions.append(action.name)
        return {"ok": True}

    monkeypatch.setattr(agent_native, "_call_messages", provider)
    result = await agent_native.run_native_build(
        system="MAX VERIFICATION OVERRIDE", task="Repair pending SQL",
        execute=execute, portable_cell=True, initial_files=BASELINE,
        completion_check=lambda files, evidence: None, max_steps=40,
        source_repair=True, allow_max_bash=True,
    )
    assert not result.done and not result.needs_finalization
    assert calls == 4
    if action[0] == "bash":
        assert executions == ["bash", "bash"]


async def test_expired_repair_budget_cannot_handoff_unchanged_source(monkeypatch, coordinated):
    async def forbidden(*args, **kwargs):
        pytest.fail("expired repair must not call provider or executor")

    monkeypatch.setattr(agent_native, "_call_messages", forbidden)
    result = await agent_native.run_native_build(
        system="MAX VERIFICATION OVERRIDE", task="Repair pending SQL",
        execute=forbidden, portable_cell=True, initial_files=BASELINE,
        completion_check=lambda files, evidence: None, source_repair=True,
        edit_deadline=datetime.now(UTC) - timedelta(seconds=1),
    )
    assert not result.done and not result.needs_finalization


async def test_source_repair_keeps_cancellation(monkeypatch, coordinated):
    async def provider(*args, **kwargs):
        return turn(("edit_file", {"path": "drizzle/0002_product.sql",
                                   "search": "missing", "replace": "own_parent"}))

    async def execute(action):
        raise asyncio.CancelledError

    monkeypatch.setattr(agent_native, "_call_messages", provider)
    with pytest.raises(asyncio.CancelledError):
        await agent_native.run_native_build(
            system="MAX VERIFICATION OVERRIDE", task="Repair pending SQL",
            execute=execute, portable_cell=True, initial_files=BASELINE, source_repair=True,
        )
