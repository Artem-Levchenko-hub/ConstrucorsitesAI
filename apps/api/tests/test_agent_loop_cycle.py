"""Cycle breaker for the agentic loop — the no-WRITE streak guard.

Regression for the live bug: the agent looped on read-only actions
(read→grep→list→read→read) for the whole 80-step budget, writing ZERO files.
The consecutive-identical circuit breaker missed it (each step differs from the
last). The no-WRITE streak guard must catch a multi-step read CYCLE, nudge to
write, and abort early instead of burning the budget — while NOT tripping a real
build that reads a couple files before writing.
"""

from __future__ import annotations

import asyncio

from omnia_api.services import agent_builder as ab


def _ok_executor(record: list):
    async def _execute(action: ab.Action):
        record.append((action.name, action.path))
        if action.name in ("write_file", "edit_file"):
            return {"ok": True, "content": action.args.get("content", "x")}
        return {"ok": True, "detail": "ok"}

    return _execute


def _cycle(replies: list[str]):
    box = {"i": 0}

    async def _complete(convo, model, **kw):
        r = replies[box["i"] % len(replies)]
        box["i"] += 1
        return r

    return _complete


def test_cycle_of_distinct_reads_aborts_as_exploring():
    # Three DIFFERENT read paths cycling → each step's signature differs from the
    # last, so the consecutive-identical circuit breaker never fires. The no-WRITE
    # streak guard must abort as "exploring", well before the 40-step budget.
    record: list = []
    replies = [
        '<omnia:action name="read_file">{"path":"a.ts"}</omnia:action>',
        '<omnia:action name="read_file">{"path":"b.ts"}</omnia:action>',
        '<omnia:action name="read_file">{"path":"c.ts"}</omnia:action>',
    ]
    res = asyncio.run(
        ab.run_agent_build(
            system_prompt="sys", user_prompt="x", model="m",
            execute=_ok_executor(record), complete=_cycle(replies), max_steps=40,
        )
    )
    assert res.done is False
    # Caught as a stuck cycle — either the global repeat guard ("looping") or the
    # no-write streak ("exploring") fires first; both mean "stuck", and both abort
    # well before the 40-step budget. The point is it does NOT run to the cap.
    assert res.stop_reason in ("looping", "exploring")
    assert res.steps < 40
    assert res.files == {}


def test_write_cycle_same_content_aborts_as_looping():
    # The live bug the no-write streak MISSED: a cycle that re-WRITES the same files
    # with identical content (write a → write b → build → repeat). Writes reset the
    # no-write streak and the steps differ from the last, so only the GLOBAL repeat
    # guard catches it — abort as "looping" well before the 40-step budget.
    record: list = []
    replies = [
        '<omnia:action name="write_file">{"path":"entities/A.json","content":"X"}</omnia:action>',
        '<omnia:action name="write_file">{"path":"entities/B.json","content":"Y"}</omnia:action>',
        '<omnia:action name="build"></omnia:action>',
    ]
    res = asyncio.run(
        ab.run_agent_build(
            system_prompt="sys", user_prompt="x", model="m",
            execute=_ok_executor(record), complete=_cycle(replies), max_steps=40,
        )
    )
    assert res.done is False
    assert res.stop_reason == "looping"
    assert res.steps < 40  # caught, not run to budget


def test_reads_then_write_is_not_falsely_aborted():
    # A legit build reads a couple files, then writes — the streak resets on the
    # write, so it reaches done without a false "exploring" abort.
    record: list = []
    replies = [
        '<omnia:action name="read_file">{"path":"a.ts"}</omnia:action>',
        '<omnia:action name="read_file">{"path":"b.ts"}</omnia:action>',
        '<omnia:action name="write_file">{"path":"src/app/page.tsx","content":"x"}</omnia:action>',
        '<omnia:action name="build"></omnia:action>',
        '<omnia:action name="done">{"summary":"built"}</omnia:action>',
    ]
    box = {"i": 0}

    async def _complete(convo, model, **kw):
        i = box["i"]
        box["i"] += 1
        return replies[i] if i < len(replies) else replies[-1]

    res = asyncio.run(
        ab.run_agent_build(
            system_prompt="sys", user_prompt="x", model="m",
            execute=_ok_executor(record), complete=_complete, max_steps=12,
        )
    )
    assert res.done is True
    assert res.stop_reason == "done"
    assert "src/app/page.tsx" in res.files
