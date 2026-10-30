"""Unit tests for the agentic builder engine (services/agent_builder.py).

The loop is dependency-injected (`complete` + `execute`), so these run with
zero network/container — they exercise the protocol parser and the
plan→act→observe→verify control flow deterministically.

Run:  cd apps/api && uv run pytest tests/test_agent_builder.py -q
"""

from __future__ import annotations

import asyncio
import hashlib
from datetime import UTC, datetime, timedelta

import pytest

from yleum_api.services import agent_builder as ab


def test_read_observation_identifies_range_and_how_to_recover_truncated_source():
    action = ab.Action("read_file", {"path": "src/lib/omnia/integration-client.ts"})
    observation = ab._format_observation(action, {
        "ok": True,
        "content": "x" * (ab._MAX_OBS_CHARS + 40),
        "read_range": {"start_line": 1, "end_line": 450, "total_lines": 450, "truncated": False},
    })
    assert "lines 1-450 of 450" in observation
    assert "start_line" in observation and "end_line" in observation
    assert "truncated" in observation
    assert len(observation) < ab._MAX_OBS_CHARS + 500


def test_ranged_read_observation_preserves_complete_sdk_signature():
    signature = (
        "export async function updateMaxAction(id: string, input: Input, expectedRevision: number)"
    )
    action = ab.Action("read_file", {
        "path": "src/lib/omnia/integration-client.ts", "start_line": 340, "end_line": 360,
    })
    observation = ab._format_observation(action, {
        "ok": True, "content": signature,
        "read_range": {"start_line": 340, "end_line": 360, "total_lines": 450, "truncated": False},
    })
    assert signature in observation
    assert "lines 340-360 of 450" in observation


@pytest.mark.parametrize('opening,closing', [
    ('invoke', 'invoke'), ('action', 'action'), ('action', 'omnia:action'),
])
def test_parse_known_alternate_action_tags(opening, closing):
    result = ab.parse_action(
        f'<{opening} name="read_file">{{"path":"src/app/page.tsx"}}</{closing}>'
    )
    assert result is not None
    assert result.name == 'read_file' and result.path == 'src/app/page.tsx'


def test_non_object_arguments_cannot_turn_into_empty_write():
    assert ab.parse_action('<omnia:action name="write_file">[]</omnia:action>') is None


def test_changed_candidate_cannot_ship_on_a_stale_green_build():
    record = []
    result = asyncio.run(ab.run_agent_build(
        system_prompt='sys', user_prompt='build', model='m',
        execute=_ok_executor(record), complete=_scripted([
            '<omnia:action name="write_file">{"path":"a.ts","content":"one"}</omnia:action>',
            '<omnia:action name="build">{}</omnia:action>',
            '<omnia:action name="write_file">{"path":"a.ts","content":"two"}</omnia:action>',
        ]), max_steps=3,
    ))
    assert result.done is False
    assert result.files == {'a.ts': 'two'}


def test_repeated_done_cannot_bypass_a_failed_required_build():
    async def red(action):
        return {'ok': False, 'detail': 'Cannot find module CrudResource'}

    result = asyncio.run(ab.run_agent_build(
        system_prompt='sys', user_prompt='build', model='m', execute=red,
        complete=_scripted([
            '<omnia:action name="build">{}</omnia:action>',
            '<omnia:action name="done">{"summary":"all done"}</omnia:action>',
        ]), max_steps=5, require_green_before_done=True,
    ))
    assert result.done is False

# ── parse_action ────────────────────────────────────────────────────────────


def test_parse_bare_json():
    a = ab.parse_action(
        'thinking...\n<omnia:action name="read_file">{"path": "src/app/page.tsx"}</omnia:action>'
    )
    assert a is not None and a.name == "read_file" and a.path == "src/app/page.tsx"


def test_parse_fenced_json():
    reply = (
        '<omnia:action name="write_file">\n```json\n'
        '{"path":"a.ts","content":"x"}\n```\n</omnia:action>'
    )
    a = ab.parse_action(reply)
    assert a is not None and a.name == "write_file" and a.args["content"] == "x"


def test_parse_done_loose_body():
    a = ab.parse_action('<omnia:action name="done">all built</omnia:action>')
    assert a is not None and a.name == "done" and "all built" in a.args["summary"]


def test_parse_build_empty():
    a = ab.parse_action('<omnia:action name="build"></omnia:action>')
    assert a is not None and a.name == "build" and a.args == {}


def test_docs_action_known_and_parses():
    # The Context7 `docs` tool is advertised in LOOP_PROTOCOL and handled by the
    # executor; it MUST be in the allowlist or parse_action rejects every call as a
    # stall (the regression fixed 2026-06-29). Opus relies on it for current APIs.
    assert "docs" in ab._KNOWN_ACTIONS
    a = ab.parse_action(
        '<omnia:action name="docs">{"library":"drizzle-orm","query":"select where"}</omnia:action>'
    )
    assert a is not None and a.name == "docs"
    assert a.args.get("library") == "drizzle-orm"


def test_probe_action_parses():
    # The `probe` E2E tool (authenticated real request) must parse with
    # method + path + body.
    a = ab.parse_action(
        '<omnia:action name="probe">{"method":"POST","path":"/api/realtime/x",'
        '"body":{"data":{"body":"hi"}}}</omnia:action>'
    )
    assert a is not None and a.name == "probe" and a.path == "/api/realtime/x"
    assert a.args.get("method") == "POST"
    assert "probe" in ab._VERIFY_ACTIONS  # exempt from the global repeat guard


def test_protocol_actions_all_in_allowlist():
    # Contract: every action documented in LOOP_PROTOCOL is in _KNOWN_ACTIONS, so a
    # newly-added tool can never silently ship dead like `docs` did.
    import re

    advertised = set(re.findall(r"^- (\w+)\s", ab.LOOP_PROTOCOL, re.MULTILINE))
    assert advertised, "no actions parsed from LOOP_PROTOCOL"
    missing = advertised - ab._KNOWN_ACTIONS
    assert not missing, f"advertised but not in allowlist: {missing}"


def test_parse_takes_last_action():
    reply = (
        '<omnia:action name="read_file">{"path":"a"}</omnia:action>\n'
        'then\n<omnia:action name="build"></omnia:action>'
    )
    a = ab.parse_action(reply)
    assert a is not None and a.name == "build"


def test_parse_none_and_unknown():
    assert ab.parse_action("just prose, no action") is None
    assert ab.parse_action('<omnia:action name="rm_rf">{}</omnia:action>') is None
    assert ab.parse_action('<omnia:action name="read_file">{bad json</omnia:action>') is None


# ── run_agent_build loop ─────────────────────────────────────────────────────


def _scripted(replies):
    """A fake `complete` that returns each scripted reply in turn."""
    box = {"i": 0}

    async def _complete(convo, model, **kw):
        i = box["i"]
        box["i"] += 1
        return replies[i] if i < len(replies) else replies[-1]

    return _complete


def _ok_executor(record, state=None):
    if state is None:
        state = {}

    async def _execute(action: ab.Action):
        record.append((action.name, action.path))
        if action.name in ("write_file", "edit_file"):
            before = state.get(action.path, action.args.get("search"))
            content = (
                action.args.get("content", "x")
                if action.name == "write_file"
                else (before or "").replace(
                    action.args.get("search", ""), action.args.get("replace", ""), 1
                )
            )
            state[action.path] = content
            return {
                "ok": True,
                "content": content,
                "content_change": {
                    "path": action.path,
                    "before_sha256": hashlib.sha256(before.encode()).hexdigest()
                    if before is not None
                    else None,
                    "after_sha256": hashlib.sha256(content.encode()).hexdigest(),
                },
            }
        return {"ok": True, "detail": "ok"}

    return _execute


def test_loop_happy_path_reaches_done():
    record: list = []
    replies = [
        '<omnia:action name="write_file">{"path":"src/app/page.tsx",'
        '"content":"export default function P(){return null}"}</omnia:action>',
        '<omnia:action name="build"></omnia:action>',
        '<omnia:action name="done">{"summary":"built the page"}</omnia:action>',
    ]
    res = asyncio.run(
        ab.run_agent_build(
            system_prompt="sys",
            user_prompt="build it",
            model="m",
            execute=_ok_executor(record),
            complete=_scripted(replies),
            max_steps=8,
        )
    )
    assert res.done is True
    assert res.stop_reason == "done"
    assert "src/app/page.tsx" in res.files
    assert ("build", "") in record
    assert res.steps == 3


def test_loop_hits_step_budget():
    record: list = []
    # always asks to read — never done
    replies = ['<omnia:action name="read_file">{"path":"a.ts"}</omnia:action>']
    res = asyncio.run(
        ab.run_agent_build(
            system_prompt="sys",
            user_prompt="x",
            model="m",
            execute=_ok_executor(record),
            complete=_scripted(replies),
            max_steps=4,
        )
    )
    assert res.done is False
    assert res.stop_reason == "max_steps"
    assert res.steps == 4


def test_loop_stalls_on_no_action():
    replies = ["I cannot do that.", "Still no action block here."]
    res = asyncio.run(
        ab.run_agent_build(
            system_prompt="sys",
            user_prompt="x",
            model="m",
            execute=_ok_executor([]),
            complete=_scripted(replies),
            max_steps=8,
        )
    )
    assert res.done is False
    assert res.stop_reason == "stalled"


def test_loop_unknown_gateway_error_stops_without_replay_or_private_details(monkeypatch):
    calls = []

    async def _boom(convo, model, **kw):
        calls.append(1)
        raise RuntimeError("gateway 502 synthetic-private-key")

    async def _no_sleep(delay):
        pytest.fail("Unknown provider outcomes must not be retried")

    monkeypatch.setattr(ab.asyncio, "sleep", _no_sleep)

    res = asyncio.run(
        ab.run_agent_build(
            system_prompt="sys",
            user_prompt="x",
            model="m",
            execute=_ok_executor([]),
            complete=_boom,
            max_steps=4,
        )
    )
    assert res.done is False
    assert res.needs_finalization is False
    assert res.stop_reason == "provider_error"
    assert calls == [1]
    assert "LLM" in res.summary
    assert "synthetic-private-key" not in res.summary


def test_infra_failure_has_non_retryable_stop_reason():
    calls: list[str] = []
    replies = [
        f'<omnia:action name="read_file">{{"path":"file-{i}.ts"}}</omnia:action>'
        for i in range(ab._INFRA_DEAD_ABORT_AT)
    ]

    async def _infra_dead(action: ab.Action):
        calls.append(action.path)
        return {
            "ok": False,
            "error": "infra: container not found",
            "infra_dead": True,
        }

    res = asyncio.run(
        ab.run_agent_build(
            system_prompt="sys",
            user_prompt="build it",
            model="m",
            execute=_infra_dead,
            complete=_scripted(replies),
            max_steps=20,
        )
    )

    assert res.done is False
    assert res.stop_reason == "infra_error"
    assert len(calls) == ab._INFRA_DEAD_ABORT_AT


def test_window_messages_caps_payload():
    convo = [{"role": "system", "content": "s"}, {"role": "user", "content": "task"}]
    for i in range(20):
        convo.append({"role": "assistant", "content": f"a{i}"})
        convo.append({"role": "user", "content": f"o{i}"})
    w = ab._window_messages(convo, keep_last=8)
    assert len(w) == 10  # 2 head + 8 last
    assert w[0]["content"] == "s" and w[1]["content"] == "task"
    assert w[-1]["content"] == "o19"
    # small convo passes through untouched
    small = convo[:5]
    assert ab._window_messages(small, keep_last=8) == small


def test_loop_breaks_on_repeated_action():
    # Model stuck re-issuing the same grep → circuit breaker stops it.
    replies = ['<omnia:action name="grep">{"pattern":"x","path":"src"}</omnia:action>']
    res = asyncio.run(
        ab.run_agent_build(
            system_prompt="s",
            user_prompt="x",
            model="m",
            execute=_ok_executor([]),
            complete=_scripted(replies),
            max_steps=30,
        )
    )
    assert res.stop_reason == "looping"
    assert res.steps < 30


def test_failed_write_not_tracked():
    async def _fail_exec(action: ab.Action):
        if action.name == "write_file":
            return {"ok": False, "error": "disk full"}
        return {"ok": True, "detail": "ok"}

    replies = [
        '<omnia:action name="write_file">{"path":"a.ts","content":"x"}</omnia:action>',
        '<omnia:action name="done">{"summary":"done"}</omnia:action>',
    ]
    res = asyncio.run(
        ab.run_agent_build(
            system_prompt="sys",
            user_prompt="x",
            model="m",
            execute=_fail_exec,
            complete=_scripted(replies),
            max_steps=6,
        )
    )
    # write failed → file must NOT be in the committed set
    assert "a.ts" not in res.files
    assert res.done is True


# ── runtime-sight tools: read_logs + runtime_check ──────────────────────────


def test_parse_new_observe_actions():
    a = ab.parse_action(
        'check it\n<omnia:action name="runtime_check">{"path":"/dashboard"}</omnia:action>'
    )
    assert a is not None and a.name == "runtime_check" and a.path == "/dashboard"
    b = ab.parse_action('<omnia:action name="read_logs">{}</omnia:action>')
    assert b is not None and b.name == "read_logs"


def test_runtime_debug_loop_recovers_then_done():
    """build clean → runtime_check 5xx → read_logs → fix → re-check ok → done.

    Proves the new tools close the compiles-vs-renders gap: the agent SEES the
    runtime failure, fixes it, re-verifies, and only then finishes.
    """
    record: list = []
    state = {"crashed": True}  # the app 5xx until the edit "fixes" it

    async def _exec(action: ab.Action):
        record.append((action.name, action.path))
        if action.name == "build":
            return {"ok": True, "detail": "typecheck clean"}
        if action.name == "read_logs":
            return {"ok": True, "detail": "TypeError: x is not a function at page.tsx:12"}
        if action.name == "runtime_check":
            return (
                {"ok": False, "detail": "route / FAILED (HTTP 500)"}
                if state["crashed"]
                else {"ok": True, "detail": "route / renders OK"}
            )
        if action.name == "edit_file":
            state["crashed"] = False  # only the FIX (the edit) clears the crash
            return {"ok": True, "content": action.args.get("replace", "patched")}
        if action.name == "write_file":
            return {"ok": True, "content": action.args.get("content", "v1")}
        return {"ok": True, "detail": "ok"}

    replies = [
        '<omnia:action name="write_file">{"path":"src/app/page.tsx","content":"v1"}</omnia:action>',
        '<omnia:action name="build"></omnia:action>',
        '<omnia:action name="runtime_check">{"path":"/"}</omnia:action>',
        '<omnia:action name="read_logs">{}</omnia:action>',
        '<omnia:action name="edit_file">{"path":"src/app/page.tsx","search":"v1",'
        '"replace":"v2"}</omnia:action>',
        '<omnia:action name="runtime_check">{"path":"/"}</omnia:action>',
        '<omnia:action name="done">{"summary":"fixed runtime crash"}</omnia:action>',
    ]
    res = asyncio.run(
        ab.run_agent_build(
            system_prompt="s",
            user_prompt="x",
            model="m",
            execute=_exec,
            complete=_scripted(replies),
            max_steps=20,
        )
    )
    assert res.done is True and res.stop_reason == "done"
    # The SAME runtime_check ran twice (non-consecutive) WITHOUT a false
    # looping abort.
    assert sum(1 for n, _ in record if n == "runtime_check") == 2
    assert ("read_logs", "") in record


def test_verify_actions_exempt_from_global_repeat_guard():
    """A real build→fix loop runs `build` many non-consecutive times. Before the
    exemption that tripped the global repeat guard (_REPEAT_ABORT_AT=4) and
    aborted as "looping". Now it must run to done."""
    record: list = []
    # 5 edit→build cycles (5 identical `build`s, edits differ) then done.
    replies = []
    for i in range(5):
        replies.append(
            f'<omnia:action name="edit_file">{{"path":"a.ts","search":"v{i}",'
            f'"replace":"v{i + 1}"}}</omnia:action>'
        )
        replies.append('<omnia:action name="build"></omnia:action>')
    replies.append('<omnia:action name="done">{"summary":"clean"}</omnia:action>')
    res = asyncio.run(
        ab.run_agent_build(
            system_prompt="s",
            user_prompt="x",
            model="m",
            execute=_ok_executor(record),
            complete=_scripted(replies),
            max_steps=30,
        )
    )
    assert res.stop_reason == "done"
    assert record.count(("build", "")) == 5  # every build actually executed


def test_consecutive_build_spam_still_caught():
    """The exemption must NOT defang the consecutive-repeat guard: a model that
    emits `build` back-to-back with NOTHING to ship is still stopped as looping.
    The build must be RED here — a clean build is legitimately shipped by the
    ship-green-on-repeat rescue (see test_consecutive_green_build_ships), so the
    'looping' stop only applies when the repeated build keeps FAILING."""

    async def _red_build(action):
        if action.name == "build":
            return {"ok": False, "detail": "TS2304: Cannot find name 'x'"}
        return {"ok": True, "detail": "ok"}

    replies = ['<omnia:action name="build"></omnia:action>']
    res = asyncio.run(
        ab.run_agent_build(
            system_prompt="s",
            user_prompt="x",
            model="m",
            execute=_red_build,
            complete=_scripted(replies),
            max_steps=30,
        )
    )
    assert res.stop_reason == "looping"
    assert res.steps < 30


def test_consecutive_green_build_ships():
    """A model that repeats `build` on an already-CLEAN build should NOT be killed
    as looping — the ship-green-on-repeat rescue finishes it (don't loop forever on
    a working app)."""
    replies = ['<omnia:action name="build"></omnia:action>']
    res = asyncio.run(
        ab.run_agent_build(
            system_prompt="s",
            user_prompt="x",
            model="m",
            execute=_ok_executor([]),
            complete=_scripted(replies),
            max_steps=30,
        )
    )
    assert res.stop_reason == "done_on_green"
    assert res.steps < 30


def test_agent_tracks_package_manager_generated_lockfile() -> None:
    async def execute(action: ab.Action):
        return {
            "ok": True,
            "content": str(action.args.get("content") or ""),
            "files": {"pnpm-lock.yaml": "lockfileVersion: '9.0'\n"},
        }

    result = asyncio.run(
        ab.run_agent_build(
            system_prompt="s",
            user_prompt="x",
            model="m",
            execute=execute,
            complete=_scripted(
                [
                    '<omnia:action name="write_file">'
                    '{"path":"package.json","content":"{}"}</omnia:action>',
                    '<omnia:action name="done">{"summary":"done"}</omnia:action>',
                ]
            ),
            max_steps=4,
        )
    )

    assert result.files["package.json"] == "{}"
    assert result.files["pnpm-lock.yaml"] == "lockfileVersion: '9.0'\n"


# ── removed visual action and functional verification ────────────────────────


def test_parse_rejects_removed_see_action():
    a = ab.parse_action('look\n<omnia:action name="see">{"path":"/dashboard"}</omnia:action>')
    assert a is None


def test_runtime_check_loop_fixes_then_done():
    """Repeated functional checks can repair a route without a false loop abort."""
    record: list = []
    state = {"broken": True}

    async def _exec(action: ab.Action):
        record.append((action.name, action.path))
        if action.name == "build":
            return {"ok": True, "detail": "typecheck clean"}
        if action.name == "runtime_check":
            return (
                {"ok": False, "detail": "route failed: HTTP 500"}
                if state["broken"]
                else {"ok": True, "detail": "route renders: HTTP 200"}
            )
        if action.name == "edit_file":
            state["broken"] = False
            return {"ok": True, "content": action.args.get("replace", "x")}
        return {"ok": True, "detail": "ok"}

    replies = [
        '<omnia:action name="write_file">{"path":"src/app/page.tsx","content":"v1"}</omnia:action>',
        '<omnia:action name="build"></omnia:action>',
        '<omnia:action name="runtime_check">{"path":"/"}</omnia:action>',
        '<omnia:action name="edit_file">{"path":"src/app/page.tsx","search":"v1",'
        '"replace":"v2"}</omnia:action>',
        '<omnia:action name="runtime_check">{"path":"/"}</omnia:action>',
        '<omnia:action name="done">{"summary":"fixed runtime error"}</omnia:action>',
    ]
    res = asyncio.run(
        ab.run_agent_build(
            system_prompt="s",
            user_prompt="x",
            model="m",
            execute=_exec,
            complete=_scripted(replies),
            max_steps=20,
        )
    )
    assert res.done is True and res.stop_reason == "done"
    assert sum(1 for n, _ in record if n == "runtime_check") == 2


# ── green-gate: require_green_before_done ────────────────────────────────────


def test_green_gate_off_by_default_allows_immediate_done():
    """Default (flag off): a `done` is honoured immediately — current behaviour,
    byte-identical to pre-Phase-2."""
    res = asyncio.run(
        ab.run_agent_build(
            system_prompt="s",
            user_prompt="x",
            model="m",
            execute=_ok_executor([]),
            complete=_scripted(['<omnia:action name="done">{"summary":"x"}</omnia:action>']),
            max_steps=8,
        )
    )
    assert res.done is True and res.stop_reason == "done" and res.steps == 1


def test_green_gate_honors_done_when_verified():
    """flag on: write → build(clean) → runtime_check(ok) → done is honoured with
    ZERO rejections (the app was actually verified)."""
    record: list = []
    replies = [
        '<omnia:action name="write_file">{"path":"src/app/page.tsx","content":"v1"}</omnia:action>',
        '<omnia:action name="build"></omnia:action>',
        '<omnia:action name="runtime_check">{"path":"/"}</omnia:action>',
        '<omnia:action name="done">{"summary":"verified"}</omnia:action>',
    ]
    res = asyncio.run(
        ab.run_agent_build(
            system_prompt="s",
            user_prompt="x",
            model="m",
            execute=_ok_executor(record),
            complete=_scripted(replies),
            max_steps=12,
            require_green_before_done=True,
        )
    )
    assert res.done is True and res.stop_reason == "done"
    assert ("build", "") in record and ("runtime_check", "/") in record
    assert res.steps == 4


def test_green_gate_rejects_premature_done():
    """flag on: a `done` before any build/runtime_check is REJECTED — the loop
    continues, the model is forced to build + runtime_check, THEN done."""
    record: list = []
    replies = [
        '<omnia:action name="done">{"summary":"too early"}</omnia:action>',
        '<omnia:action name="build"></omnia:action>',
        '<omnia:action name="runtime_check">{"path":"/"}</omnia:action>',
        '<omnia:action name="done">{"summary":"now verified"}</omnia:action>',
    ]
    res = asyncio.run(
        ab.run_agent_build(
            system_prompt="s",
            user_prompt="x",
            model="m",
            execute=_ok_executor(record),
            complete=_scripted(replies),
            max_steps=12,
            require_green_before_done=True,
        )
    )
    # The step-0 `done` was rejected (else it would have ended at steps==1).
    assert res.done is True and res.steps == 4
    assert ("build", "") in record and ("runtime_check", "/") in record


def test_unverified_done_remains_incomplete_with_a_bounded_budget():
    """Repeated claims cannot replace proof, and still cannot hang forever."""
    replies = ['<omnia:action name="done">{"summary":"insist"}</omnia:action>']
    res = asyncio.run(
        ab.run_agent_build(
            system_prompt="s",
            user_prompt="x",
            model="m",
            execute=_ok_executor([]),
            complete=_scripted(replies),
            max_steps=12,
            require_green_before_done=True,
        )
    )
    assert res.done is False and res.stop_reason == "max_steps"
    assert res.steps == 12


def test_progressing_candidate_continues_in_same_executor_after_step_budget():
    record = []
    saved = []

    async def checkpoint(result):
        saved.append(dict(result.files))

    result = asyncio.run(ab.run_agent_build(
        system_prompt='sys', user_prompt='build', model='m',
        execute=_ok_executor(record), complete=_scripted([
            '<omnia:action name="write_file">{"path":"a.ts","content":"one"}</omnia:action>',
            '<omnia:action name="build">{}</omnia:action>',
            '<omnia:action name="write_file">{"path":"a.ts","content":"two"}</omnia:action>',
            '<omnia:action name="build">{}</omnia:action>',
            '<omnia:action name="done">{"summary":"done"}</omnia:action>',
        ]), max_steps=3, max_segments=2, coordinator_handoff=True,
        checkpoint=checkpoint,
    ))
    assert result.done is True and result.needs_finalization is True
    assert result.files == {'a.ts': 'two'}
    assert result.segments == 2 and result.steps == 5
    assert saved == [{'a.ts': 'one'}, {'a.ts': 'two'}]
    assert record == [('write_file', 'a.ts'), ('build', ''), ('write_file', 'a.ts'), ('build', '')]


def test_no_progress_segment_does_not_buy_more_model_calls():
    calls = []

    async def read_only(convo, model, **kwargs):
        calls.append(1)
        return '<omnia:action name="read_file">{"path":"a.ts"}</omnia:action>'

    result = asyncio.run(ab.run_agent_build(
        system_prompt='sys', user_prompt='build', model='m',
        execute=_ok_executor([]), complete=read_only,
        max_steps=2, max_segments=4,
    ))
    assert result.done is False and result.stop_reason == 'no_progress'
    assert len(calls) == 2 and result.segments == 1


def test_provider_failure_preserves_checkpoint_without_another_segment():
    calls = []
    saved = []

    async def complete(convo, model, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            return (
                '<omnia:action name="write_file">'
                '{"path":"a.ts","content":"draft"}</omnia:action>'
            )
        raise RuntimeError('unknown outcome')

    async def checkpoint(result):
        saved.append(dict(result.files))

    result = asyncio.run(ab.run_agent_build(
        system_prompt='sys', user_prompt='build', model='m',
        execute=_ok_executor([]), complete=complete,
        max_steps=3, max_segments=4, checkpoint=checkpoint,
    ))
    assert result.stop_reason == 'provider_error' and result.done is False
    assert saved == [{'a.ts': 'draft'}] and len(calls) == 2


def test_failed_write_cannot_authorize_a_continuation():
    calls = []

    async def complete(convo, model, **kwargs):
        calls.append(1)
        return '<omnia:action name="write_file">{"path":"a.ts","content":"draft"}</omnia:action>'

    async def rejected(action):
        return {'ok': False, 'environment_mutated': False, 'error': 'locked file'}

    result = asyncio.run(ab.run_agent_build(
        system_prompt='sys', user_prompt='build', model='m', execute=rejected,
        complete=complete, max_steps=2, max_segments=4,
    ))
    assert result.done is False and result.segments == 1 and len(calls) == 2


def test_identical_write_receipt_does_not_buy_another_segment_without_snapshots():
    calls = []
    digest = hashlib.sha256(b'same').hexdigest()

    async def complete(*args, **kwargs):
        calls.append(1)
        return '<omnia:action name="write_file">{"path":"a.ts","content":"same"}</omnia:action>'

    async def unchanged(action):
        return {'ok': True, 'content': 'same', 'content_change': {
            'path': 'a.ts', 'before_sha256': digest, 'after_sha256': digest,
        }}

    result = asyncio.run(ab.run_agent_build(
        system_prompt='sys', user_prompt='build', model='m', execute=unchanged,
        complete=complete, max_steps=1, max_segments=4,
    ))
    assert len(calls) == 1 and result.segments == 1 and not result.done


def test_draft_is_saved_before_handoff_and_the_next_model_call():
    saved = []
    observed_saved = []
    replies = iter([
        '<omnia:action name="write_file">{"path":"a.ts","content":"draft"}</omnia:action>',
        '<omnia:action name="build">{}</omnia:action>',
        '<omnia:action name="done">{"summary":"done"}</omnia:action>',
    ])

    async def complete(*args, **kwargs):
        observed_saved.append(bool(saved))
        return next(replies)

    async def checkpoint(result):
        saved.append(dict(result.files))

    result = asyncio.run(ab.run_agent_build(
        system_prompt='sys', user_prompt='build', model='m', execute=_ok_executor([]),
        complete=complete, max_steps=3, checkpoint=checkpoint, coordinator_handoff=True,
    ))
    assert result.needs_finalization and result.done
    assert observed_saved == [False, True, True] and saved == [{'a.ts': 'draft'}]


def test_checkpoint_fault_returns_recoverable_candidate_instead_of_throwing():
    async def broken(result):
        raise RuntimeError('private storage detail')

    result = asyncio.run(ab.run_agent_build(
        system_prompt='sys', user_prompt='build', model='m', execute=_ok_executor([]),
        complete=_scripted([
            '<omnia:action name="write_file">{"path":"a.ts","content":"draft"}</omnia:action>',
        ]), max_steps=1, max_segments=2, checkpoint=broken,
    ))
    assert not result.done and result.stop_reason == 'checkpoint_error'
    assert result.files == {'a.ts': 'draft'}
    assert 'private storage detail' not in result.summary


def test_expired_source_deadline_does_not_start_a_provider_call():
    async def forbidden(*args, **kwargs):
        pytest.fail('No model call is allowed after the source deadline')

    result = asyncio.run(ab.run_agent_build(
        system_prompt='sys', user_prompt='build', model='m', execute=_ok_executor([]),
        complete=forbidden, max_steps=2, max_segments=4,
        edit_deadline=datetime.now(UTC) - timedelta(seconds=1),
    ))
    assert not result.done and result.stop_reason == 'source_deadline'


# ── K1 knowledge layer: skills injection ─────────────────────────────────────

def test_coordinator_handoff_does_not_require_the_runtime_it_owns():
    async def execute(action):
        if action.name == "runtime_check":
            return {"ok": False, "deferred_to": "max_finalization"}
        return {"ok": True, "content": "v1"}

    result = asyncio.run(ab.run_agent_build(
        system_prompt="s", user_prompt="x", model="m", execute=execute,
        complete=_scripted([
            '<omnia:action name="write_file">{"path":"page.tsx","content":"v1"}</omnia:action>',
            '<omnia:action name="build">{}</omnia:action>',
            '<omnia:action name="runtime_check">{"path":"/"}</omnia:action>',
            '<omnia:action name="done">{"summary":"candidate"}</omnia:action>',
        ]), max_steps=4, require_green_before_done=True, coordinator_handoff=True,
    ))
    assert result.done and result.needs_finalization and result.steps == 4


@pytest.mark.parametrize("feedback", ["management_gap", "exception", "invalid_type"])
def test_source_completion_failure_cannot_be_rescued_by_green_abort(feedback):
    events = []
    state = {}

    async def snapshot():
        return dict(state)

    async def emit(event, payload):
        events.append((event, payload))

    def check(_files, _evidence):
        if feedback == "exception":
            raise RuntimeError("PRIVATE_SENTINEL must not enter observations")
        if feedback == "invalid_type":
            return False
        return "Connect useYleumAppProfile to a rendered app name/settings field."

    result = asyncio.run(ab.run_agent_build(
        system_prompt="s", user_prompt="x", model="m", execute=_ok_executor([], state),
        snapshot_files=snapshot,
        complete=_scripted([
            '<omnia:action name="write_file">'
            '{"path":"src/app/page.tsx","content":"thin"}</omnia:action>',
            '<omnia:action name="build">{}</omnia:action>',
            '<omnia:action name="done">{"summary":"premature"}</omnia:action>',
        ]), max_steps=3, coordinator_handoff=True, completion_check=check, emit=emit,
        ship_green_on_abort=True,
    ))

    assert not result.done and not result.needs_finalization
    assert result.stop_reason == "max_steps"
    done_steps = [payload for event, payload in events
                  if event == "agent.step" and payload.get("action") == "done"]
    assert len(done_steps) == 1 and done_steps[0]["ok"] is False
    assert done_steps[0]["detail"]
    assert "PRIVATE_SENTINEL" not in str(events) + str(result.transcript)


def test_source_completion_callback_waits_for_fresh_green_build():
    checked = []
    state = {}

    async def snapshot():
        return dict(state)

    def check(files, _evidence):
        checked.append(dict(files))
        return None

    result = asyncio.run(ab.run_agent_build(
        system_prompt="s", user_prompt="x", model="m", execute=_ok_executor([], state),
        snapshot_files=snapshot,
        complete=_scripted([
            '<omnia:action name="done">{"summary":"no build"}</omnia:action>',
            '<omnia:action name="build">{}</omnia:action>',
            '<omnia:action name="write_file">'
            '{"path":"src/app/page.tsx","content":"changed"}</omnia:action>',
            '<omnia:action name="done">{"summary":"stale build"}</omnia:action>',
            '<omnia:action name="build">{}</omnia:action>',
            '<omnia:action name="done">{"summary":"checked"}</omnia:action>',
        ]), max_steps=6, coordinator_handoff=True, completion_check=check,
    ))

    assert result.done and result.needs_finalization and result.summary == "checked"
    assert checked == [{"src/app/page.tsx": "changed"}]


def test_source_completion_cancellation_propagates_without_handoff():
    async def snapshot():
        return {}

    def check(_files, _evidence):
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(ab.run_agent_build(
            system_prompt="s", user_prompt="x", model="m", execute=_ok_executor([]),
            complete=_scripted([
                '<omnia:action name="build">{}</omnia:action>',
                '<omnia:action name="done">{"summary":"cancelled"}</omnia:action>',
            ]), max_steps=2, coordinator_handoff=True, completion_check=check,
            snapshot_files=snapshot,
        ))


@pytest.mark.parametrize(("before", "after", "accepted"), [
    ("thin", "complete", True), ("complete", "thin", False),
])
@pytest.mark.parametrize("call_done", [True, False])
def test_source_completion_uses_post_build_inventory(before, after, accepted, call_done):
    state = {}
    checked = []
    snapshots = 0

    async def snapshot():
        nonlocal snapshots
        snapshots += 1
        return dict(state)

    async def execute(action):
        if action.name == "write_file":
            state[action.path] = action.args["content"]
            return {"ok": True, "content": state[action.path]}
        state["src/app/page.tsx"] = after
        return {"ok": True, "detail": "clean", "environment_mutated": True}

    def check(files, _evidence):
        checked.append(dict(files))
        return None if files.get("src/app/page.tsx") == "complete" else "source gap"

    replies = [
        '<omnia:action name="write_file">'
        f'{{"path":"src/app/page.tsx","content":"{before}"}}</omnia:action>',
        '<omnia:action name="build">{}</omnia:action>',
    ]
    if call_done:
        replies.append('<omnia:action name="done">{"summary":"candidate"}</omnia:action>')
    result = asyncio.run(ab.run_agent_build(
        system_prompt="s", user_prompt="x", model="m", execute=execute,
        complete=_scripted(replies), snapshot_files=snapshot, completion_check=check,
        coordinator_handoff=True, ship_green_on_abort=True, max_steps=len(replies),
    ))

    assert result.done is accepted and result.needs_finalization is accepted
    assert checked and all(files == {"src/app/page.tsx": after} for files in checked)
    assert snapshots == 4  # Reuse the existing before/after reads of write + build.


@pytest.mark.parametrize("inventory", [None, {"src/app/page.tsx": None}])
def test_source_completion_inventory_unavailable_fails_closed(inventory):
    checked = []

    async def snapshot():
        return inventory

    def check(files, _evidence):
        checked.append(files)
        return None

    result = asyncio.run(ab.run_agent_build(
        system_prompt="s", user_prompt="x", model="m", execute=_ok_executor([]),
        complete=_scripted([
            '<omnia:action name="build">{}</omnia:action>',
            '<omnia:action name="done">{"summary":"unverified"}</omnia:action>',
        ]), snapshot_files=snapshot if inventory is not None else None,
        completion_check=check, coordinator_handoff=True, max_steps=2,
    ))

    assert not result.done and not result.needs_finalization
    assert checked == []


def test_source_completion_snapshot_cancellation_is_not_a_missing_inventory():
    built = False

    async def snapshot():
        if built:
            raise asyncio.CancelledError
        return {}

    async def execute(_action):
        nonlocal built
        built = True
        return {"ok": True}

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(ab.run_agent_build(
            system_prompt="s", user_prompt="x", model="m", execute=execute,
            complete=_scripted(['<omnia:action name="build">{}</omnia:action>']),
            snapshot_files=snapshot, completion_check=lambda *_: None,
            coordinator_handoff=True, max_steps=1,
        ))


@pytest.mark.parametrize("mutation", ["write_file", "edit_file", "bash", "probe"])
def test_coordinator_handoff_never_rescues_a_stale_fast_check(mutation):
    async def execute(action):
        if action.name == "probe":
            return {"ok": True, "environment_mutated": True}
        return {"ok": True, "content": "v1"}

    result = asyncio.run(ab.run_agent_build(
        system_prompt="s", user_prompt="x", model="m", execute=execute,
        complete=_scripted([
            '<omnia:action name="build">{}</omnia:action>',
            f'<omnia:action name="{mutation}">'
            '{"path":"page.tsx","cmd":"change","content":"v2"}</omnia:action>',
            '<omnia:action name="done">{"summary":"stale"}</omnia:action>',
        ]), max_steps=3, require_green_before_done=True, coordinator_handoff=True,
    ))
    assert not result.done and not result.needs_finalization


def test_coordinator_handoff_nudges_done_after_read_only_exploration():
    result = asyncio.run(ab.run_agent_build(
        system_prompt="s", user_prompt="x", model="m", execute=_ok_executor([]),
        complete=_scripted([
            '<omnia:action name="build">{}</omnia:action>',
            *[f'<omnia:action name="read_file">{{"path":"file{i}.tsx"}}</omnia:action>'
              for i in range(5)],
            '<omnia:action name="done">{"summary":"candidate"}</omnia:action>',
        ]), max_steps=7, require_green_before_done=True, coordinator_handoff=True,
    ))
    assert result.done and result.needs_finalization
    assert any("HAND OFF" in item["content"] for item in result.transcript)


@pytest.mark.parametrize("call_done", [True, False])
@pytest.mark.parametrize("rejection,expected_done", [
    ({"ok": False, "error": "exact edit rejected", "environment_mutated": False}, True),
    ({"ok": False, "error": "unknown persistence result"}, False),
    ({"ok": False, "environment_mutated": "false"}, False),
    ({"ok": False, "environment_mutated": 0}, False),
    ({"ok": False, "environment_mutated": False, "files": {"page.tsx": "changed"}}, False),
    ({"ok": False, "environment_mutated": False, "mutation": {"source_changed": True}}, False),
    ({"ok": False, "environment_mutated": False, "invalidated_dimensions": ["fast_check"]}, False),
    ({"ok": False, "environment_mutated": False, "content_change": {"after_sha256": "x"}}, False),
    ({"ok": False, "environment_mutated": False, "infra_dead": True}, False),
    ({"ok": True, "environment_mutated": False, "content": "changed"}, False),
])
def test_failed_edit_preserves_fast_check_only_with_unambiguous_no_mutation(
    rejection, expected_done, call_done
):
    async def execute(action):
        if action.name == "edit_file":
            return rejection
        return {"ok": True, "content": "candidate"}

    result = asyncio.run(ab.run_agent_build(
        system_prompt="s", user_prompt="minimal patch", model="m", execute=execute,
        complete=_scripted([
            '<omnia:action name="write_file">'
            '{"path":"page.tsx","content":"candidate"}</omnia:action>',
            '<omnia:action name="build">{}</omnia:action>',
            '<omnia:action name="edit_file">'
            '{"path":"page.tsx","search":"missing","replace":"x"}</omnia:action>',
            '<omnia:action name="done">{"summary":"candidate"}</omnia:action>',
        ]), max_steps=4 if call_done else 3,
        require_green_before_done=True, coordinator_handoff=True,
        edit_mode=True,
    ))
    assert result.done is expected_done
    assert result.needs_finalization is expected_done


@pytest.mark.parametrize("bash_result", [
    {"ok": True}, {"ok": True, "environment_mutated": True},
])
def test_nonmutating_failed_edit_cannot_restore_fast_check_lost_to_bash(bash_result):
    async def execute(action):
        if action.name == "bash":
            return bash_result
        if action.name == "edit_file":
            return {"ok": False, "error": "exact edit rejected", "environment_mutated": False}
        return {"ok": True, "content": "candidate"}

    result = asyncio.run(ab.run_agent_build(
        system_prompt="s", user_prompt="minimal patch", model="m", execute=execute,
        complete=_scripted([
            '<omnia:action name="write_file">'
            '{"path":"page.tsx","content":"candidate"}</omnia:action>',
            '<omnia:action name="build">{}</omnia:action>',
            '<omnia:action name="bash">{"cmd":"unknown"}</omnia:action>',
            '<omnia:action name="edit_file">'
            '{"path":"page.tsx","search":"missing","replace":"x"}</omnia:action>',
            '<omnia:action name="done">{"summary":"stale"}</omnia:action>',
        ]), max_steps=5, require_green_before_done=True, coordinator_handoff=True,
        edit_mode=True,
    ))
    assert not result.done and not result.needs_finalization


def test_untrusted_deferred_runtime_does_not_relax_the_ordinary_gate():
    async def execute(action):
        return {"ok": action.name != "runtime_check", "deferred_to": "max_finalization"}

    result = asyncio.run(ab.run_agent_build(
        system_prompt="s", user_prompt="x", model="m", execute=execute,
        complete=_scripted([
            '<omnia:action name="build">{}</omnia:action>',
            '<omnia:action name="runtime_check">{"path":"/"}</omnia:action>',
            '<omnia:action name="done">{"summary":"unverified"}</omnia:action>',
        ]), max_steps=3, require_green_before_done=True, ship_green_on_abort=False,
    ))
    assert not result.done


def test_build_system_prompt_without_skills_is_unchanged():
    p = ab.build_system_prompt("STACK GUIDE")
    assert p == ab.LOOP_PROTOCOL + "\n\n" + "STACK GUIDE"
    # None / empty skills must not alter the output
    assert ab.build_system_prompt("STACK GUIDE", skills=None) == p
    assert ab.build_system_prompt("STACK GUIDE", skills="   ") == p


def test_build_system_prompt_appends_skills():
    p = ab.build_system_prompt("GUIDE", skills="SKILL BODY")
    assert p.endswith("SKILL BODY")
    assert "GUIDE" in p and ab.LOOP_PROTOCOL in p


def test_load_stack_skills_reads_exact_max_ui_contract():
    block = ab.load_stack_skills("max-miniapp-nextjs")

    assert block is not None
    assert "@maxhub/max-ui@0.2.0" in block
    assert "DESIGN.md" in block
    assert "Do not import `Panel`, `Grid`, `Container`, `Flex` or `TabBar`" in block
    assert "Do not browse `node_modules`" in block
    assert "pnpm typecheck" in block


def test_load_stack_skills_absent_or_none_is_none():
    assert ab.load_stack_skills(None) is None
    assert ab.load_stack_skills("definitely-not-a-real-template-xyz") is None


# ── agentic builder canary gate ──────────────────────────────────────────────


def test_agentic_global_on_enables_everyone():
    assert ab.is_agentic_enabled(True, "", "u1") is True
    assert ab.is_agentic_enabled(True, None, None) is True


def test_agentic_canary_user_enabled_when_global_off():
    assert ab.is_agentic_enabled(False, "u1, u2 ,u3", "u2") is True


def test_agentic_non_canary_user_disabled_when_global_off():
    assert ab.is_agentic_enabled(False, "u1,u2", "u9") is False


def test_agentic_off_by_default():
    assert ab.is_agentic_enabled(False, "", "u1") is False
    assert ab.is_agentic_enabled(False, None, "u1") is False
    assert ab.is_agentic_enabled(False, "u1", None) is False


def test_green_explore_stall_nudges_done_not_write():
    """After build+runtime are green, a no-write thrash (bash spiral) must nudge
    the model to FINISH (done), not to write — fixes the observed see-driven
    bash spiral that wasted ~10 steps post-success."""
    record: list = []
    replies = [
        '<omnia:action name="write_file">{"path":"src/app/page.tsx","content":"v1"}</omnia:action>',
        '<omnia:action name="build"></omnia:action>',
        '<omnia:action name="runtime_check">{"path":"/"}</omnia:action>',
        '<omnia:action name="bash">{"cmd":"pnpm test"}</omnia:action>',
        '<omnia:action name="bash">{"cmd":"pnpm lint"}</omnia:action>',
        '<omnia:action name="bash">{"cmd":"echo hi"}</omnia:action>',
        '<omnia:action name="done">{"summary":"crm built"}</omnia:action>',
    ]
    res = asyncio.run(
        ab.run_agent_build(
            system_prompt="s",
            user_prompt="x",
            model="m",
            execute=_ok_executor(record),
            complete=_scripted(replies),
            max_steps=20,
        )
    )
    assert res.done is True and res.stop_reason == "done"
    # the GREEN done-nudge was issued (not the write nudge)
    user_msgs = [m["content"] for m in res.transcript if m["role"] == "user"]
    assert any(m == ab._DONE_WHEN_GREEN_NUDGE for m in user_msgs)


_FONTS_IMPORT = (
    '@import url("https://fonts.googleapis.com/css2?'
    'family=Libre+Franklin:wght@400;500;600;700&family=Lora:wght@400;500;600&display=swap");'
)


if __name__ == "__main__":
    # Allow `python tests/test_agent_builder.py` without pytest.
    import sys

    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"PASS {fn.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL {fn.__name__}: {e}")
        except Exception as e:
            failed += 1
            print(f"ERROR {fn.__name__}: {type(e).__name__}: {e}")
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    sys.exit(1 if failed else 0)
