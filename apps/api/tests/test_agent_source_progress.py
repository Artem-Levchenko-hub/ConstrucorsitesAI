"""Real legacy loop against per-action source state, without provider/Cell I/O."""

from __future__ import annotations

import json
from uuid import uuid4

import pytest

from yleum_api.services import agent_builder as ab
from yleum_api.services.generation.progress import GenerationProgress


def action(name: str, **args) -> str:
    return f'<omnia:action name="{name}">{json.dumps(args)}</omnia:action>'


@pytest.mark.asyncio
async def test_threshold_bash_executes_and_returns_real_source_change_to_model():
    state = {"src/app/page.tsx": "before"}
    executed = []
    observed = []
    replies = [
        action("read_file", path="a.ts"),
        action("read_file", path="b.ts"),
        action("bash", cmd="apply_requested_change"),
        action("read_file", path="src/app/page.tsx"),
        action("done"),
    ]

    async def complete(messages, *_args, **_kwargs):
        observed.append([m["content"] for m in messages])
        return replies[len(observed) - 1]

    async def execute(tool):
        executed.append(tool.name)
        if tool.name == "bash":
            state["src/app/page.tsx"] = "after"
            return {"ok": True, "detail": "applied", "files": dict(state)}
        return {"ok": True, "content": "source"}

    async def snapshot():
        return dict(state)

    result = await ab.run_agent_build(
        system_prompt="sys",
        user_prompt="edit",
        model="test",
        complete=complete,
        execute=execute,
        edit_mode=True,
        max_steps=5,
        ship_green_on_abort=False,
        snapshot_files=snapshot,
    )
    assert executed == ["read_file", "read_file", "bash", "read_file"]
    assert state["src/app/page.tsx"] == "after"
    assert any("[observation: bash OK]\napplied" in m for m in observed[-1])
    assert result.done


@pytest.mark.asyncio
async def test_unique_failed_edits_do_not_reset_no_progress_streak():
    calls = 0
    observations = []

    async def complete(messages, *_args, **_kwargs):
        nonlocal calls
        calls += 1
        observations.extend(m["content"] for m in messages if "exact mismatch" in m["content"])
        return action(
            "edit_file", path=f"src/app/page-{calls}.tsx", search=f"absent-{calls}", replace="x"
        )

    async def execute(_tool):
        return {"ok": False, "error": "exact mismatch", "environment_mutated": False}

    result = await ab.run_agent_build(
        system_prompt="sys",
        user_prompt="edit",
        model="test",
        complete=complete,
        execute=execute,
        edit_mode=True,
        max_steps=40,
        ship_green_on_abort=False,
    )
    assert observations  # failure is delivered back to the model
    assert result.stop_reason == "exploring"
    assert calls == result.steps == ab._NO_WRITE_ABORT_AT + 1


@pytest.mark.asyncio
async def test_step_reports_actual_failed_completion_after_execution():
    events = []
    calls = 0

    async def complete(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        return (
            action("edit_file", path="page.tsx", search="missing", replace="new")
            if calls == 1
            else action("done")
        )

    async def emit(event, data):
        events.append((event, data))

    async def execute(_tool):
        assert not any(e == "agent.step" for e, _ in events)
        return {"ok": False, "error": "exact mismatch", "environment_mutated": False}

    await ab.run_agent_build(
        system_prompt="sys",
        user_prompt="edit",
        model="test",
        complete=complete,
        execute=execute,
        emit=emit,
        max_steps=2,
    )
    failed = next(
        data for event, data in events if event == "agent.step" and data["action"] == "edit_file"
    )
    assert failed["ok"] is False
    assert failed["detail"] == "exact mismatch"


@pytest.mark.asyncio
@pytest.mark.parametrize("result", [{}, {"ok": None}, {"ok": "false"}, {"ok": False}, {"ok": True}])
async def test_progress_preserves_only_actual_completion_boolean(monkeypatch, result):
    recorder = GenerationProgress(None, uuid4(), uuid4(), uuid4())
    rows = []

    async def record(row):
        rows.append(row)

    monkeypatch.setattr(recorder, "record_agent_step", record)
    await recorder.emit_agent_event("agent.step", {"action": "bash", **result})
    expected = result.get("ok") if type(result.get("ok")) is bool else None
    assert rows[0]["ok"] is expected


@pytest.mark.asyncio
async def test_successful_noop_bash_does_not_credit_cumulative_baseline_difference():
    state = {"page.tsx": "already changed before this action"}
    calls = 0
    executed = []
    events = []

    async def snapshot():
        return dict(state)

    async def complete(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        return action("bash", cmd=f"read-only-{calls}")

    async def execute(tool):
        executed.append(tool.name)
        return {"ok": True, "files": dict(state), "detail": "no changes"}

    async def emit(event, data):
        events.append((event, data))

    result = await ab.run_agent_build(
        system_prompt="sys",
        user_prompt="edit",
        model="test",
        complete=complete,
        execute=execute,
        emit=emit,
        edit_mode=True,
        max_steps=40,
        ship_green_on_abort=False,
        snapshot_files=snapshot,
    )
    assert executed == ["bash"] * 3  # threshold attempt executes, fourth discovery is skipped
    assert result.stop_reason == "exploring"
    assert result.steps < 40
    skipped = [d for e, d in events if e == "agent.step" and d.get("skipped")]
    assert skipped and all(d["ok"] is None for d in skipped)


@pytest.mark.asyncio
@pytest.mark.parametrize("ok", [True, False])
async def test_per_action_source_noop_edit_cannot_reset_progress(ok):
    calls = 0

    async def snapshot():
        return {"page.tsx": "unchanged"}

    async def complete(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        return action("edit_file", path=f"file-{calls}.tsx", search="missing", replace="same")

    async def execute(_tool):
        return {"ok": ok, "content": "same", "files": {"page.tsx": "unchanged"}}

    result = await ab.run_agent_build(
        system_prompt="sys",
        user_prompt="edit",
        model="test",
        complete=complete,
        execute=execute,
        edit_mode=True,
        max_steps=40,
        ship_green_on_abort=False,
        snapshot_files=snapshot,
    )
    assert result.stop_reason == "exploring"
    assert result.steps < 40


@pytest.mark.asyncio
async def test_source_changes_can_never_bypass_total_model_step_budget():
    state = {"page.tsx": "v0"}
    calls = 0

    async def snapshot():
        return dict(state)

    async def complete(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        return action("bash", cmd=f"apply-{calls}")

    async def execute(_tool):
        state["page.tsx"] = f"v{calls}"
        return {"ok": True}

    result = await ab.run_agent_build(
        system_prompt="sys",
        user_prompt="edit",
        model="test",
        complete=complete,
        execute=execute,
        edit_mode=True,
        max_steps=4,
        ship_green_on_abort=False,
        snapshot_files=snapshot,
    )
    assert calls == result.steps == 4
    assert result.stop_reason == "max_steps" and not result.done


@pytest.mark.asyncio
async def test_failed_partial_source_changes_never_credit_progress_or_preserve_green():
    state = {"page.tsx": "v0"}
    events = []
    calls = 0

    async def snapshot():
        return dict(state)

    async def complete(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            return action("build")
        if calls == 3:
            return action("done")
        return action("edit_file", path="page.tsx", search=str(calls), replace="x")

    async def execute(tool):
        if tool.name == "build":
            return {"ok": True}
        state["page.tsx"] = str(calls)
        # Even a contradictory receipt cannot erase an actual source delta.
        return {"ok": False, "environment_mutated": False, "error": "partial failure"}

    async def emit(event, data):
        events.append((event, data))

    result = await ab.run_agent_build(
        system_prompt="sys",
        user_prompt="edit",
        model="test",
        complete=complete,
        execute=execute,
        emit=emit,
        snapshot_files=snapshot,
        edit_mode=True,
        coordinator_handoff=True,
        max_steps=40,
        ship_green_on_abort=False,
    )
    assert not result.done and result.stop_reason == "exploring"
    rejected_done = next(d for e, d in events if e == "agent.step" and d["action"] == "done")
    assert rejected_done["skipped"] and rejected_done["ok"] is None
    assert "FAST_CHECK" in rejected_done["detail"]
    assert result.steps < 40


@pytest.mark.asyncio
async def test_progress_skipped_completion_cannot_be_reported_as_success(monkeypatch):
    recorder = GenerationProgress(None, uuid4(), uuid4(), uuid4())
    rows = []

    async def record(row):
        rows.append(row)

    monkeypatch.setattr(recorder, "record_agent_step", record)
    await recorder.emit_agent_event("agent.step", {"action": "done", "ok": True, "skipped": True})
    assert rows[0]["ok"] is None and rows[0]["skipped"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("changed,build_ok", [(True, True), (True, False), (False, True)])
@pytest.mark.parametrize("handoff", [False, True])
async def test_first_build_at_hard_exploration_limit_requires_real_source_delta(
    tmp_path, changed, build_ok, handoff,
):
    source = tmp_path / "app.py"
    source.write_text("value = 1")
    builds = []
    content = ("value = 2" if build_ok else "value =") if changed else "value = 1"
    replies = iter([
        action("write_file", path="app.py", content=content),
        *[action("read_file", path=f"context-{i}.txt") for i in range(ab._NO_WRITE_ABORT_AT)],
        action("build"), action("done") if handoff else action("build"),
    ])

    async def complete(*_args, **_kwargs):
        return next(replies, action("build"))

    async def snapshot():
        return {"app.py": source.read_text()}

    async def execute(tool):
        if tool.name == "write_file":
            source.write_text(tool.args["content"])
            return {"ok": True, "content": source.read_text()}
        if tool.name == "build":
            builds.append(source.read_text())
            try:
                compile(source.read_text(), str(source), "exec")
            except SyntaxError:
                return {"ok": False, "detail": "source syntax error"}
            return {"ok": True, "detail": "compiled current source"}
        return {"ok": True, "content": "context"}

    result = await ab.run_agent_build(
        system_prompt="sys", user_prompt="edit", model="test", complete=complete,
        execute=execute, snapshot_files=snapshot, max_steps=40,
        coordinator_handoff=True, ship_green_on_abort=False,
    )
    assert builds == ([content] if changed else [])
    assert result.done is (changed and build_ok and handoff)
    assert result.needs_finalization is result.done
    if not result.done:
        assert result.stop_reason == "exploring"
    assert result.steps < 40  # the one needed build cannot renew the read/paid-call budget


@pytest.mark.asyncio
async def test_skipped_discovery_does_not_poison_first_read_after_unrelated_source_progress():
    state = {"page.tsx": "v0", "target.ts": "needed"}
    executed_reads = []
    replies = iter([
        *[action("read_file", path=f"context-{i}.txt") for i in range(ab._NO_WRITE_NUDGE_AT)],
        action("read_file", path="target.ts"),  # discovery paused, never executed
        action("write_file", path="page.tsx", content="v1"),
        action("read_file", path="target.ts"),
        action("done"),
    ])

    async def complete(*_args, **_kwargs):
        return next(replies)

    async def snapshot():
        return dict(state)

    async def execute(tool):
        if tool.name == "write_file":
            state[tool.path] = tool.args["content"]
            return {"ok": True, "content": state[tool.path]}
        executed_reads.append(tool.path)
        return {"ok": True, "content": state.get(tool.path, "context")}

    result = await ab.run_agent_build(
        system_prompt="sys", user_prompt="edit", model="test", complete=complete,
        execute=execute, snapshot_files=snapshot, max_steps=12,
    )
    assert result.done
    assert executed_reads.count("target.ts") == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["build", "edit"])
async def test_coordinator_protocol_hands_off_without_asking_model_for_deferred_probes(mode):
    calls = 0
    tools = []

    async def complete(messages, *_args, **_kwargs):
        nonlocal calls
        calls += 1
        system = messages[0]["content"]
        assert "in MAX FINALIZATION HANDOFF after fresh clean FAST_CHECK" in system
        assert "server coordinator MUST still run full build, runtime" in system
        assert "done hands off a candidate, not an accepted product" in system
        if mode == "build":
            assert "Outside MAX FINALIZATION HANDOFF, for an INTERACTIVE feature" in system
            assert "mandatory full build, signed runtime and release checks" in system
            assert "applicable isolated local tests" in system
            assert "A/B owner isolation remains unverified" in system
        assert "runtime, isolation and release" not in system
        return action("build") if calls == 1 else action("done")

    async def execute(tool):
        tools.append(tool.name)
        return {"ok": True}

    result = await ab.run_agent_build(
        system_prompt=(ab.build_system_prompt("preserve platform") if mode == "build"
                       else ab.build_edit_system_prompt("preserve platform")), user_prompt="edit",
        model="test", complete=complete, execute=execute, max_steps=3,
        coordinator_handoff=True, require_green_before_done=True, ship_green_on_abort=False,
    )
    assert tools == ["build"]
    assert result.done and result.needs_finalization
