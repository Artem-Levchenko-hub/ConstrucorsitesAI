"""Tests for the native tool-use build loop (services/agent_native).

Covers: `_module_not_found_hint` (anti-hallucination recovery), the
EXPLORE-STALL no-write guard (nudge → abort as 'exploring'), and the infra
circuit breaker (container/orchestrator dead → abort as 'error' instead of
grinding the step budget — the 2026-07-08 hibernate-mid-build incident).
"""

from __future__ import annotations

from typing import Any

import pytest

from omnia_api.services import agent_native
from omnia_api.services.agent_native import _module_not_found_hint


def test_hint_none_on_clean_or_unrelated_error() -> None:
    assert _module_not_found_hint("") is None
    assert _module_not_found_hint("Build succeeded, 0 errors") is None
    # a real error that is NOT a missing @/ module → no hint (don't over-fire)
    assert (
        _module_not_found_hint(
            "src/app/page.tsx(3,10): error TS2345: Argument of type 'string'"
        )
        is None
    )
    # a bare package (not an @/ alias) is a dependency problem, not the
    # SDK-hallucination this hint addresses → stay silent.
    assert _module_not_found_hint("Cannot find module 'postgres'") is None


def test_hint_fires_on_ts2307_internal_alias() -> None:
    out = _module_not_found_hint(
        "src/lib/sdk/tasks.ts(4,24): error TS2307: Cannot find module "
        "'@/lib/entities/engine' or its corresponding type declarations."
    )
    assert out is not None
    assert "@/lib/entities/engine" in out
    assert "do not create" in out.lower()
    # steers away from fabricating an SDK/engine wrapper
    assert "sdk" in out.lower() and "engine" in out.lower()


def test_hint_dedupes_and_caps_modules() -> None:
    blob = "\n".join(
        f"src/a{i}.ts: error TS2307: Cannot find module '@/lib/m{i}'"
        for i in range(8)
    )
    blob += "\nsrc/z.ts: error TS2307: Cannot find module '@/lib/m0'"  # repeat
    out = _module_not_found_hint(blob)
    assert out is not None
    assert out.count("@/lib/m0") == 1  # deduped
    listed = [m for m in (f"@/lib/m{i}" for i in range(8)) if m in out]
    assert len(listed) == 5  # capped to 5


# ── run_native_build loop guards (stubbed LLM + executor) ────────────────────


def _turn(*tools: tuple[str, dict[str, Any]]) -> dict[str, Any]:
    """An Anthropic-shaped assistant turn with the given tool_use blocks."""
    return {
        "stop_reason": "tool_use",
        "content": [
            {"type": "tool_use", "id": f"tu_{i}", "name": name, "input": args}
            for i, (name, args) in enumerate(tools)
        ],
    }


@pytest.mark.asyncio
async def test_native_infra_breaker_aborts_after_dead_turns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Container dead → every op infra_dead → abort in _INFRA_DEAD_ABORT_AT turns
    (the 2026-07-08 regression: it used to grind the whole step budget)."""
    calls = {"n": 0}

    async def fake_call(client: Any, url: str, convo: Any, system: str) -> dict[str, Any]:
        calls["n"] += 1
        return _turn(("read_file", {"path": "a.ts"}), ("list_dir", {"path": "."}))

    monkeypatch.setattr(agent_native, "_call_messages", fake_call)

    async def execute(action: Any) -> dict[str, Any]:
        return {"ok": False, "error": "infra: Orchestrator returned 500", "infra_dead": True}

    res = await agent_native.run_native_build(
        system="s", task="t", execute=execute, max_steps=40,
    )
    assert res.stop_reason == "error"
    assert "unreachable" in res.summary
    assert calls["n"] == agent_native._INFRA_DEAD_ABORT_AT  # aborted, not ground out


@pytest.mark.asyncio
async def test_native_no_write_guard_nudges_then_aborts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Endless successful READS (no writes) → nudge from turn 6, abort at 12 as
    'exploring' — messages.py's honest-result branches consume that."""

    async def fake_call(client: Any, url: str, convo: Any, system: str) -> dict[str, Any]:
        return _turn(("read_file", {"path": "a.ts"}))

    monkeypatch.setattr(agent_native, "_call_messages", fake_call)

    async def execute(action: Any) -> dict[str, Any]:
        return {"ok": True, "content": "file body"}

    res = await agent_native.run_native_build(
        system="s", task="t", execute=execute, max_steps=40,
    )
    assert res.stop_reason == "exploring"
    assert res.steps == agent_native._NO_WRITE_ABORT_AT
    assert "[LOOP GUARD]" in str(res.transcript)  # the nudge actually landed


@pytest.mark.asyncio
async def test_native_write_resets_no_write_streak(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A write every few turns keeps the streak below the abort threshold —
    the guard must not fire on a normally-working build."""
    counter = {"n": 0}

    async def fake_call(client: Any, url: str, convo: Any, system: str) -> dict[str, Any]:
        counter["n"] += 1
        if counter["n"] % 5 == 0:
            return _turn(("write_file", {"path": f"f{counter['n']}.ts", "content": "x"}))
        return _turn(("read_file", {"path": "a.ts"}))

    monkeypatch.setattr(agent_native, "_call_messages", fake_call)

    async def execute(action: Any) -> dict[str, Any]:
        return {"ok": True, "content": "body"}

    res = await agent_native.run_native_build(
        system="s", task="t", execute=execute, max_steps=15,
    )
    assert res.stop_reason == "max_steps"  # never tripped the exploring abort
    assert len(res.files) == 3  # the three successful writes were tracked


@pytest.mark.asyncio
async def test_native_edit_file_counts_as_write_and_lands_in_files(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """edit_file success resets the streak AND its post-edit content is tracked
    (closes the gap where only write_file dirtied the done fact-gate)."""
    counter = {"n": 0}

    async def fake_call(client: Any, url: str, convo: Any, system: str) -> dict[str, Any]:
        counter["n"] += 1
        if counter["n"] == 1:
            return _turn(("edit_file", {"path": "e.ts", "search": "a", "replace": "b"}))
        return _turn(("read_file", {"path": "a.ts"}))

    monkeypatch.setattr(agent_native, "_call_messages", fake_call)

    async def execute(action: Any) -> dict[str, Any]:
        if action.name == "edit_file":
            return {"ok": True, "content": "post-edit content", "detail": "patched e.ts"}
        return {"ok": True, "content": "body"}

    res = await agent_native.run_native_build(
        system="s", task="t", execute=execute, max_steps=5,
    )
    assert res.stop_reason == "max_steps"
    assert res.files == {"e.ts": "post-edit content"}
