"""No partial response may execute tools; recovery must stay bounded."""
from copy import deepcopy
from unittest.mock import AsyncMock

import pytest

from yleum_api.services import agent_native


def turn(*calls, stop="tool_use"):
    return {"stop_reason": stop, "content": [
        {"type": "tool_use", "id": f"call-{i}", "name": name, "input": args}
        for i, (name, args) in enumerate(calls)
    ]}


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", [
    turn(("bash", {"cmd": "NEVER_EXECUTE"}), ("write_file", {"path": "a"}), stop="max_tokens"),
    turn(("bash", {"cmd": "NEVER_EXECUTE"}), ("write_file", {"path": "a"})),
    {"stop_reason": "tool_use", "content": [{"type": "tool_use", "id": "bad",
        "name": "write_file", "input": {}, "input_error": "invalid_tool_arguments"}]},
    turn(("write_file", [])),
    turn(("write_file", {"path": "a", "content": None})),
    {"stop_reason": "max_tokens", "content": [{"type": "text", "text": "unfinished"}]},
])
async def test_bad_response_does_not_execute_any_call_and_recovers_with_small_edit(
    monkeypatch, tmp_path, bad,
):
    turns = iter([bad, turn(("edit_file", {"path": "a", "search": "original", "replace": "small"})),
                  turn(("build", {})), turn(("done", {"summary": "ready"}))])
    conversations = []
    source = tmp_path / "a"
    source.write_text("original")
    executed = []

    async def provider(*args, **kwargs):
        conversations.append(deepcopy(args[2]))
        return next(turns)

    monkeypatch.setattr(agent_native, "_call_messages", provider)
    async def execute(action):
        executed.append(action.name)
        if action.name == "edit_file":
            assert source.read_text() == "original"
            source.write_text(
                source.read_text().replace(action.args["search"], action.args["replace"]),
            )
            return {"ok": True, "content": source.read_text()}
        assert action.name == "build"
        assert source.read_text() == "small"
        return {"ok": True}

    result = await agent_native._run_native_segment(
        system="build", task="edit", execute=execute, max_steps=6, initial_files={"a": "original"},
    )
    assert result.done
    assert executed == ["edit_file", "build"]
    assert source.read_text() == "small"
    assert result.files["a"] == "small"
    feedback = str(conversations[1][-1])
    assert "No tools from this response were executed" in feedback
    assert "edit_file" in feedback and "small" in feedback


@pytest.mark.asyncio
async def test_repeated_truncation_stops_without_replaying_prior_side_effect(monkeypatch):
    calls = 0

    async def provider(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            return turn(("bash", {"cmd": "SIDE_EFFECT_ONCE"}))
        return turn(("bash", {"cmd": "NEVER_EXECUTE"}), stop="max_tokens")

    monkeypatch.setattr(agent_native, "_call_messages", provider)
    execute = AsyncMock(return_value={"ok": True})
    result = await agent_native._run_native_segment(
        system="build", task="edit", execute=execute, max_steps=12,
    )
    assert not result.done
    assert result.stop_reason == "error"
    assert calls == 4  # initial success, bad response, two bounded recovery attempts
    assert execute.await_count == 1
    assert execute.await_args.args[0].args["cmd"] == "SIDE_EFFECT_ONCE"


@pytest.mark.asyncio
async def test_truncated_done_cannot_publish_using_earlier_green_build(monkeypatch):
    turns = iter([turn(("build", {})), turn(("done", {"summary": "partial"}), stop="max_tokens"),
                  turn(("done", {"summary": "old proof"}))])
    monkeypatch.setattr(
        agent_native, "_call_messages", AsyncMock(side_effect=lambda *a, **k: next(turns)),
    )
    execute = AsyncMock(return_value={"ok": True})
    result = await agent_native._run_native_segment(
        system="build", task="edit", execute=execute, max_steps=3,
    )
    assert not result.done
    assert not result.evidence.get("build_after_write")


@pytest.mark.asyncio
async def test_incomplete_response_respects_existing_step_budget(monkeypatch):
    provider = AsyncMock(return_value=turn(("write_file", {}), stop="max_tokens"))
    monkeypatch.setattr(agent_native, "_call_messages", provider)
    execute = AsyncMock(return_value={"ok": True})
    result = await agent_native._run_native_segment(
        system="build", task="edit", execute=execute, max_steps=1,
    )
    assert not result.done
    assert provider.await_count == 1
    execute.assert_not_awaited()
