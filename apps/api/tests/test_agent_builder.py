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
