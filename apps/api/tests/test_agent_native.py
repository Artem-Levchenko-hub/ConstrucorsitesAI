"""Tests for the native tool-use build loop (services/agent_native).

Covers: `_module_not_found_hint` (anti-hallucination recovery), the
EXPLORE-STALL no-write guard (nudge → abort as 'exploring'), and the infra
circuit breaker (container/orchestrator dead → abort as 'infra_error' instead of
grinding the step budget — the 2026-07-08 hibernate-mid-build incident).
"""

from __future__ import annotations

import inspect
from typing import Any

import pytest

from omnia_api.services import agent_native
from omnia_api.services.agent_native import _module_not_found_hint


def test_native_agent_uses_sonnet_while_autoheal_keeps_gemini() -> None:
    from omnia_api.services import autoheal

    assert agent_native._MODEL == "claude-sonnet-5"
    assert autoheal._HEAL_MODEL == "gemini-3.1-pro-preview-customtools"


def test_max_native_prompt_disables_incompatible_generic_proof_tools() -> None:
    prompt = agent_native.native_system_prompt("MAX PLATFORM CORE CONTRACT\nBuild the app")

    assert "takes precedence" in prompt
    assert "Do NOT call or retry probe/verify_isolation" in prompt
    assert "run runtime_check after the final write" in prompt
    assert "signed MAX preview session" in prompt


def test_first_max_build_has_no_template_and_cannot_finish_at_core_stage() -> None:
    """The verified core is a seed, never a replacement for the Google agent."""
    from omnia_api.routers import messages

    source = inspect.getsource(messages._process_prompt)

    assert 'stop_reason="deterministic_template"' not in source
    assert "_merge_seeded_agent_files" in source
    assert "agent_native.run_native_build" in source
    assert "build_max_product_contract" in source
    assert "max_completion_gap" in source
    assert "completion_check=_completion_check" in source
    assert "_agent_step_budget" in source
    assert "configured_steps=_agent_steps" in source
    assert "max_steps=_agent_steps" in source
    assert '"autonomous_recovery"' not in source
    assert "max_source_completion_gap" not in source
    assert "_seg < 2" not in source
    assert "_first_max_without_product" in source
    assert "func.length(func.trim(Snapshot.prompt_text)) > 0" in source
    assert '_bounded_stop and project_template != "max_miniapp"' in source
    assert "if path not in MAX_MODEL_LOCKED_FILES" in source
    assert "Direct DB access is forbidden in MAX product files." in source
    assert "max_model_write_rejection" in source
    assert "create_max_preview_session" in source
    assert "_recover_max_resume_prompt" in source
    assert '"rm -f -- src/app/page.tsx"' in source
    assert "{} if not _max_has_generated_snapshot else dict(current_files)" in source
    assert "normalize_max_globals_css" in source
    assert "seed_design_memory" in source
    assert "await asyncio.sleep(2)" in source


def test_failed_max_resume_recovers_the_original_brief() -> None:
    from omnia_api.routers.messages import _recover_max_resume_prompt

    assert (
        _recover_max_resume_prompt(
            ["продолжи", "Продолжай сборку", "Собери фитнес-тренера с ИИ и статистикой"]
        )
        == "Собери фитнес-тренера с ИИ и статистикой"
    )
    assert _recover_max_resume_prompt(["продолжи", "доделай"]) is None


def test_rolled_back_max_generation_is_never_reported_as_done() -> None:
    from omnia_api.routers.messages import _agent_result_message
    from omnia_api.services.agent_builder import AgentResult

    result = AgentResult(
        done=False,
        summary="Первая генерация не завершена; оставлена безопасная основа.",
        files={},
        steps=30,
        stop_reason="core_only_rolled_back",
    )

    assert _agent_result_message(result, is_edit=False).startswith("Первая генерация не завершена")


def test_seeded_max_files_are_committed_with_agent_customisations() -> None:
    from omnia_api.routers.messages import _merge_seeded_agent_files

    starter = {"src/app/page.tsx": "starter", "src/app/globals.css": "safe css"}
    generated = {"src/app/page.tsx": "google agent result", "src/components/Profile.tsx": "ui"}

    merged = _merge_seeded_agent_files(starter, generated)

    assert merged == {
        "src/app/page.tsx": "google agent result",
        "src/app/globals.css": "safe css",
        "src/components/Profile.tsx": "ui",
    }
    assert starter["src/app/page.tsx"] == "starter"


def test_hint_none_on_clean_or_unrelated_error() -> None:
    assert _module_not_found_hint("") is None
    assert _module_not_found_hint("Build succeeded, 0 errors") is None
    # a real error that is NOT a missing @/ module → no hint (don't over-fire)
    assert (
        _module_not_found_hint("src/app/page.tsx(3,10): error TS2345: Argument of type 'string'")
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
        f"src/a{i}.ts: error TS2307: Cannot find module '@/lib/m{i}'" for i in range(8)
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

    async def fake_call(
        client: Any, url: str, convo: Any, system: str, **kwargs: Any
    ) -> dict[str, Any]:
        calls["n"] += 1
        return _turn(("read_file", {"path": "a.ts"}), ("list_dir", {"path": "."}))

    monkeypatch.setattr(agent_native, "_call_messages", fake_call)

    async def execute(action: Any) -> dict[str, Any]:
        return {"ok": False, "error": "infra: Orchestrator returned 500", "infra_dead": True}

    res = await agent_native.run_native_build(
        system="s",
        task="t",
        execute=execute,
        max_steps=40,
    )
    assert res.stop_reason == "infra_error"
    assert "unreachable" in res.summary
    assert calls["n"] == agent_native._INFRA_DEAD_ABORT_AT  # aborted, not ground out


@pytest.mark.asyncio
async def test_native_no_write_guard_nudges_then_aborts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Endless successful READS (no writes) → nudge from turn 6, abort at 12 as
    'exploring' — messages.py's honest-result branches consume that."""

    async def fake_call(
        client: Any, url: str, convo: Any, system: str, **kwargs: Any
    ) -> dict[str, Any]:
        return _turn(("read_file", {"path": "a.ts"}))

    monkeypatch.setattr(agent_native, "_call_messages", fake_call)

    async def execute(action: Any) -> dict[str, Any]:
        return {"ok": True, "content": "file body"}

    res = await agent_native.run_native_build(
        system="s",
        task="t",
        execute=execute,
        max_steps=40,
    )
    assert res.stop_reason == "exploring"
    assert res.steps == agent_native._NO_WRITE_ABORT_AT
    assert "[LOOP GUARD]" in str(res.transcript)  # the nudge actually landed


@pytest.mark.asyncio
async def test_first_max_build_locks_discovery_tools_until_first_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """After discovery closes, the next provider turn is forced to write the
    fixed MAX entry instead of spending another paid turn on a rejected read."""

    calls = {"n": 0}
    executed_reads = 0

    async def fake_call(
        client: Any, url: str, convo: Any, system: str, **kwargs: Any
    ) -> dict[str, Any]:
        calls["n"] += 1
        if calls["n"] <= agent_native._MAX_PREWRITE_DISCOVERY_TURNS:
            assert kwargs["tools"] is None
            assert kwargs["tool_choice"] is None
            return _turn(("read_file", {"path": "src/app/layout.tsx"}))
        if calls["n"] == agent_native._MAX_PREWRITE_DISCOVERY_TURNS + 1:
            forced_tools = kwargs["tools"]
            assert [tool["name"] for tool in forced_tools] == ["write_file"]
            assert forced_tools[0]["input_schema"]["properties"]["path"] == {
                "type": "string",
                "enum": ["src/app/page.tsx"],
            }
            assert forced_tools[0]["description"] == agent_native._MAX_ENTRY_WRITE_GUIDANCE
            assert "self-contained src/app/page.tsx" in str(convo[-1])
            assert "do not import product components" in str(convo[-1])
            assert kwargs["tool_choice"] == {"type": "tool", "name": "write_file"}
            return _turn(
                ("write_file", {"path": "src/app/page.tsx", "content": "export default 1"})
            )
        if calls["n"] == agent_native._MAX_PREWRITE_DISCOVERY_TURNS + 2:
            assert kwargs["tools"] is None
            assert kwargs["tool_choice"] is None
            return _turn(("build", {}))
        return _turn(("done", {"summary": "Готово"}))

    monkeypatch.setattr(agent_native, "_call_messages", fake_call)

    async def execute(action: Any) -> dict[str, Any]:
        nonlocal executed_reads
        if action.name == "read_file":
            executed_reads += 1
            return {"ok": True, "content": "platform core"}
        if action.name == "write_file":
            return {"ok": True, "content": action.args["content"]}
        return {"ok": True, "detail": "clean"}

    res = await agent_native.run_native_build(
        system="MAX VERIFICATION OVERRIDE",
        task="build product",
        execute=execute,
        completion_check=lambda written, evidence: None if written else "write product files",
        max_steps=40,
    )

    assert res.done is True
    assert res.files == {"src/app/page.tsx": "export default 1"}
    assert executed_reads == agent_native._MAX_PREWRITE_DISCOVERY_TURNS
    assert calls["n"] == agent_native._MAX_PREWRITE_DISCOVERY_TURNS + 3
    assert agent_native._MAX_PREWRITE_LOCK_RESULT in str(res.transcript)


@pytest.mark.asyncio
async def test_max_auxiliary_write_does_not_unlock_product_entry_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One helper write is not a product. The next mutation must create the
    required user-facing entry instead of extending support code indefinitely."""
    turns = iter(
        [
            _turn(("write_file", {"path": "src/lib/helper.ts", "content": "export const x=1"})),
            _turn(("write_file", {"path": "src/app/page.tsx", "content": "export default 1"})),
            _turn(("build", {})),
            _turn(("done", {"summary": "Готово"})),
        ]
    )

    calls = {"n": 0}

    async def fake_call(
        client: Any, url: str, convo: Any, system: str, **kwargs: Any
    ) -> dict[str, Any]:
        calls["n"] += 1
        if calls["n"] == 1:
            assert kwargs["tools"] is None
            assert kwargs["tool_choice"] is None
        elif calls["n"] == 2:
            assert [tool["name"] for tool in kwargs["tools"]] == ["write_file"]
            assert kwargs["tools"][0]["description"] == agent_native._MAX_ENTRY_WRITE_GUIDANCE
            assert kwargs["tool_choice"] == {"type": "tool", "name": "write_file"}
            assert agent_native._MAX_PRODUCT_ENTRY_REQUIRED_RESULT in str(convo[-1])
        return next(turns)

    monkeypatch.setattr(agent_native, "_call_messages", fake_call)
    executed: list[tuple[str, str]] = []

    async def execute(action: Any) -> dict[str, Any]:
        executed.append((action.name, action.path))
        return {"ok": True, "content": action.args.get("content", ""), "detail": "clean"}

    res = await agent_native.run_native_build(
        system="MAX VERIFICATION OVERRIDE",
        task="build product",
        execute=execute,
        completion_check=lambda files, evidence: (
            None if "src/app/page.tsx" in files else "Create src/app/page.tsx."
        ),
        max_steps=10,
    )

    assert res.done is True
    assert ("write_file", "src/lib/helper.ts") in executed
    assert ("write_file", "src/lib/second.ts") not in executed
    assert ("read_file", "package.json") not in executed
    assert ("write_file", "src/app/page.tsx") in executed
    assert agent_native._MAX_PRODUCT_ENTRY_REQUIRED_RESULT in str(res.transcript)
    assert agent_native._MAX_ENTRY_WRITE_GUIDANCE in str(res.transcript)


@pytest.mark.asyncio
async def test_max_auxiliary_rewrites_are_not_product_progress(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Changing the same helper forever must hit the bounded no-progress stop."""
    calls = {"n": 0}

    async def fake_call(
        client: Any, url: str, convo: Any, system: str, **kwargs: Any
    ) -> dict[str, Any]:
        calls["n"] += 1
        if calls["n"] == 1:
            return _turn(("write_file", {"path": "src/app/page.tsx", "content": "page"}))
        return _turn(
            (
                "write_file",
                {"path": "src/lib/helper.ts", "content": f"export const n={calls['n']}"},
            )
        )

    monkeypatch.setattr(agent_native, "_call_messages", fake_call)

    async def execute(action: Any) -> dict[str, Any]:
        return {"ok": True, "content": action.args.get("content", ""), "detail": "written"}

    res = await agent_native.run_native_build(
        system="MAX VERIFICATION OVERRIDE",
        task="build product",
        execute=execute,
        completion_check=lambda files, evidence: None,
        max_steps=40,
    )

    assert res.stop_reason == "exploring"
    assert "without user-facing product progress" in res.summary
    assert calls["n"] == 1 + agent_native._NO_WRITE_ABORT_AT


@pytest.mark.asyncio
async def test_native_write_resets_no_write_streak(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A write every few turns keeps the streak below the abort threshold —
    the guard must not fire on a normally-working build."""
    counter = {"n": 0}

    async def fake_call(
        client: Any, url: str, convo: Any, system: str, **kwargs: Any
    ) -> dict[str, Any]:
        counter["n"] += 1
        if counter["n"] % 5 == 0:
            return _turn(("write_file", {"path": f"f{counter['n']}.ts", "content": "x"}))
        return _turn(("read_file", {"path": "a.ts"}))

    monkeypatch.setattr(agent_native, "_call_messages", fake_call)

    async def execute(action: Any) -> dict[str, Any]:
        return {"ok": True, "content": "body"}

    res = await agent_native.run_native_build(
        system="s",
        task="t",
        execute=execute,
        max_steps=15,
    )
    assert res.stop_reason == "max_steps_green"  # never tripped the exploring abort
    assert len(res.files) == 3  # the three successful writes were tracked


@pytest.mark.asyncio
async def test_native_edit_file_counts_as_write_and_lands_in_files(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """edit_file success resets the streak AND its post-edit content is tracked
    (closes the gap where only write_file dirtied the done fact-gate)."""
    counter = {"n": 0}

    async def fake_call(
        client: Any, url: str, convo: Any, system: str, **kwargs: Any
    ) -> dict[str, Any]:
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
        system="s",
        task="t",
        execute=execute,
        max_steps=5,
    )
    assert res.stop_reason == "max_steps_green"
    assert res.files == {"e.ts": "post-edit content"}


@pytest.mark.asyncio
async def test_non_max_file_keys_are_not_normalized(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """MAX entry matching may normalize a path, but other stacks must preserve
    the executor's exact file key (especially leading-dot config files)."""
    turns = iter(
        [
            _turn(("write_file", {"path": ".config", "content": "exact"})),
            _turn(("build", {})),
            _turn(("done", {"summary": "done"})),
        ]
    )

    async def fake_call(
        client: Any, url: str, convo: Any, system: str, **kwargs: Any
    ) -> dict[str, Any]:
        return next(turns)

    monkeypatch.setattr(agent_native, "_call_messages", fake_call)

    async def execute(action: Any) -> dict[str, Any]:
        return {"ok": True, "content": action.args.get("content", ""), "detail": "clean"}

    res = await agent_native.run_native_build(
        system="generic stack",
        task="write config",
        execute=execute,
        max_steps=5,
    )

    assert res.done is True
    assert res.files == {".config": "exact"}


@pytest.mark.asyncio
async def test_native_completion_check_rejects_thin_done_and_keeps_building(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A clean compiler result cannot bypass a product-specific fidelity gate."""
    turns = iter(
        [
            _turn(("write_file", {"path": "src/app/page.tsx", "content": "page"})),
            _turn(("build", {})),
            _turn(("done", {"summary": "too early"})),
            _turn(
                (
                    "write_file",
                    {"path": "src/components/Feature.tsx", "content": "feature"},
                )
            ),
            _turn(("build", {})),
            _turn(("done", {"summary": "complete"})),
        ]
    )

    async def fake_call(
        client: Any, url: str, convo: Any, system: str, **kwargs: Any
    ) -> dict[str, Any]:
        return next(turns)

    monkeypatch.setattr(agent_native, "_call_messages", fake_call)

    async def execute(action: Any) -> dict[str, Any]:
        return {
            "ok": True,
            "content": action.args.get("content", ""),
            "detail": "clean",
        }

    def check(files: Any, evidence: Any) -> str | None:
        return None if len(files) >= 2 else "Need a real feature component."

    res = await agent_native.run_native_build(
        system="s",
        task="t",
        execute=execute,
        completion_check=check,
        max_steps=8,
    )

    assert res.done is True
    assert res.summary == "complete"
    assert set(res.files) == {"src/app/page.tsx", "src/components/Feature.tsx"}
    assert "Need a real feature component" in str(res.transcript)


@pytest.mark.asyncio
async def test_native_hard_stop_runs_missing_local_proofs_before_shipping(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A green tree must not fail only because the provider turn ended before
    deterministic runtime, persistence and visual proof tool calls."""

    async def fake_call(
        client: Any, url: str, convo: Any, system: str, **kwargs: Any
    ) -> dict[str, Any]:
        return _turn(("write_file", {"path": "src/app/page.tsx", "content": "page"}))

    monkeypatch.setattr(agent_native, "_call_messages", fake_call)
    actions: list[str] = []

    async def execute(action: Any) -> dict[str, Any]:
        actions.append(action.name)
        return {
            "ok": True,
            "content": action.args.get("content", ""),
            "detail": "green",
        }

    def check(files: Any, evidence: Any) -> str | None:
        assert "src/app/page.tsx" in files
        if evidence.get("runtime_check_after_write", 0) < 1:
            return "Run runtime_check after the last source write."
        if evidence.get("probe_after_write", 0) < 1:
            return "Run probe after the last source write."
        if evidence.get("see", 0) < 2:
            return "Run a second see after the visual fix."
        return None

    res = await agent_native.run_native_build(
        system="s",
        task="t",
        execute=execute,
        completion_check=check,
        max_steps=1,
    )

    assert res.done is True
    assert res.stop_reason == "max_steps_green"
    assert actions == ["write_file", "build", "runtime_check", "probe", "see", "see"]


@pytest.mark.asyncio
async def test_native_tool_call_emits_chat_progress(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every executed native tool remains visible through the agent.step WS path."""
    calls = {"n": 0}

    async def fake_call(
        client: Any, url: str, convo: Any, system: str, **kwargs: Any
    ) -> dict[str, Any]:
        calls["n"] += 1
        if calls["n"] == 1:
            return _turn(("write_file", {"path": "src/app.ts", "content": "ok"}))
        return {"stop_reason": "end_turn", "content": [{"type": "text", "text": "Готово"}]}

    monkeypatch.setattr(agent_native, "_call_messages", fake_call)

    async def execute(action: Any) -> dict[str, Any]:
        return {
            "ok": True,
            "content": action.args.get("content", ""),
            "detail": "written" if action.name != "build" else "clean",
        }

    events: list[tuple[str, dict[str, Any]]] = []

    async def emit(event_type: str, data: dict[str, Any]) -> None:
        events.append((event_type, data))

    await agent_native.run_native_build(
        system="s",
        task="t",
        execute=execute,
        emit=emit,
        max_steps=5,
    )

    progress = [data for event_type, data in events if event_type == "agent.step"]
    assert len(progress) == 2
    assert progress[0]["step"] == 0
    assert progress[0]["action"] == "write_file"
    assert progress[0]["path"] == "src/app.ts"
    assert "ok" in progress[0]["detail"]
    assert progress[0]["ok"] is True


@pytest.mark.asyncio
async def test_native_proseless_done_gets_human_summary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """end_turn with NO text must not leak "(no tool call)" into the chat —
    the summary becomes the user-visible assistant message (observed live)."""

    async def fake_call(
        client: Any, url: str, convo: Any, system: str, **kwargs: Any
    ) -> dict[str, Any]:
        return {"stop_reason": "end_turn", "content": []}  # prose-less finish

    monkeypatch.setattr(agent_native, "_call_messages", fake_call)

    async def execute(action: Any) -> dict[str, Any]:
        return {"ok": True}

    res = await agent_native.run_native_build(
        system="s",
        task="t",
        execute=execute,
        max_steps=5,
    )
    assert res.done is True
    assert res.stop_reason == "max_steps_green"
    assert "(no tool call)" not in res.summary
    assert "Готово" in res.summary


@pytest.mark.asyncio
async def test_native_hard_clamps_legacy_limit_and_forwards_trace_ids(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []

    async def fake_call(
        client: Any, url: str, convo: Any, system: str, **kwargs: Any
    ) -> dict[str, Any]:
        calls.append(kwargs)
        number = len(calls)
        if number % 5 == 0:
            return _turn(("write_file", {"path": f"f{number}.ts", "content": "ok"}))
        return _turn(("read_file", {"path": "src/app.ts"}))

    monkeypatch.setattr(agent_native, "_call_messages", fake_call)

    async def execute(action: Any) -> dict[str, Any]:
        return {"ok": True, "content": action.args.get("content", ""), "detail": "clean"}

    res = await agent_native.run_native_build(
        system="s",
        task="t",
        execute=execute,
        user_id="11111111-1111-1111-1111-111111111111",
        project_id="22222222-2222-2222-2222-222222222222",
        run_id="33333333-3333-3333-3333-333333333333",
        message_id="44444444-4444-4444-4444-444444444444",
        max_steps=120,
    )

    assert len(calls) == agent_native._HARD_MAX_STEPS == 40
    assert res.stop_reason == "max_steps_green"
    assert calls[0]["stage"] == "build_plan"
    assert all(call["project_id"] == "22222222-2222-2222-2222-222222222222" for call in calls)
    assert all(call["run_id"] == "33333333-3333-3333-3333-333333333333" for call in calls)
    assert all(call["user_id"] == "11111111-1111-1111-1111-111111111111" for call in calls)


@pytest.mark.asyncio
async def test_provider_limit_stops_immediately_and_keeps_green_tree(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = {"n": 0}

    async def fake_call(
        client: Any, url: str, convo: Any, system: str, **kwargs: Any
    ) -> dict[str, Any]:
        calls["n"] += 1
        raise RuntimeError("PAYMENT_REQUIRED")

    monkeypatch.setattr(agent_native, "_call_messages", fake_call)

    async def execute(action: Any) -> dict[str, Any]:
        assert action.name == "build"
        return {"ok": True, "detail": "clean"}

    res = await agent_native.run_native_build(system="s", task="t", execute=execute, max_steps=120)

    assert calls["n"] == 1
    assert res.done is True
    assert res.stop_reason == "provider_stopped_green"


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
        res = asyncio.run(agent_media.generate_media("p", kind="image", prompt="cinematic hero"))
    finally:
        agent_media.image_resolver.generate_and_store_image = orig  # type: ignore[assignment]

    assert res["ok"] is True
    assert res["url"] == "http://minio.local/omnia-images/p/deadbeef.png"
    # The URL must live in a field the model actually reads back (content), not
    # only in `url` which _obs_to_tool_result ignores.
    assert res["url"] in str(res["content"])
    body = agent_native._obs_to_tool_result("tu_1", res)["content"]
    assert res["url"] in body  # end-to-end: model truly receives the URL
