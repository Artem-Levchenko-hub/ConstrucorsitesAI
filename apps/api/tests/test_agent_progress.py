"""Progress-note memory: the loop tells the model what it already wrote, so the
windowed transcript can't make it re-write files on a cycle.

Dependency-injected (`complete` + `execute`), zero network/container.
Run:  cd apps/api && uv run pytest tests/test_agent_progress.py -q
"""
from __future__ import annotations

import asyncio

from omnia_api.services import agent_builder as ab


def test_progress_note_lists_already_written_files():
    seen_system: list[str] = []
    calls = {"n": 0}

    async def _complete(messages, model, **kwargs):
        # Record the system-slot content the model actually receives each call.
        seen_system.append(messages[0]["content"])
        calls["n"] += 1
        if calls["n"] == 1:
            return (
                '<omnia:action name="write_file">'
                '{"path": "entities/Client.json", "content": "{}"}</omnia:action>'
            )
        return '<omnia:action name="done">built</omnia:action>'

    async def _execute(action: ab.Action) -> dict:
        if action.name == "write_file":
            return {"ok": True, "content": action.args.get("content", "")}
        return {"ok": True}

    res = asyncio.run(
        ab.run_agent_build(
            system_prompt="SYS",
            user_prompt="task",
            model="m",
            execute=_execute,
            complete=_complete,
            max_steps=5,
        )
    )

    assert res.done is True
    # Call 1: nothing written yet → no progress note in the system slot.
    assert "ALREADY WRITTEN" not in seen_system[0]
    # Call 2: Client.json now exists → the note must name it so the model
    # doesn't re-create it.
    assert len(seen_system) >= 2
    assert "entities/Client.json" in seen_system[1]
    assert "ALREADY WRITTEN" in seen_system[1]


def test_progress_note_reports_clean_build():
    seen_system: list[str] = []
    calls = {"n": 0}

    async def _complete(messages, model, **kwargs):
        seen_system.append(messages[0]["content"])
        calls["n"] += 1
        if calls["n"] == 1:
            return '<omnia:action name="build"></omnia:action>'
        return '<omnia:action name="done">ok</omnia:action>'

    async def _execute(action: ab.Action) -> dict:
        return {"ok": True}  # build clean

    asyncio.run(
        ab.run_agent_build(
            system_prompt="SYS",
            user_prompt="task",
            model="m",
            execute=_execute,
            complete=_complete,
            max_steps=5,
        )
    )
    # After a clean build, the next call's note states the build is CLEAN.
    assert len(seen_system) >= 2
    assert "LAST build: CLEAN" in seen_system[1]
