"""A green quick check cannot discharge an outstanding source repair."""

import asyncio
from copy import deepcopy
from datetime import UTC, datetime, timedelta

import pytest

from yleum_api.core.config import get_settings
from yleum_api.services import agent_native
from yleum_api.services.project_cell_executor import _normalize_path

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
        assert {tool["name"] for tool in call["tools"]} == {"write_file", "edit_file", "read_file"}


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
            assert {tool["name"] for tool in kwargs["tools"]} == {
                "write_file", "edit_file", "read_file",
            }
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


async def test_repair_can_inspect_unread_source_then_edit_without_extra_discovery(
    monkeypatch, coordinated,
):
    """The repair task names files but does not include their current contents."""
    calls, executed = [], []
    original = "export default function Page() { return <main>UNIQUE_SOURCE_47</main>; }"
    repaired = original.replace("UNIQUE_SOURCE_47", "Profile; History")

    async def provider(*args, **kwargs):
        calls.append(deepcopy(kwargs))
        if len(calls) <= 2:
            return turn(("list_dir", {"path": "src/app"}))
        if len(calls) == 3 or "UNIQUE_SOURCE_47" not in str(args[2]):
            return turn(("read_file", {"path": "src/app/page.tsx"}))
        if len(calls) == 4:
            return turn(("edit_file", {
                "path": "src/app/page.tsx", "search": "UNIQUE_SOURCE_47",
                "replace": "Profile; History",
            }))
        return turn(("done", {"summary": "repaired candidate"}))

    async def execute(action):
        executed.append(action.name)
        if action.name == "read_file":
            return {"ok": True, "content": original}
        if action.name == "edit_file":
            assert action.args["search"] in original
            return {"ok": True, "content": repaired}
        return {"ok": True}

    monkeypatch.setattr(agent_native, "_call_messages", provider)
    result = await agent_native.run_native_build(
        system="MAX VERIFICATION OVERRIDE", task="Repair missing profile/history in page.tsx",
        execute=execute, portable_cell=True, initial_files={"src/app/page.tsx": original},
        completion_check=lambda files, evidence: None, source_repair=True, max_steps=8,
    )
    assert executed == ["list_dir", "list_dir", "read_file", "edit_file", "build"]
    assert result.done and result.files == {"src/app/page.tsx": repaired}
    assert len(calls) == 5
    read_tool = next(tool for tool in calls[2]["tools"] if tool["name"] == "read_file")
    assert read_tool["input_schema"]["properties"]["path"]["enum"] == ["src/app/page.tsx"]
    assert {tool["name"] for tool in calls[3]["tools"]} == {"write_file", "edit_file"}


@pytest.mark.parametrize("read_ok", [False, True])
@pytest.mark.parametrize("fourth_action", [
    ("read_file", {"path": "./src/app/page.tsx"}),
    ("read_file", {"path": "drizzle/0002_product.sql"}),
    ("bash", {"cmd": "must not execute"}),
    ("build", {}),
    ("write_file", {"path": "src/app/page.tsx", "content": BASELINE["src/app/page.tsx"]}),
    ("edit_file", {"path": "src/app/page.tsx", "search": "absent", "replace": "Profile"}),
])
async def test_one_repair_read_never_resets_budget_or_proves_source_change(
    monkeypatch, coordinated, read_ok, fourth_action,
):
    calls, executed = [], []

    async def provider(*args, **kwargs):
        calls.append(deepcopy(kwargs))
        if len(calls) <= 2:
            return turn(("list_dir", {"path": "src/app"}))
        if len(calls) == 3:
            return turn(("read_file", {"path": "src/app/page.tsx"}))
        return turn(fourth_action)

    async def execute(action):
        executed.append(action.name)
        if action.name == "read_file":
            return {"ok": read_ok, "content": BASELINE["src/app/page.tsx"]}
        if action.name == "edit_file":
            return {"ok": False, "error": "no exact match"}
        return {"ok": True}

    monkeypatch.setattr(agent_native, "_call_messages", provider)
    result = await agent_native.run_native_build(
        system="MAX VERIFICATION OVERRIDE", task="Repair missing profile",
        execute=execute, portable_cell=True, initial_files=BASELINE,
        completion_check=lambda files, evidence: None, source_repair=True, max_steps=40,
        allow_max_bash=True,
    )
    assert not result.done and not result.needs_finalization
    assert result.stop_reason == "exploring" and len(calls) == 4
    assert executed == ["list_dir", "list_dir", "read_file"] + (
        [fourth_action[0]] if fourth_action[0] in {"write_file", "edit_file"} else []
    )
    assert {tool["name"] for tool in calls[3]["tools"]} == {"write_file", "edit_file"}


@pytest.mark.parametrize("path", [
    "src/app/unknown.tsx", "src/components/.env", "src/app/../app/page.tsx",
    "src/app//page.tsx", "/etc/passwd", "C:\\src\\app\\page.tsx", "src/app/page.tsx\x00",
    "package.json", ".omnia/cell.json",
])
async def test_repair_read_cannot_escape_existing_source_allowlist(monkeypatch, coordinated, path):
    calls, executed = [], []
    baseline = {**BASELINE, "package.json": "opaque config", ".omnia/cell.json": "opaque config",
                "src/components/.env": "not source"}

    async def provider(*args, **kwargs):
        calls.append(deepcopy(kwargs))
        return turn(("list_dir", {})) if len(calls) <= 2 else turn(("read_file", {"path": path}))

    async def execute(action):
        executed.append(action.name)
        return {"ok": True}

    monkeypatch.setattr(agent_native, "_call_messages", provider)
    result = await agent_native.run_native_build(
        system="MAX VERIFICATION OVERRIDE", task="Repair missing profile",
        execute=execute, portable_cell=True, initial_files=baseline,
        source_repair=True, max_steps=40,
    )
    assert executed == ["list_dir", "list_dir"]
    assert len(calls) == 4 and not result.done and not result.needs_finalization
    read_tool = next(tool for tool in calls[2]["tools"] if tool["name"] == "read_file")
    assert read_tool["input_schema"]["properties"]["path"]["enum"] == sorted(BASELINE)


@pytest.mark.parametrize("alias", ["./src/app/page.tsx", "src\\app\\page.tsx",
                                       "/workspace/src/app/page.tsx"])
async def test_discovery_read_cannot_be_repeated_using_alias_after_lock(
    monkeypatch, coordinated, alias,
):
    calls, executed = [], []

    async def provider(*args, **kwargs):
        calls.append(deepcopy(kwargs))
        if len(calls) == 1:
            return turn(("read_file", {"path": "src/app/page.tsx"}))
        if len(calls) == 2:
            return turn(("list_dir", {}))
        return turn(("read_file", {"path": alias}))

    async def execute(action):
        executed.append(action.name)
        return {"ok": True, "content": "existing product"}

    monkeypatch.setattr(agent_native, "_call_messages", provider)
    result = await agent_native.run_native_build(
        system="MAX VERIFICATION OVERRIDE", task="Repair missing profile", execute=execute,
        portable_cell=True, initial_files=BASELINE, source_repair=True, max_steps=40,
    )
    assert executed == ["read_file", "list_dir"]
    assert len(calls) == 4 and not result.done and not result.needs_finalization


@pytest.mark.parametrize("discovery_path", [
    "src/app/./page.tsx", "src/app//page.tsx", "src/app/../app/page.tsx",
])
async def test_executor_normalized_discovery_read_is_not_read_again(
    monkeypatch, coordinated, discovery_path,
):
    calls, executed = [], []

    async def provider(*args, **kwargs):
        calls.append(deepcopy(kwargs))
        if len(calls) == 1:
            return turn(("read_file", {"path": discovery_path}))
        if len(calls) == 2:
            return turn(("list_dir", {}))
        return turn(("read_file", {"path": "src/app/page.tsx"}))

    async def execute(action):
        executed.append(action.name)
        if action.name == "read_file":
            assert _normalize_path(action.path) == "src/app/page.tsx"
        return {"ok": True, "content": BASELINE["src/app/page.tsx"]}

    monkeypatch.setattr(agent_native, "_call_messages", provider)
    result = await agent_native.run_native_build(
        system="MAX VERIFICATION OVERRIDE", task="Repair missing profile", execute=execute,
        portable_cell=True, initial_files=BASELINE, source_repair=True, max_steps=40,
    )
    assert executed == ["read_file", "list_dir"]
    assert len(calls) == 4 and not result.done and not result.needs_finalization
    read_tool = next(tool for tool in calls[2]["tools"] if tool["name"] == "read_file")
    assert read_tool["input_schema"]["properties"]["path"]["enum"] == ["drizzle/0002_product.sql"]


async def test_repair_read_budget_is_shared_across_tool_batch(monkeypatch, coordinated):
    calls, executed = [], []

    async def provider(*args, **kwargs):
        calls.append(deepcopy(kwargs))
        if len(calls) <= 2:
            return turn(("list_dir", {}))
        return turn(("read_file", {"path": "src/app/page.tsx"}),
                    ("read_file", {"path": "drizzle/0002_product.sql"}),
                    ("bash", {"cmd": "must not execute"}), ("done", {"summary": "not ready"}))

    async def execute(action):
        executed.append(action.name)
        return {"ok": True, "content": "existing source"}

    monkeypatch.setattr(agent_native, "_call_messages", provider)
    result = await agent_native.run_native_build(
        system="MAX VERIFICATION OVERRIDE", task="Repair missing profile", execute=execute,
        portable_cell=True, initial_files=BASELINE, source_repair=True, max_steps=40,
        allow_max_bash=True,
    )
    assert executed == ["list_dir", "list_dir", "read_file"]
    assert len(calls) == 4 and not result.done and not result.needs_finalization


async def test_locked_targeted_read_keeps_cancellation(monkeypatch, coordinated):
    calls = 0

    async def provider(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls <= 2:
            return turn(("list_dir", {}))
        return turn(("read_file", {"path": "src/app/page.tsx"}))

    async def execute(action):
        if action.name == "read_file":
            raise asyncio.CancelledError
        return {"ok": True}

    monkeypatch.setattr(agent_native, "_call_messages", provider)
    with pytest.raises(asyncio.CancelledError):
        await agent_native.run_native_build(
            system="MAX VERIFICATION OVERRIDE", task="Repair missing profile", execute=execute,
            portable_cell=True, initial_files=BASELINE, source_repair=True,
        )
    assert calls == 3
