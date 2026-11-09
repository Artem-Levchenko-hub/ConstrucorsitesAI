"""Escalation-on-stall: the loop upgrades the model once a guard nudges.

Dependency-injected (`complete` + `execute`), so these run with zero network /
container. The fake `complete` records which model each step was issued on and
returns a read action with a FRESH path each call — so no consecutive/cycle
repeat fires, the no-write streak climbs, and the explore nudge (and thus the
escalation) is what we exercise.

Run:  cd apps/api && uv run pytest tests/test_agent_escalation.py -q
"""
from __future__ import annotations

import asyncio
from copy import deepcopy
from types import SimpleNamespace

import pytest

from yleum_api.services import agent_builder as ab


async def _ok_executor(action: ab.Action) -> dict:
    return {"ok": True, "content": "x"}


def _recording_complete(models: list[str]):
    """A fake gateway call: records the model, emits a non-write read with a
    unique path each step (varied path → only the no-write streak grows)."""
    counter = {"n": 0}

    async def _complete(messages, model, **kwargs):
        models.append(model)
        counter["n"] += 1
        return (
            f'<omnia:action name="read_file">'
            f'{{"path": "src/file{counter["n"]}.tsx"}}</omnia:action>'
        )

    return _complete


def test_escalates_to_strong_model_after_stall_nudge():
    models: list[str] = []
    asyncio.run(
        ab.run_agent_build(
            system_prompt="s",
            user_prompt="u",
            model="cheap",
            escalate_model="strong",
            execute=_ok_executor,
            complete=_recording_complete(models),
            max_steps=8,
        )
    )
    # The first few steps run on the cheap model; once the no-write streak trips
    # the nudge (_NO_WRITE_NUDGE_AT=5) the loop switches to the strong model.
    assert models[0] == "cheap"
    assert "strong" in models
    assert models[-1] == "strong"


def test_escalates_at_most_once():
    models: list[str] = []
    asyncio.run(
        ab.run_agent_build(
            system_prompt="s",
            user_prompt="u",
            model="cheap",
            escalate_model="strong",
            execute=_ok_executor,
            complete=_recording_complete(models),
            max_steps=10,
        )
    )
    first_strong = models.index("strong")
    # Once switched, it never flips back to cheap.
    assert all(m == "strong" for m in models[first_strong:])


def test_no_escalation_when_escalate_model_none():
    models: list[str] = []
    asyncio.run(
        ab.run_agent_build(
            system_prompt="s",
            user_prompt="u",
            model="cheap",
            escalate_model=None,
            execute=_ok_executor,
            complete=_recording_complete(models),
            max_steps=8,
        )
    )
    # No escalate model → byte-identical model usage throughout.
    assert all(m == "cheap" for m in models)


def _alternating_edit_build_complete(models: list[str]):
    """Alternate a unique edit_file with a build, so neither the consecutive-
    repeat guard nor the no-write streak fires — isolating the red-build trigger.
    """
    counter = {"n": 0}

    async def _complete(messages, model, **kwargs):
        models.append(model)
        counter["n"] += 1
        if counter["n"] % 2 == 1:
            return (
                f'<omnia:action name="edit_file">'
                f'{{"path": "src/f{counter["n"]}.tsx", "search": "a", "replace": "b"}}'
                f'</omnia:action>'
            )
        return '<omnia:action name="build">{}</omnia:action>'

    return _complete


async def _red_build_executor(action: ab.Action) -> dict:
    # Builds are RED (the cheap model can't clear the typecheck); edits succeed.
    if action.name == "build":
        return {"ok": False, "detail": "src/x.tsx: error TS2307: Cannot find module 'z'"}
    return {"ok": True, "content": "x"}


def test_escalates_after_consecutive_red_builds():
    # Two failed builds (non-consecutive, edits between) → escalate to the strong
    # model so the loop grinds to green instead of giving up on the cheap one.
    models: list[str] = []
    asyncio.run(
        ab.run_agent_build(
            system_prompt="s",
            user_prompt="u",
            model="cheap",
            escalate_model="strong",
            execute=_red_build_executor,
            complete=_alternating_edit_build_complete(models),
            max_steps=8,
        )
    )
    assert models[0] == "cheap"
    assert "strong" in models  # the 2nd red build escalated
    assert models[-1] == "strong"


def _stall_then_act_complete(models: list[str], misses: int):
    """No valid <omnia:action> for the first `misses` turns (→ parse None → stall),
    then a clean `done`. Exercises the no-action recovery path."""
    counter = {"n": 0}

    async def _complete(messages, model, **kwargs):
        models.append(model)
        counter["n"] += 1
        if counter["n"] <= misses:
            return "I am thinking about the problem, no action yet."
        return '<omnia:action name="done">{"summary": "ok"}</omnia:action>'

    return _complete


def test_stall_escalates_before_abort():
    # The cheap model emits zero action twice (the "stalled, 0 files" first-build
    # failure), then recovers. Must NOT abort at the 2nd miss — it escalates to the
    # strong model and finishes.
    models: list[str] = []
    res = asyncio.run(
        ab.run_agent_build(
            system_prompt="s",
            user_prompt="u",
            model="cheap",
            escalate_model="strong",
            execute=_ok_executor,
            complete=_stall_then_act_complete(models, misses=2),
            max_steps=8,
        )
    )
    assert res.done is True            # recovered instead of aborting at miss 2
    assert "strong" in models          # escalated on the 2nd no-action
    assert models[-1] == "strong"


def test_stall_aborts_after_cap():
    # A model that NEVER emits a valid action still aborts (bounded), as stalled.
    models: list[str] = []
    res = asyncio.run(
        ab.run_agent_build(
            system_prompt="s",
            user_prompt="u",
            model="cheap",
            escalate_model="strong",
            execute=_ok_executor,
            complete=_stall_then_act_complete(models, misses=99),
            max_steps=8,
        )
    )
    assert res.done is False
    assert res.stop_reason == "stalled"


def _trace_stalled_loop(escalate_model, *, misses):
    requests, events, models = [], [], []
    reply = _stall_then_act_complete(models, misses)

    async def complete(messages, model, **kwargs):
        requests.append((deepcopy(messages), model, deepcopy(kwargs)))
        return await reply(messages, model, **kwargs)

    async def emit(event, data):
        events.append((event, deepcopy(data)))

    result = asyncio.run(ab.run_agent_build(
        system_prompt="s", user_prompt="u", model="claude-sonnet-5.5",
        escalate_model=escalate_model, execute=_ok_executor, complete=complete,
        max_steps=8, emit=emit,
    ))
    return result, requests, events


@pytest.mark.parametrize("misses", [2, 99])
def test_same_model_consumes_fallback_without_false_upgrade_or_request_changes(misses, capsys):
    expected, expected_requests, expected_events = _trace_stalled_loop(None, misses=misses)
    result, requests, events = _trace_stalled_loop("claude-sonnet-5.5", misses=misses)

    assert requests == expected_requests
    assert events == expected_events
    assert (result.done, result.stop_reason, result.steps) == (
        expected.done, expected.stop_reason, expected.steps,
    )
    assert len(requests) == (3 if misses == 2 else ab._NO_ACTION_ABORT_AT)
    assert all(model == "claude-sonnet-5.5" and kwargs["max_tokens"] is None
               for _, model, kwargs in requests)
    assert not any(event == "agent.escalate" for event, _ in events)
    assert "ESCALATE" not in capsys.readouterr().out


@pytest.mark.parametrize("misses", [2, 99])
def test_different_model_switches_and_reports_exactly_once(misses, capsys):
    result, requests, events = _trace_stalled_loop("strong", misses=misses)
    changes = [data for event, data in events if event == "agent.escalate"]

    assert len(changes) == 1
    assert changes[0]["to"] == "strong" and changes[0]["reason"] == "no_action"
    assert [model for _, model, _ in requests] == (
        ["claude-sonnet-5.5"] * 2 + ["strong"] * (1 if misses == 2 else 2)
    )
    assert [kwargs["max_tokens"] for _, _, kwargs in requests] == (
        [None, None] + [16384] * (1 if misses == 2 else 2)
    )
    assert result.done is (misses == 2)
    assert capsys.readouterr().out.count("ESCALATE") == 1


@pytest.mark.parametrize("fallback", [None, ""])
def test_absent_fallback_keeps_identical_requests_events_and_abort(fallback, capsys):
    expected, expected_requests, expected_events = _trace_stalled_loop(None, misses=99)
    result, requests, events = _trace_stalled_loop(fallback, misses=99)

    assert requests == expected_requests and events == expected_events
    assert result.stop_reason == expected.stop_reason == "stalled"
    assert len(requests) == ab._NO_ACTION_ABORT_AT
    assert "ESCALATE" not in capsys.readouterr().out


@pytest.mark.parametrize("fallback", ["claude-sonnet-5.5", "strong"])
def test_consumed_fallback_flag_survives_saved_frame_and_cold_continuation(fallback):
    frames, events = [], []

    async def provider(frame, invoke):
        frames.append(deepcopy(frame))
        return await invoke()

    async def emit(event, data):
        events.append((event, deepcopy(data)))

    async def segment(driver, complete):
        return await ab._run_agent_segment(
            system_prompt="s", user_prompt="u", model="claude-sonnet-5.5",
            escalate_model=fallback, execute=_ok_executor, complete=complete,
            continuation=driver, max_steps=8, emit=emit,
        )

    driver = SimpleNamespace(restore_frame=lambda kind: None, provider=provider)
    result = asyncio.run(segment(driver, _stall_then_act_complete([], misses=2)))
    assert result.done
    assert [frame["value"]["escalated"] for frame in frames] == [False, False, True]
    assert frames[-1]["value"]["active_model"] == fallback
    assert sum(event == "agent.escalate" for event, _ in events) == (
        0 if fallback == "claude-sonnet-5.5" else 1
    )

    saved = deepcopy(frames[-1]["value"])
    frames.clear()
    events.clear()
    models = []
    restored = SimpleNamespace(restore_frame=lambda kind: deepcopy(saved), provider=provider)
    result = asyncio.run(segment(restored, _stall_then_act_complete(models, misses=99)))
    assert not result.done and result.stop_reason == "stalled"
    assert models == [fallback, fallback]
    assert all(frame["value"]["escalated"] is True for frame in frames)
    assert not any(event == "agent.escalate" for event, _ in events)
