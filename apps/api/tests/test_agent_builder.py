"""Unit tests for the agentic builder engine (services/agent_builder.py).

The loop is dependency-injected (`complete` + `execute`), so these run with
zero network/container — they exercise the protocol parser and the
plan→act→observe→verify control flow deterministically.

Run:  cd apps/api && uv run pytest tests/test_agent_builder.py -q
"""

from __future__ import annotations

import asyncio

from omnia_api.services import agent_builder as ab


# ── parse_action ────────────────────────────────────────────────────────────

def test_parse_bare_json():
    a = ab.parse_action('thinking...\n<omnia:action name="read_file">{"path": "src/app/page.tsx"}</omnia:action>')
    assert a is not None and a.name == "read_file" and a.path == "src/app/page.tsx"


def test_parse_fenced_json():
    reply = '<omnia:action name="write_file">\n```json\n{"path":"a.ts","content":"x"}\n```\n</omnia:action>'
    a = ab.parse_action(reply)
    assert a is not None and a.name == "write_file" and a.args["content"] == "x"


def test_parse_done_loose_body():
    a = ab.parse_action('<omnia:action name="done">all built</omnia:action>')
    assert a is not None and a.name == "done" and "all built" in a.args["summary"]


def test_parse_build_empty():
    a = ab.parse_action('<omnia:action name="build"></omnia:action>')
    assert a is not None and a.name == "build" and a.args == {}


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


def _ok_executor(record):
    async def _execute(action: ab.Action):
        record.append((action.name, action.path))
        if action.name == "build":
            return {"ok": True, "detail": "typecheck clean"}
        if action.name in ("write_file", "edit_file"):
            return {"ok": True, "content": action.args.get("content", "patched")}
        return {"ok": True, "detail": "ok"}

    return _execute


def test_loop_happy_path_reaches_done():
    record: list = []
    replies = [
        '<omnia:action name="write_file">{"path":"src/app/page.tsx","content":"export default function P(){return null}"}</omnia:action>',
        '<omnia:action name="build"></omnia:action>',
        '<omnia:action name="done">{"summary":"built the page"}</omnia:action>',
    ]
    res = asyncio.run(ab.run_agent_build(
        system_prompt="sys", user_prompt="build it", model="m",
        execute=_ok_executor(record), complete=_scripted(replies), max_steps=8,
    ))
    assert res.done is True
    assert res.stop_reason == "done"
    assert "src/app/page.tsx" in res.files
    assert ("build", "") in record
    assert res.steps == 3


def test_loop_hits_step_budget():
    record: list = []
    # always asks to read — never done
    replies = ['<omnia:action name="read_file">{"path":"a.ts"}</omnia:action>']
    res = asyncio.run(ab.run_agent_build(
        system_prompt="sys", user_prompt="x", model="m",
        execute=_ok_executor(record), complete=_scripted(replies), max_steps=4,
    ))
    assert res.done is False
    assert res.stop_reason == "max_steps"
    assert res.steps == 4


def test_loop_stalls_on_no_action():
    replies = ["I cannot do that.", "Still no action block here."]
    res = asyncio.run(ab.run_agent_build(
        system_prompt="sys", user_prompt="x", model="m",
        execute=_ok_executor([]), complete=_scripted(replies), max_steps=8,
    ))
    assert res.done is False
    assert res.stop_reason == "stalled"


def test_loop_gateway_error_is_soft():
    async def _boom(convo, model, **kw):
        raise RuntimeError("gateway 502")

    res = asyncio.run(ab.run_agent_build(
        system_prompt="sys", user_prompt="x", model="m",
        execute=_ok_executor([]), complete=_boom, max_steps=4,
    ))
    assert res.done is False
    assert res.stop_reason == "error"
    assert "gateway" in res.summary


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
    res = asyncio.run(ab.run_agent_build(
        system_prompt="s", user_prompt="x", model="m",
        execute=_ok_executor([]), complete=_scripted(replies), max_steps=30,
    ))
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
    res = asyncio.run(ab.run_agent_build(
        system_prompt="sys", user_prompt="x", model="m",
        execute=_fail_exec, complete=_scripted(replies), max_steps=6,
    ))
    # write failed → file must NOT be in the committed set
    assert "a.ts" not in res.files
    assert res.done is True


# ── runtime-sight tools: read_logs + runtime_check ──────────────────────────

def test_parse_new_observe_actions():
    a = ab.parse_action('check it\n<omnia:action name="runtime_check">{"path":"/dashboard"}</omnia:action>')
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
            return ({"ok": False, "detail": "route / FAILED (HTTP 500)"}
                    if state["crashed"] else {"ok": True, "detail": "route / renders OK"})
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
        '<omnia:action name="edit_file">{"path":"src/app/page.tsx","search":"v1","replace":"v2"}</omnia:action>',
        '<omnia:action name="runtime_check">{"path":"/"}</omnia:action>',
        '<omnia:action name="done">{"summary":"fixed runtime crash"}</omnia:action>',
    ]
    res = asyncio.run(ab.run_agent_build(
        system_prompt="s", user_prompt="x", model="m",
        execute=_exec, complete=_scripted(replies), max_steps=20,
    ))
    assert res.done is True and res.stop_reason == "done"
    # The SAME runtime_check ran twice (non-consecutive) WITHOUT a false looping abort.
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
            f'<omnia:action name="edit_file">{{"path":"a.ts","search":"v{i}","replace":"v{i+1}"}}</omnia:action>'
        )
        replies.append('<omnia:action name="build"></omnia:action>')
    replies.append('<omnia:action name="done">{"summary":"clean"}</omnia:action>')
    res = asyncio.run(ab.run_agent_build(
        system_prompt="s", user_prompt="x", model="m",
        execute=_ok_executor(record), complete=_scripted(replies), max_steps=30,
    ))
    assert res.stop_reason == "done"
    assert record.count(("build", "")) == 5  # every build actually executed


def test_consecutive_build_spam_still_caught():
    """The exemption must NOT defang the consecutive-repeat guard: a model that
    emits `build` back-to-back with nothing between is still stopped as looping."""
    replies = ['<omnia:action name="build"></omnia:action>']
    res = asyncio.run(ab.run_agent_build(
        system_prompt="s", user_prompt="x", model="m",
        execute=_ok_executor([]), complete=_scripted(replies), max_steps=30,
    ))
    assert res.stop_reason == "looping"
    assert res.steps < 30


# ── vision tool: see ─────────────────────────────────────────────────────────

def test_parse_see_action():
    a = ab.parse_action('look\n<omnia:action name="see">{"path":"/dashboard"}</omnia:action>')
    assert a is not None and a.name == "see" and a.path == "/dashboard"


def test_see_loop_fixes_then_done():
    """build clean → see (ugly) → fix → see (beautiful) → done. `see` is a verify
    action: it runs twice non-consecutively without a false looping abort."""
    record: list = []
    state = {"ugly": True}

    async def _exec(action: ab.Action):
        record.append((action.name, action.path))
        if action.name == "build":
            return {"ok": True, "detail": "typecheck clean"}
        if action.name == "see":
            return ({"ok": True, "detail": "verdict: generic (4/10)\n- hero too small"}
                    if state["ugly"]
                    else {"ok": True, "detail": "verdict: beautiful (9/10)\n(no concrete issues)"})
        if action.name == "edit_file":
            state["ugly"] = False
            return {"ok": True, "content": action.args.get("replace", "x")}
        return {"ok": True, "detail": "ok"}

    replies = [
        '<omnia:action name="write_file">{"path":"src/app/page.tsx","content":"v1"}</omnia:action>',
        '<omnia:action name="build"></omnia:action>',
        '<omnia:action name="see">{"path":"/"}</omnia:action>',
        '<omnia:action name="edit_file">{"path":"src/app/page.tsx","search":"v1","replace":"v2"}</omnia:action>',
        '<omnia:action name="see">{"path":"/"}</omnia:action>',
        '<omnia:action name="done">{"summary":"made it pretty"}</omnia:action>',
    ]
    res = asyncio.run(ab.run_agent_build(
        system_prompt="s", user_prompt="x", model="m",
        execute=_exec, complete=_scripted(replies), max_steps=20,
    ))
    assert res.done is True and res.stop_reason == "done"
    assert sum(1 for n, _ in record if n == "see") == 2  # ran twice, no false abort


# ── green-gate: require_green_before_done ────────────────────────────────────

def test_green_gate_off_by_default_allows_immediate_done():
    """Default (flag off): a `done` is honoured immediately — current behaviour,
    byte-identical to pre-Phase-2."""
    res = asyncio.run(ab.run_agent_build(
        system_prompt="s", user_prompt="x", model="m",
        execute=_ok_executor([]),
        complete=_scripted(['<omnia:action name="done">{"summary":"x"}</omnia:action>']),
        max_steps=8,
    ))
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
    res = asyncio.run(ab.run_agent_build(
        system_prompt="s", user_prompt="x", model="m",
        execute=_ok_executor(record), complete=_scripted(replies), max_steps=12,
        require_green_before_done=True,
    ))
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
    res = asyncio.run(ab.run_agent_build(
        system_prompt="s", user_prompt="x", model="m",
        execute=_ok_executor(record), complete=_scripted(replies), max_steps=12,
        require_green_before_done=True,
    ))
    # The step-0 `done` was rejected (else it would have ended at steps==1).
    assert res.done is True and res.steps == 4
    assert ("build", "") in record and ("runtime_check", "/") in record


def test_green_gate_cap_prevents_hang():
    """flag on but the app is never verifiable: after _DONE_REJECT_CAP rejections
    the `done` is honoured anyway (fail-soft — the server gate is the backstop)."""
    replies = ['<omnia:action name="done">{"summary":"insist"}</omnia:action>']
    res = asyncio.run(ab.run_agent_build(
        system_prompt="s", user_prompt="x", model="m",
        execute=_ok_executor([]), complete=_scripted(replies), max_steps=12,
        require_green_before_done=True,
    ))
    assert res.done is True and res.stop_reason == "done"
    assert res.steps == ab._DONE_REJECT_CAP + 1  # rejected CAP times, then honoured


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
        except Exception as e:  # noqa
            failed += 1
            print(f"ERROR {fn.__name__}: {type(e).__name__}: {e}")
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    sys.exit(1 if failed else 0)
