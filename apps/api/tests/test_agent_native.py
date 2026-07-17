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


@pytest.mark.asyncio
async def test_native_proseless_done_gets_human_summary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """end_turn with NO text must not leak "(no tool call)" into the chat —
    the summary becomes the user-visible assistant message (observed live)."""

    async def fake_call(client: Any, url: str, convo: Any, system: str) -> dict[str, Any]:
        return {"stop_reason": "end_turn", "content": []}  # prose-less finish

    monkeypatch.setattr(agent_native, "_call_messages", fake_call)

    async def execute(action: Any) -> dict[str, Any]:  # never reached
        return {"ok": True}

    res = await agent_native.run_native_build(
        system="s", task="t", execute=execute, max_steps=5,
    )
    assert res.done is True
    assert res.stop_reason == "no_tool"
    assert "(no tool call)" not in res.summary
    assert "Готово" in res.summary


def test_native_agent_has_eyes_and_taste() -> None:
    """Smart-agent contract (deep-research 2026-07-17): the native builder must
    ADVERTISE the `see` vision tool (screenshot → design self-critique) AND carry
    design-system + think-first rules in its system prompt — the two levers that
    lift TASTE and cut bugs. A dropped `see` or a stripped taste block silently
    reverts the agent to «компилируется, но уродливо»."""
    from omnia_api.services import agent_builder as B

    names = [t["name"] for t in agent_native._TOOLS]
    assert "see" in names, "native agent must offer the `see` vision-critique tool"
    # Dead schema is worse than none — the executor must actually route `see`.
    assert "see" in B._KNOWN_ACTIONS, "executor must route the `see` action"

    sysp = agent_native.native_system_prompt("STACK GUIDE", None)
    assert "ВКУС В ДИЗАЙНЕ" in sysp, "design-taste + see-loop rules must be present"
    assert "root-cause" in sysp, "think-before-fix (fewer-bugs) rule must be present"
    assert "`see` главный" in agent_native._NATIVE_PREAMBLE  # visual-critique cycle


def test_native_agent_can_generate_media() -> None:
    """generate_media (flux image + Kling video, same key) must be ADVERTISED as a
    native tool AND routed by the executor, and the preamble must teach WHEN/HOW to
    use video (scroll-driven hero / 3D fly-through). A dropped tool or missing route
    means the agent can never build the cinematic-video sites the owner asked for."""
    from omnia_api.services import agent_builder as B

    names = [t["name"] for t in agent_native._TOOLS]
    assert "generate_media" in names, "native agent must offer the generate_media tool"
    assert "generate_media" in B._KNOWN_ACTIONS, "executor must route generate_media"

    # The tool schema must expose kind + prompt (required) so the model can pick
    # image vs video — a schema that dropped `kind` would silently force images.
    media = next(t for t in agent_native._TOOLS if t["name"] == "generate_media")
    props = media["input_schema"]["properties"]
    assert "kind" in props and "prompt" in props
    assert media["input_schema"]["required"] == ["kind", "prompt"]
    # Keyframe interpolation (Flux first+last → Kling) is the signature move — the
    # schema MUST offer first_frame/last_frame or the model can't request it.
    assert "first_frame" in props and "last_frame" in props

    # The preamble must carry the video design pattern (scroll-scrub / bg loop) +
    # the keyframe recipe + hover microinteractions, else the model has the tool
    # but no idea when/how to reach for a clip or to make the UI feel alive.
    assert "МЕДИА" in agent_native._NATIVE_PREAMBLE
    assert "video" in agent_native._NATIVE_PREAMBLE.lower()
    assert "КЕЙФРЕЙМ" in agent_native._NATIVE_PREAMBLE  # first+last frame recipe
    assert "МИКРО-ВЗАИМОДЕЙСТВИЯ" in agent_native._NATIVE_PREAMBLE  # hover rules
    # From a PLAIN prompt the agent must reason about the MODEL CHAIN itself
    # (Flux frames → Kling motion → scroll embed) — not require the user to name
    # models. Drop this and a normal request never triggers the cinematic combo.
    assert "ОРКЕСТРАЦИЯ МОДЕЛЕЙ" in agent_native._NATIVE_PREAMBLE
    # Scroll-scrub jank is a real defect (measured 2026-07-17) — the preamble must
    # carry the 60fps smoothness contract (rAF-only currentTime, GPU compositing).
    assert "ПЛАВНОСТЬ" in agent_native._NATIVE_PREAMBLE


def test_generate_media_returns_url_in_model_visible_field() -> None:
    """The whole feature dies if the agent can't SEE the generated URL: the native
    loop feeds a tool result back via `content`/`detail`/`error` (never the bare
    `url` key), so generate_media MUST echo the URL inside `content`. This guards
    the review-2026-07-17 critical: url-only → model gets "ok" → no <img>/<video>."""
    import asyncio

    from omnia_api.services import agent_media

    async def _fake_gen(project_id: str, prompt: str) -> str:
        return "http://minio.local/omnia-images/p/deadbeef.png"

    # Stub the flux call so this stays a pure unit test (no gateway/MinIO).
    orig = agent_media.image_resolver.generate_and_store_image
    agent_media.image_resolver.generate_and_store_image = _fake_gen  # type: ignore[assignment]
    try:
        res = asyncio.run(
            agent_media.generate_media("p", kind="image", prompt="cinematic hero")
        )
    finally:
        agent_media.image_resolver.generate_and_store_image = orig  # type: ignore[assignment]

    assert res["ok"] is True
    assert res["url"] == "http://minio.local/omnia-images/p/deadbeef.png"
    # The URL must live in a field the model actually reads back (content), not
    # only in `url` which _obs_to_tool_result ignores.
    assert res["url"] in str(res["content"])
    body = agent_native._obs_to_tool_result("tu_1", res)["content"]
    assert res["url"] in body  # end-to-end: model truly receives the URL
