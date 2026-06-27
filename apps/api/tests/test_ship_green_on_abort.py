"""Ship-green-on-abort: a loop-guard abort (cycle / repeat / explore / budget)
whose LAST build was green must SHIP the app as a success, not discard a compiling
app as «Сборка прервана».

This is the exact live failure: the messenger built green (`build ok=True`), then
the model fussed re-reading layout.tsx, the cycle-guard tripped, and the green app
was thrown away as `done=False stop=looping`. Dependency-injected (`complete` +
`execute`) — no network, no container.

Run:  cd apps/api && uv run pytest tests/test_ship_green_on_abort.py -q
"""
from __future__ import annotations

import asyncio

from omnia_api.services import agent_builder as ab


def _green_build_then_cycle(build_ok: bool):
    """Step 0 = a build (ok=`build_ok`); every step after = re-reading the SAME
    file, so the consecutive-repeat guard aborts the loop while the last build's
    result stands."""

    async def _executor(action: ab.Action) -> dict:
        if action.name == "build":
            return {"ok": build_ok, "detail": "" if build_ok else "error TS2307: x"}
        return {"ok": True, "content": "x"}

    counter = {"n": 0}

    async def _complete(messages, model, **kwargs):
        counter["n"] += 1
        if counter["n"] == 1:
            return '<omnia:action name="build">{}</omnia:action>'
        # same path every step → repeat_count climbs → looping abort
        return '<omnia:action name="read_file">{"path": "src/app/layout.tsx"}</omnia:action>'

    return _executor, _complete


def test_green_abort_ships_as_success():
    executor, complete = _green_build_then_cycle(build_ok=True)
    res = asyncio.run(
        ab.run_agent_build(
            system_prompt="s",
            user_prompt="u",
            model="cheap",
            execute=executor,
            complete=complete,
            max_steps=14,
        )
    )
    assert res.done is True
    assert res.stop_reason == "done_on_green"


def test_red_abort_does_not_ship():
    # Last build RED → nothing to rescue → the abort stays a failure.
    executor, complete = _green_build_then_cycle(build_ok=False)
    res = asyncio.run(
        ab.run_agent_build(
            system_prompt="s",
            user_prompt="u",
            model="cheap",
            execute=executor,
            complete=complete,
            max_steps=14,
        )
    )
    assert res.done is False
    assert res.stop_reason in ("looping", "exploring")


def test_flag_off_keeps_legacy_looping():
    # ship_green_on_abort=False → byte-identical to old behaviour even when green.
    executor, complete = _green_build_then_cycle(build_ok=True)
    res = asyncio.run(
        ab.run_agent_build(
            system_prompt="s",
            user_prompt="u",
            model="cheap",
            execute=executor,
            complete=complete,
            max_steps=14,
            ship_green_on_abort=False,
        )
    )
    assert res.done is False
    assert res.stop_reason in ("looping", "exploring")
