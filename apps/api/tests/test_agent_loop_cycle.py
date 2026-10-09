"""Cycle breaker for the agentic loop — the no-WRITE streak guard.

Regression for the live bug: the agent looped on read-only actions
(read→grep→list→read→read) for the whole 80-step budget, writing ZERO files.
The consecutive-identical circuit breaker missed it (each step differs from the
last). The no-WRITE streak guard must catch a multi-step read CYCLE, nudge to
write, and abort early instead of burning the budget — while NOT tripping a real
build that reads a couple files before writing.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import PurePosixPath

import pytest

from yleum_api.services import agent_builder as ab


def _ok_executor(record: list):
    async def _execute(action: ab.Action):
        record.append((action.name, action.path))
        if action.name in ("write_file", "edit_file"):
            return {"ok": True, "content": action.args.get("content", "x")}
        return {"ok": True, "detail": "ok"}

    return _execute


def _cycle(replies: list[str]):
    box = {"i": 0}

    async def _complete(convo, model, **kw):
        r = replies[box["i"] % len(replies)]
        box["i"] += 1
        return r

    return _complete


def test_cycle_of_distinct_reads_aborts_as_exploring():
    # Three DIFFERENT read paths cycling → each step's signature differs from the
    # last, so the consecutive-identical circuit breaker never fires. The no-WRITE
    # streak guard must abort as "exploring", well before the 40-step budget.
    record: list = []
    replies = [
        '<omnia:action name="read_file">{"path":"a.ts"}</omnia:action>',
        '<omnia:action name="read_file">{"path":"b.ts"}</omnia:action>',
        '<omnia:action name="read_file">{"path":"c.ts"}</omnia:action>',
    ]
    res = asyncio.run(
        ab.run_agent_build(
            system_prompt="sys", user_prompt="x", model="m",
            execute=_ok_executor(record), complete=_cycle(replies), max_steps=40,
        )
    )
    assert res.done is False
    # Caught as a stuck cycle — either the global repeat guard ("looping") or the
    # no-write streak ("exploring") fires first; both mean "stuck", and both abort
    # well before the 40-step budget. The point is it does NOT run to the cap.
    assert res.stop_reason in ("looping", "exploring")
    assert res.steps < 40
    assert res.files == {}


def test_write_cycle_same_content_aborts_as_looping():
    # The live bug the no-write streak MISSED: a cycle that re-WRITES the same files
    # with identical content (write a → write b → build → repeat). Writes reset the
    # no-write streak and the steps differ from the last, so only the GLOBAL repeat
    # guard catches it — abort as "looping" well before the 40-step budget.
    record: list = []
    replies = [
        '<omnia:action name="write_file">{"path":"entities/A.json","content":"X"}</omnia:action>',
        '<omnia:action name="write_file">{"path":"entities/B.json","content":"Y"}</omnia:action>',
        '<omnia:action name="build"></omnia:action>',
    ]
    res = asyncio.run(
        ab.run_agent_build(
            system_prompt="sys", user_prompt="x", model="m",
            execute=_ok_executor(record), complete=_cycle(replies), max_steps=40,
        )
    )
    # Since 35306d5 (ship-green-on-abort), a re-write cycle whose build is GREEN is
    # shipped as done_on_green rather than discarded as "looping" — both mean the
    # cycle was CAUGHT early (not run to budget), which is the point of this guard.
    assert res.stop_reason in ("looping", "done_on_green")
    assert res.steps < 40  # caught, not run to budget


def test_reads_then_write_is_not_falsely_aborted():
    # A legit build reads a couple files, then writes — the streak resets on the
    # write, so it reaches done without a false "exploring" abort.
    record: list = []
    replies = [
        '<omnia:action name="read_file">{"path":"a.ts"}</omnia:action>',
        '<omnia:action name="read_file">{"path":"b.ts"}</omnia:action>',
        '<omnia:action name="write_file">{"path":"src/app/page.tsx","content":"x"}</omnia:action>',
        '<omnia:action name="build"></omnia:action>',
        '<omnia:action name="done">{"summary":"built"}</omnia:action>',
    ]
    box = {"i": 0}

    async def _complete(convo, model, **kw):
        i = box["i"]
        box["i"] += 1
        return replies[i] if i < len(replies) else replies[-1]

    res = asyncio.run(
        ab.run_agent_build(
            system_prompt="sys", user_prompt="x", model="m",
            execute=_ok_executor(record), complete=_complete, max_steps=12,
        )
    )
    assert res.done is True
    assert res.stop_reason == "done"
    assert "src/app/page.tsx" in res.files


def test_rewrite_rotation_without_build_aborts_as_looping():
    # The live messenger bug: the agent ROTATES rewrites across the same files with
    # VARYING content and NEVER runs build. sig_seen misses it (content differs each
    # step), the consecutive guard misses it (paths alternate), the no-write streak
    # misses it (it's all writes). The build-pressure / rotation guard must catch it
    # — force a build, then abort as "looping" well before the budget.
    record: list = []
    paths = ["src/app/(app)/chat/page.tsx", "src/app/(app)/chat/[id]/room.tsx"]
    box = {"i": 0}

    async def _complete(convo, model, **kw):
        i = box["i"]
        box["i"] += 1
        p = paths[i % len(paths)]
        # content varies every step → the signature never repeats
        return f'<omnia:action name="write_file">{{"path":"{p}","content":"v{i}"}}</omnia:action>'

    res = asyncio.run(
        ab.run_agent_build(
            system_prompt="sys", user_prompt="x", model="m",
            execute=_ok_executor(record), complete=_complete, max_steps=40,
        )
    )
    assert res.done is False
    assert res.stop_reason == "looping"
    assert res.steps < 40  # caught early, not run to the step budget


def test_distinct_writes_then_build_not_falsely_aborted():
    # The guard must target REWRITES-without-build, NOT a legitimate first draft that
    # writes several DISTINCT files then builds. No path repeats before the build, so
    # no rotation is detected and the build reaches done.
    record: list = []
    replies = [
        '<omnia:action name="write_file">{"path":"a.tsx","content":"1"}</omnia:action>',
        '<omnia:action name="write_file">{"path":"b.tsx","content":"2"}</omnia:action>',
        '<omnia:action name="write_file">{"path":"c.tsx","content":"3"}</omnia:action>',
        '<omnia:action name="write_file">{"path":"d.tsx","content":"4"}</omnia:action>',
        '<omnia:action name="build"></omnia:action>',
        '<omnia:action name="done">{"summary":"messenger"}</omnia:action>',
    ]
    box = {"i": 0}

    async def _complete(convo, model, **kw):
        i = box["i"]
        box["i"] += 1
        return replies[i] if i < len(replies) else replies[-1]

    res = asyncio.run(
        ab.run_agent_build(
            system_prompt="sys", user_prompt="x", model="m",
            execute=_ok_executor(record), complete=_complete, max_steps=12,
        )
    )
    assert res.done is True
    assert res.stop_reason == "done"
    assert len(res.files) == 4  # all distinct writes landed, none blocked as churn


def _action(name: str, **args) -> str:
    return f'<omnia:action name="{name}">{json.dumps(args)}</omnia:action>'


def _repair_scenario(mutation: str, *, initial: str = "v0", read_paths=None, receipt=False):
    """Run the real loop against the existing Cell content-result contract."""
    page = "src/app/page.tsx"
    state = {page: initial, "src/app/other.tsx": "v0"}
    replies = []
    for i in range(4):
        replies.append(_action("read_file", path=(read_paths or [page] * 4)[i]))
        if i == 3:
            break
        if mutation == "external_no_op":
            replies.append(_action("build"))
        path = "src/app/other.tsx" if mutation == "other_file" else page
        if mutation == "safe_alias":
            path = "./src\\app/page.tsx"
        elif mutation == "traversal":
            path = "src/app/../app/page.tsx"
        if mutation in {"no_op_write", "missing_content", "truncated_no_op", "external_no_op"}:
            content = f"v{i + 1}" if mutation == "external_no_op" else initial
            replies.append(_action("write_file", path=path, content=content, attempt=i))
        else:
            replies.append(_action(
                "edit_file", path=path, search=f"v{i}",
                replace=f"v{i}" if mutation == "no_op_edit" else f"v{i + 1}",
            ))
        replies.append(_action("build"))
    replies.append(_action("done", summary="repaired"))
    executed = []

    async def execute(action):
        executed.append(action)
        path = str(PurePosixPath(action.path.replace("\\", "/")))
        # Deliberately accept an unsafe alias in this injected executor: the loop
        # must never credit it as progress even if a dependency says ok=True.
        if ".." in path:
            path = page
        if action.name == "read_file":
            content = state[path]
            if len(content) > 16_000:
                content = content[:16_000] + f"\n…[truncated {len(content) - 16_000} chars]"
            return {"ok": True, "content": content}
        if action.name == "build":
            if mutation == "external_no_op":
                # The world can change between the model's last read and its
                # write; that stale difference must not credit a no-op write.
                state[page] = f"v{sum(a.name == 'build' for a in executed) // 2 + 1}"
            return {"ok": False, "detail": f"compiler failure {len(executed)}"}
        if mutation == "failed_edit":
            return {"ok": False, "content": f"v{len(executed)}", "error": "failed"}
        if action.name == "write_file":
            previous = state.get(path)
            state[path] = action.args["content"]
        else:
            previous = state[path]
            state[path] = state[path].replace(action.args["search"], action.args["replace"], 1)
        if mutation == "missing_content":
            return {"ok": True, "detail": "wrote file"}
        result = {"ok": True, "content": state[path]}
        if receipt:
            result["content_change"] = {
                "path": path,
                "before_sha256": hashlib.sha256(previous.encode()).hexdigest(),
                "after_sha256": hashlib.sha256(state[path].encode()).hexdigest(),
            }
            if receipt == "wrong_path":
                result["content_change"]["path"] = "src/app/other.tsx"
            elif receipt == "bad_before":
                result["content_change"]["before_sha256"] = "untrusted"
            elif receipt == "wrong_after":
                result["content_change"]["after_sha256"] = "f" * 64
            elif receipt == "missing_before":
                result["content_change"].pop("before_sha256")
        return result

    res = asyncio.run(ab.run_agent_build(
        system_prompt="sys", user_prompt="repair", model="m",
        execute=execute, complete=_cycle(replies), max_steps=24,
        edit_mode=True, ship_green_on_abort=False,
    ))
    return res, executed


@pytest.mark.parametrize("mutation", ["edit", "safe_alias"])
def test_repeated_read_after_confirmed_same_file_edits_reaches_done(mutation):
    res, _ = _repair_scenario(mutation)
    assert res.done is True
    assert res.stop_reason == "done"
    # Every requested read must actually produce an observation, including the
    # fourth read previously aborted before execution.
    reads = [turn for turn in res.transcript
             if turn["content"].startswith("[observation: read_file")]
    assert len(reads) == 4
    assert res.files["src/app/page.tsx" if mutation == "edit" else "./src\\app/page.tsx"] == "v3"


@pytest.mark.parametrize("mutation", [
    "no_op_write", "no_op_edit", "failed_edit", "other_file", "traversal", "missing_content",
])
def test_unconfirmed_or_other_file_change_preserves_read_repeat_limit(mutation):
    res, _ = _repair_scenario(mutation)
    assert res.done is False
    assert res.stop_reason == "looping"
    assert res.steps == 10  # fourth identical read attempt, NUDGE at attempts 2/3
    reads = [turn for turn in res.transcript
             if turn["content"].startswith("[observation: read_file")]
    assert len(reads) == 1


def test_truncated_no_op_write_does_not_look_like_content_progress():
    res, _ = _repair_scenario("truncated_no_op", initial="v0" + "x" * 16_001)
    assert res.stop_reason == "looping"
    assert res.steps == 10


def test_safe_read_aliases_share_the_same_unchanged_repeat_limit():
    res, _ = _repair_scenario("failed_edit", read_paths=[
        "src/app/page.tsx", "./src/app/page.tsx", "src\\app\\page.tsx", "src//app/page.tsx",
    ])
    assert res.stop_reason == "looping"
    assert res.steps == 10


def test_no_op_write_cannot_claim_a_change_since_an_older_read():
    res, _ = _repair_scenario("external_no_op")
    assert res.stop_reason == "looping"
    assert res.steps == 13


def test_large_file_edits_with_exact_content_receipts_allow_the_next_read():
    res, _ = _repair_scenario("edit", initial="v0" + "x" * 16_001, receipt=True)
    assert res.stop_reason == "done"
    assert res.files["src/app/page.tsx"].startswith("v3")


@pytest.mark.parametrize("mutation", ["no_op_write", "no_op_edit", "other_file", "traversal"])
def test_content_receipts_only_credit_changed_same_safe_file(mutation):
    res, _ = _repair_scenario(mutation, receipt=True)
    assert res.stop_reason == "looping"
    assert res.steps == 10


@pytest.mark.parametrize("receipt", ["wrong_path", "bad_before", "wrong_after", "missing_before"])
def test_invalid_receipt_never_resets_reads_even_when_generic_witness_changed(receipt):
    res, _ = _repair_scenario("edit", receipt=receipt)
    assert res.stop_reason == "looping"
    assert res.steps == 10
