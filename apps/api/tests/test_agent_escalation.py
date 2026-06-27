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

from omnia_api.services import agent_builder as ab


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
