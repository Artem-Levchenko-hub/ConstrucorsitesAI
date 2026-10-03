"""Escalate an ignored edit tool choice inside the existing six-turn guard."""

from copy import deepcopy
from types import SimpleNamespace

import pytest

from yleum_api.core.config import get_settings
from yleum_api.services import agent_native

from .test_agent_native_truncation import turn


@pytest.fixture
def edit_loop(monkeypatch):
    settings = get_settings().model_copy(update={"use_max_finalization_coordinator": True})
    monkeypatch.setattr(agent_native, "get_settings", lambda: settings)
    baseline = {"src/app/page.tsx": "Catalog; History"}
    workspace = dict(baseline)
    calls, executed, events = [], [], []

    async def execute(action):
        executed.append(action)
        if action.name == "edit_file":
            workspace[action.path] = workspace[action.path].replace(
                action.args["search"],
                action.args["replace"],
            )
        # Match C's fourth discovery command being red, without changing source.
        return {"ok": len(executed) != 4, "detail": "synthetic discovery or source check"}

    async def emit(event, payload):
        events.append((event, payload))

    async def run(provider, *, existing_edit=True, source_repair=False, max_steps=10):
        async def capture(*args, **kwargs):
            calls.append(deepcopy(kwargs))
            return await provider(len(calls))

        monkeypatch.setattr(agent_native, "_call_messages", capture)
        result = await agent_native._run_native_segment(
            system="MAX VERIFICATION OVERRIDE",
            task="Remove History",
            execute=execute,
            emit=emit,
            portable_cell=True,
            allow_max_bash=True,
            initial_files=baseline,
            completion_check=lambda *_: None,
            max_steps=max_steps,
            source_repair=source_repair,
            edit_source_changed=(lambda: workspace != baseline) if existing_edit else None,
        )
        return result

    return SimpleNamespace(
        run=run,
        calls=calls,
        executed=executed,
        events=events,
        workspace=workspace,
        baseline=baseline,
    )


async def test_ignored_write_only_choice_escalates_last_edit_attempt_then_restores_tools(edit_loop):
    async def provider(call):
        if call == 2:
            return turn(("read_file", {"path": "src/app/page.tsx"}))
        if call <= 5:
            return turn(("bash", {"cmd": "synthetic inspect; never repeat at turn five"}))
        if call == 6:
            return turn(
                (
                    "edit_file",
                    {
                        "path": "src/app/page.tsx",
                        "search": "History",
                        "replace": "Due dates",
                    },
                )
            )
        if call == 7:
            return turn(("build", {}))
        return turn(("done", {"summary": "done"}))

    result = await edit_loop.run(provider)
    assert result.done
    assert edit_loop.calls[4]["tool_choice"] == {"type": "any"}
    assert edit_loop.calls[5]["tool_choice"] == {"type": "tool", "name": "edit_file"}
    assert {t["name"] for t in edit_loop.calls[5]["tools"]} == {"write_file", "edit_file"}
    assert edit_loop.calls[6]["tool_choice"] is None
    assert "bash" in {t["name"] for t in edit_loop.calls[6]["tools"]}
    assert [a.name for a in edit_loop.executed] == [
        "bash",
        "read_file",
        "bash",
        "bash",
        "edit_file",
        "build",
        "build",
    ]
    assert edit_loop.workspace["src/app/page.tsx"] == "Catalog; Due dates"
    mismatches = [p for e, p in edit_loop.events if e == "agent.tool_contract_mismatch"]
    assert mismatches == [{"tool": "bash", "step": 4, "required_mode": "existing_edit_write_only"}]


async def test_repeated_forbidden_response_remains_six_call_no_change(edit_loop):
    async def provider(_):
        return turn(("bash", {"cmd": "synthetic secret args must not be recorded"}))

    result = await edit_loop.run(provider)
    assert not result.done and not result.needs_finalization
    assert result.stop_reason == "no_progress"
    assert len(edit_loop.calls) == 6
    assert edit_loop.calls[5]["tool_choice"] == {"type": "tool", "name": "edit_file"}
    assert len(edit_loop.executed) == 4
    assert edit_loop.workspace == edit_loop.baseline
    assert "synthetic secret" not in str(edit_loop.events)


async def test_named_edit_noop_cannot_satisfy_source_change_guard(edit_loop):
    async def provider(call):
        if call <= 5:
            return turn(("bash", {"cmd": "synthetic inspect"}))
        return turn(
            (
                "edit_file",
                {
                    "path": "src/app/page.tsx",
                    "search": "History",
                    "replace": "History",
                },
            )
        )

    result = await edit_loop.run(provider)
    assert not result.done and not result.needs_finalization
    assert result.stop_reason == "no_progress"
    assert len(edit_loop.calls) == 6
    assert edit_loop.calls[5]["tool_choice"] == {"type": "tool", "name": "edit_file"}
    assert edit_loop.workspace == edit_loop.baseline
    assert "build" not in [action.name for action in edit_loop.executed]


@pytest.mark.parametrize("source_repair", [False, True])
async def test_non_edit_or_source_repair_does_not_escalate_to_named_edit(edit_loop, source_repair):
    async def provider(_):
        return turn(("bash", {"cmd": "synthetic inspect"}))

    await edit_loop.run(provider, existing_edit=False, source_repair=source_repair, max_steps=6)
    assert all(c["tool_choice"] != {"type": "tool", "name": "edit_file"} for c in edit_loop.calls)
    assert not any(e == "agent.tool_contract_mismatch" for e, _ in edit_loop.events)
