"""Real injected legacy loops: useful discovery survives without minting proof."""

from __future__ import annotations

import hashlib
import json
from types import SimpleNamespace

import pytest

from yleum_api.services import agent_builder as ab
from yleum_api.services.agent_discovery_context import (
    MAX_RETAINED_CONTEXT_CHARS,
    cached_information,
    diagnostic_read,
    new_information,
    retained_information,
)


def call(name, **args):
    return f'<omnia:action name="{name}">{json.dumps(args)}</omnia:action>'


def turns(name, body, **args):
    tool = ab.Action(name, args)
    obs = {"ok": True, "content": body}
    if name == "read_file":
        obs["read_source_sha256"] = hashlib.sha256(body.encode()).hexdigest()
    return [
        {"role": "assistant", "content": call(name, **args)},
        {"role": "user", "content": ab._format_observation(tool, obs)},
    ]


@pytest.mark.parametrize("tool", ["docs", "provider_docs", "read_file"])
def test_information_delivers_real_content_and_tail_beyond_old_6000(tool):
    content = "export type SDK = string;\n" + "x" * 9000 + "\nACTUAL_SDK_TAIL"
    result = ab._format_observation(
        ab.Action(tool, {"path": "sdk.ts"}),
        {
            "ok": True,
            "detail": "Found SDK documentation",
            "content": content,
        },
    )
    assert content in result
    assert "ACTUAL_SDK_TAIL" in result
    assert "Found SDK documentation" not in result


def test_window_preserves_initial_request_and_exact_sdk_after_recent_turns_leave():
    history = [{"role": "system", "content": "rules"}, {"role": "user", "content": "owner plan"}]
    history += turns("read_file", "export function buy(id: string): PaidReceipt;", path="sdk.ts")
    for i in range(10):
        history += [
            {"role": "assistant", "content": f"reasoning {i}"},
            {"role": "user", "content": f"event {i}"},
        ]
    original = json.dumps(history)
    window = ab._window_messages(history)
    assert window[:2] == history[:2]
    assert "export function buy(id: string): PaidReceipt;" in str(window)
    assert window[-12:] == history[-12:]
    assert json.dumps(history) == original


def test_current_hash_preserves_unchanged_sdk_after_build_and_rejects_side_effect_change():
    history = turns("read_file", "old SDK", path="sdk.ts") + turns("build", "compiled")
    old = retained_information(
        history, ab.parse_action, recent_messages=0, source_files={"sdk.ts": "old SDK"}
    )
    changed = retained_information(
        history, ab.parse_action, recent_messages=0, source_files={"sdk.ts": "new SDK"}
    )
    assert "old SDK" in old
    assert "old SDK" not in changed


def test_hashless_historical_read_is_not_a_current_source_witness():
    history = [
        {"role": "assistant", "content": call("read_file", path="sdk.ts")},
        {"role": "user", "content": "[observation: read_file sdk.ts OK]\nOLD_SIGNATURE"},
    ]
    source = {"sdk.ts": "NEW_SIGNATURE"}
    assert "OLD_SIGNATURE" not in retained_information(
        history,
        ab.parse_action,
        recent_messages=0,
        source_files=source,
    )
    assert (
        cached_information(
            ab.Action("read_file", {"path": "sdk.ts"}),
            history,
            ab.parse_action,
            source_files=source,
        )
        == ""
    )


@pytest.mark.asyncio
async def test_next_segment_checks_current_source_before_first_new_provider_call():
    order = []
    prior = turns("read_file", "OLD_SIGNATURE", path="sdk.ts")
    prior += turns("docs", "SDK_DOCUMENTATION", q="SDK")

    async def snapshot():
        order.append("snapshot")
        return {"sdk.ts": "NEW_SIGNATURE"}

    async def complete(messages, *_args, **_kwargs):
        order.append("provider")
        assert "OLD_SIGNATURE" not in str(messages)
        assert "SDK_DOCUMENTATION" in str(messages)
        assert messages[1]["content"] == "owner request"
        return call("done")

    async def execute(_tool):
        raise AssertionError("no tool required")

    await ab._run_agent_segment(
        system_prompt="rules",
        user_prompt="owner request",
        model="fixture",
        execute=execute,
        complete=complete,
        snapshot_files=snapshot,
        discovery_history=prior,
        max_steps=1,
    )
    assert order == ["snapshot", "provider"]


@pytest.mark.parametrize(
    "mutation,path", [("write_file", "./sdk.ts"), ("edit_file", "sdk.ts"), ("bash", "")]
)
def test_unknown_mutation_invalidates_source_aliases_but_not_external_docs(mutation, path):
    history = turns("read_file", "old signature", path="sdk.ts")
    history += turns("docs", "official SDK guide", q="sdk")
    history += turns(mutation, "changed", path=path)
    context = retained_information(history, ab.parse_action, recent_messages=0)
    assert "old signature" not in context
    assert "official SDK guide" in context


def test_failed_mutation_does_not_make_identical_read_new_information():
    read = ab.Action("read_file", {"path": "sdk.ts"})
    previous = turns("read_file", "same SDK", path="sdk.ts")
    previous += [
        {
            "role": "assistant",
            "content": call("edit_file", path="sdk.ts", search="absent", replace="x"),
        },
        {"role": "user", "content": "[observation: edit_file sdk.ts FAILED]\nexact edit mismatch"},
    ]
    assert not new_information(
        read, turns("read_file", "same SDK", path="sdk.ts")[1]["content"], previous, ab.parse_action
    )
    assert cached_information(read, previous, ab.parse_action) == ""


@pytest.mark.asyncio
async def test_sol_reads_current_failure_target_through_discovery_pause_then_reuses_the_copy():
    path = "src/lesson.ts"
    files = {path: "old lesson"}
    actions = [
        *[call("bash", cmd=f"inspect-tool-{i}") for i in range(4)],
        call("bash", cmd="pnpm test"),
        call("bash", cmd="ls -la"),
        call("bash", cmd=f"cat -- './{path}'"),
        call("read_file", path=path),
        call("bash", cmd=f"cat {path}"),
        call("edit_file", path=path, search="old lesson", replace="working lesson"),
        call("build"),
        call("done"),
    ]
    replies = iter(actions)
    executed, events = [], []

    async def complete(*args, **kwargs):
        return next(replies)

    async def snapshot():
        return dict(files)

    async def execute(action):
        executed.append((action.name, action.args))
        if action.name == "bash" and action.args["cmd"] == "pnpm test":
            return {"ok": False, "detail": f"/workspace/{path}:17: Expected 5 cards; got 0"}
        if action.name == "edit_file":
            files[path] = action.args["replace"]
        return {"ok": True, "content": files.get(action.path, "ok")}

    async def emit(name, data):
        if name == "agent.step":
            events.append(data)

    result = await ab._run_agent_segment(
        system_prompt="rules", user_prompt="Spanish cards", model="gpt-6.2-sol",
        complete=complete, execute=execute, snapshot_files=snapshot,
        coordinator_handoff=True, emit=emit, max_steps=len(actions), ship_green_on_abort=False,
    )
    assert result.done and files[path] == "working lesson"
    assert [args["cmd"] for tool, args in executed if tool == "bash"] == [
        *[f"inspect-tool-{i}" for i in range(4)], "pnpm test",
    ]
    assert [args for tool, args in executed if tool == "read_file"] == [{"path": path}]
    reasons = [data.get("tool_diagnostic", {}).get("reason_code") for data in events]
    assert reasons.count("discovery_pause") == 1
    assert reasons.count("cached_result") == reasons.count("repeat_guard") == 1
    assert any(
        "PREVIOUS EXECUTED RESULT" in msg["content"] and "old lesson" in msg["content"]
        for msg in result.transcript
    )


@pytest.mark.asyncio
async def test_repeated_diagnostic_reads_keep_existing_repeat_abort_with_one_file_read():
    replies = [call("bash", cmd="pnpm test")] + [call("bash", cmd="cat src/lesson.ts")] * 12
    used, reads = [], []

    async def complete(*args, **kwargs):
        reply = replies[len(used)]
        used.append(reply)
        return reply

    async def snapshot():
        return {"src/lesson.ts": "unchanged"}

    async def execute(tool):
        if tool.name == "bash":
            return {"ok": False, "detail": "src/lesson.ts:4: failed"}
        reads.append(tool.path)
        return {"ok": True, "content": "unchanged"}

    result = await ab._run_agent_segment(
        system_prompt="rules", user_prompt="fix the lesson", model="gpt-6.2-sol",
        coordinator_handoff=True, execute=execute, complete=complete, snapshot_files=snapshot,
        max_steps=len(replies), ship_green_on_abort=False,
    )
    assert reads == ["src/lesson.ts"]
    assert result.stop_reason == "looping" and not result.done
    assert len(used) == 7  # failure, first read, five repeats (last one ends the segment)


@pytest.mark.parametrize(("cmd", "error"), [
    ("cat src/lesson.ts", "Timed out while installing jsdom"),
    ("cat src/lesson.ts", "src/lesson.tsx:12 failed"),
    ("cat src/lesson.ts", "/outside/src/lesson.ts:12 failed"),
    ("cat src/lesson.ts", "Cannot fetch https://registry.example/check?file=src/lesson.ts"),
    ("cat ../src/lesson.ts", "../src/lesson.ts:12 failed"),
    ("cat .env", "error loading .env"),
    ("cat src/lesson.ts | head", "src/lesson.ts:12 failed"),
    ("cat src/lesson.ts; pnpm test", "src/lesson.ts:12 failed"),
])
def test_error_read_never_admits_other_files_or_shell_commands(cmd, error):
    history = [
        {"role": "assistant", "content": call("bash", cmd="pnpm test")},
        {"role": "user", "content": "[observation: bash FAILED]\n" + error},
    ]
    files = {"src/lesson.ts": "source", ".env": "", "../src/lesson.ts": ""}
    assert diagnostic_read(ab.Action("bash", {"cmd": cmd}), history, ab.parse_action, files) is None


def test_error_read_expires_after_successful_build_or_source_edit_and_supports_quoted_route():
    path = "src/app/lesson/[id]/page.tsx"
    files = {path: "owned"}
    action = ab.Action("bash", {"cmd": f"cat '{path}'"})
    failed = [
        {"role": "assistant", "content": call("done")},
        {"role": "user", "content": f"[observation: build FAILED]\n./{path}(8,4): error TS2304"},
    ]
    expected = ab.Action("read_file", {"path": path})
    assert diagnostic_read(action, failed, ab.parse_action, files) == expected
    for later in (turns("build", "clean"), turns("edit_file", "changed", path=path)):
        assert diagnostic_read(action, failed + later, ab.parse_action, files) is None


def test_memory_is_bounded_explicit_and_redacted():
    history = []
    for i in range(7):
        history += turns("docs", f"guide {i}: " + "x" * 15_000 + " sk-" + "s" * 32, q=str(i))
    result = retained_information(history, ab.parse_action, recent_messages=0)
    assert len(result) < MAX_RETAINED_CONTEXT_CHARS + 1000
    assert "older result(s)" in result and "targeted new read is permitted" in result
    assert "sk-" not in result


@pytest.mark.asyncio
async def test_eight_fresh_reads_allow_implementation_without_false_source_progress():
    state = {
        **{f"sdk{i}.ts": f"export type SDK{i} = string;" for i in range(8)},
        "page.tsx": "before",
    }
    replies = iter(
        [
            *[call("read_file", path=f"sdk{i}.ts") for i in range(8)],
            call("write_file", path="page.tsx", content="after"),
            call("build"),
            call("done"),
        ]
    )
    seen, executed, progress = [], [], []

    async def complete(messages, *_args, **_kwargs):
        seen.append(messages)
        return next(replies)

    async def execute(tool):
        executed.append(tool.name)
        if tool.name == "write_file":
            state[tool.path] = tool.args["content"]
        return {"ok": True, "content": state.get(tool.path, "compiled")}

    async def snapshot():
        return dict(state)

    async def checkpoint(result):
        progress.append(result)

    result = await ab.run_agent_build(
        system_prompt="rules",
        user_prompt="owner plan",
        model="fixture",
        execute=execute,
        complete=complete,
        snapshot_files=snapshot,
        coordinator_handoff=True,
        checkpoint=checkpoint,
        max_steps=11,
        ship_green_on_abort=False,
    )
    assert executed[:8] == ["read_file"] * 8
    assert result.done and state["page.tsx"] == "after"
    assert len(progress) == 1 and progress[0].files == {"page.tsx": "after"}
    assert "export type SDK0 = string;" in str(seen[-1])
    assert not any("discovery paused" in str(message) for message in seen)


@pytest.mark.asyncio
async def test_fresh_information_cannot_authorize_unchanged_edit_or_finalization():
    replies = iter(
        [*[call("read_file", path=f"sdk{i}.ts") for i in range(8)], call("build"), call("done")]
    )
    executed = []

    async def complete(*_args, **_kwargs):
        return next(replies, call("done"))

    async def execute(tool):
        executed.append(tool.name)
        return {"ok": True, "content": "information from " + tool.path}

    result = await ab.run_agent_build(
        system_prompt="rules",
        user_prompt="edit",
        model="fixture",
        execute=execute,
        complete=complete,
        edit_source_changed=lambda: False,
        coordinator_handoff=True,
        max_steps=25,
        ship_green_on_abort=False,
    )
    assert executed == ["read_file"] * 8
    assert not result.done and not result.needs_finalization
    assert result.stop_reason == "no_progress"


@pytest.mark.asyncio
async def test_blocked_duplicate_returns_real_previous_result_without_another_tool_call():
    state = {"sdk.ts": "export type ImportantSDK = string;", "page.tsx": "before"}
    replies = iter(
        [
            *[call("read_file", path="sdk.ts") for _ in range(3)],
            call("write_file", path="page.tsx", content="after"),
            call("build"),
            call("done"),
        ]
    )
    messages, reads = [], []

    async def complete(current, *_args, **_kwargs):
        messages.append(current)
        return next(replies)

    async def execute(tool):
        if tool.name == "read_file":
            reads.append(tool.path)
        if tool.name == "write_file":
            state[tool.path] = tool.args["content"]
        return {"ok": True, "content": state.get(tool.path, "compiled")}

    async def snapshot():
        return dict(state)

    result = await ab.run_agent_build(
        system_prompt="rules",
        user_prompt="build",
        model="fixture",
        execute=execute,
        complete=complete,
        snapshot_files=snapshot,
        coordinator_handoff=True,
        max_steps=6,
        ship_green_on_abort=False,
    )
    assert result.done and len(reads) == 2
    assert "PREVIOUS EXECUTED RESULT" in str(messages[3])
    assert "export type ImportantSDK = string;" in str(messages[3])


def test_prior_segment_discovery_survives_with_exact_original_task():
    prior = turns("docs", "exact useful SDK documentation", q="sdk")
    current = [
        {"role": "system", "content": "system"},
        {"role": "user", "content": "original complete plan"},
    ]
    window = ab._window_messages(current, discovery_history=prior)
    assert window[:2] == current
    assert "exact useful SDK documentation" in str(window)


def test_progress_notes_do_not_restrict_valid_design_work_to_compiler_errors():
    note = ab._progress_note({"page.tsx": "source"}, True)
    assert "unfinished requirement" in note and "design improvement" in note
    assert "not requested behavior" in note
    assert "Only touch one" not in note


@pytest.mark.asyncio
@pytest.mark.parametrize("bounded", [False, True])
async def test_only_ordinary_discovery_renews_the_exploration_allowance(bounded):
    frames = []
    state = {f"sdk{i}.ts": f"export type SDK{i} = string;" for i in range(4)}

    async def snapshot():
        return dict(state)

    async def complete(*_args, **_kwargs):
        return call("read_file", path=f"sdk{len(frames) - 1}.ts")

    async def provider(frame, invoke):
        frames.append(frame["value"])
        return await invoke()

    async def execute(tool):
        return {"ok": True, "content": state[tool.path]}

    result = await ab._run_agent_segment(
        system_prompt="rules", user_prompt="owner request", model="fixture",
        execute=execute, complete=complete, snapshot_files=snapshot,
        continuation=SimpleNamespace(restore_frame=lambda _kind: None, provider=provider),
        bounded_repair=bounded, max_steps=4, ship_green_on_abort=False,
    )
    assert [frame["no_write_streak"] for frame in frames] == (
        [0, 1, 2, 3] if bounded else [0, 0, 0, 0]
    )
    assert not result.done and not result.needs_finalization and not result.files


@pytest.mark.parametrize("source,args", [
    ("", {}), ("short\nsource\n", {}), ("x" * 17_000, {}),
    ("first\nsecond\nthird\n", {"start_line": 2, "end_line": 9}),
    ("first\n" + "x" * 17_000, {"start_line": 2}),
    ("", {"start_line": 1}),
])
def test_read_hash_matches_the_executors_exact_projection(source, args):
    from yleum_api.services.project_cell_executor import _read_file

    action = ab.Action("read_file", {"path": "sdk.ts", **args})
    files = {"sdk.ts": source}
    observation = _read_file(files, action.path, action.args)
    assert ab._read_observation_source_hash(action, observation, files, files) == hashlib.sha256(
        source.encode()
    ).hexdigest()
    assert ab._read_observation_source_hash(
        action, {**observation, "content": "stale replay"}, files, files
    ) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("changed_during_read", [False, True])
async def test_stale_read_replay_cannot_acquire_the_current_source_hash(changed_during_read):
    state = {"sdk.ts": "old source" if changed_during_read else "new source"}

    async def snapshot():
        return dict(state)

    async def complete(*_args, **_kwargs):
        return call("read_file", path="sdk.ts")

    async def execute(_tool):
        state["sdk.ts"] = "new source"
        return {"ok": True, "content": "old source", "read_source_sha256": "f" * 64}

    result = await ab._run_agent_segment(
        system_prompt="rules", user_prompt="owner request", model="fixture",
        execute=execute, complete=complete, snapshot_files=snapshot,
        max_steps=1, ship_green_on_abort=False,
    )
    observation = result.transcript[-1]["content"]
    assert "old source" in observation and "[source sha256:" not in observation
    assert "old source" not in retained_information(
        result.transcript, ab.parse_action, recent_messages=0, source_files=state
    )
