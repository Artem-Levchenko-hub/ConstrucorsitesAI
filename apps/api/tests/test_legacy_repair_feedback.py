"""Legacy repairs receive exact owned source without earning progress or reads."""

from __future__ import annotations

import asyncio
import hashlib
import json
from copy import deepcopy

import pytest

from yleum_api.services import agent_builder as ab

ENTRY = "src/app/page.tsx"
SDK = "src/lib/omnia/catalog-client.ts"
MARKER = "CURRENT_EDITABLE_SOURCE_JSON\n"
GAP = "Connect useYleumCatalogSelection(items) category/getOption(item)."


def action(name, **args):
    return f'<omnia:action name="{name}">{json.dumps(args)}</omnia:action>'


def page_source():
    # The editable target is beyond the generic 6000-character observation cap.
    tail = "\nexport const selection = 'not connected';\n"
    return "/*" + "x" * (8662 - len(tail) - 4) + "*/" + tail


def context_from(messages):
    contexts = [message["content"].split(MARKER, 1)[1]
                for message in messages
                if isinstance(message.get("content"), str) and MARKER in message["content"]]
    assert len(contexts) == 1
    return json.loads(contexts[0])


def run_loop(
    actions, *, source=None, checker=True, snapshot_available=True, edit_mutation_flag=True,
):
    state = {ENTRY: page_source() if source is None else source, SDK: "locked SDK fixture"}
    calls, executed, events, checked = [], [], [], []
    snapshots = 0

    async def complete(messages, _model, **_kwargs):
        calls.append(deepcopy(messages))
        return actions[len(calls) - 1]

    async def execute(current):
        executed.append((current.name, current.path))
        if current.name == "read_file":
            return {"ok": True, "content": state.get(current.path, "")}
        if current.name == "edit_file":
            before = state.get(current.path, "")
            search = current.args["search"]
            if search not in before:
                return {"ok": False, "error": "search text not found; read the file first",
                        **({"environment_mutated": False} if edit_mutation_flag else {})}
            state[current.path] = before.replace(search, current.args["replace"], 1)
            return {"ok": True, "content": state[current.path]}
        return {"ok": True, "detail": "clean"}

    async def snapshot():
        nonlocal snapshots
        snapshots += 1
        return dict(state)

    async def emit(event, payload):
        events.append((event, payload))

    def check(files, _evidence):
        checked.append(files[ENTRY])
        return None if "selection = 'connected'" in files[ENTRY] else GAP

    result = asyncio.run(ab.run_agent_build(
        system_prompt="fixture", user_prompt="repair selection", model="fixture",
        complete=complete, execute=execute,
        snapshot_files=snapshot if snapshot_available else None,
        completion_check=check if checker else None, coordinator_handoff=True,
        max_steps=len(actions), max_segments=1, emit=emit, ship_green_on_abort=False,
    ))
    return result, calls, executed, events, checked, snapshots


def test_inline_gap_and_exact_mismatch_keep_full_current_source_after_read_credit_is_spent():
    original = page_source()
    actions = [
        action("build"), action("done", summary="not actually ready"),
        action("read_file", path=SDK),  # The existing one-read credit is consumed here.
        action("read_file", path=ENTRY),  # Ordinary read remains blocked.
        action("edit_file", path=ENTRY, search="stale exact target", replace="ignored"),
        action("read_file", path=ENTRY),
        action("edit_file", path=ENTRY, search="selection = 'not connected'",
               replace="selection = 'connected'"),
        action("build"), action("done", summary="repaired"),
    ]
    result, calls, executed, events, checked, snapshots = run_loop(actions)
    assert all(set(message) == {"role", "content"} and
               message["role"] in {"system", "user", "assistant"}
               for messages in calls for message in messages)
    for index in (2, 4, 5, 6):
        context = context_from(calls[index])
        assert context == {
            "path": ENTRY, "complete": True, "content": original,
            "sha256": hashlib.sha256(original.encode()).hexdigest(),
            "utf8_bytes": 8662,
        }
    assert len(original) > ab._MAX_OBS_CHARS
    assert "stale exact target" in str(calls[5])
    assert result.done and result.needs_finalization
    assert checked[-1] == original.replace("'not connected'", "'connected'")
    assert checked[:-1] and all(source == original for source in checked[:-1])
    assert [item for item in executed if item[0] == "read_file"] == [("read_file", SDK)]
    assert snapshots == 2 * len(executed)  # No new physical snapshot or tool read.
    assert original not in str(events)  # Full source is private model context, not UI telemetry.
    assert not any(MARKER in str(message) for message in calls[7])  # Actual mutation clears it.


@pytest.mark.parametrize("path,files", [
    (SDK, {SDK: "DO_NOT_DISCLOSE"}),
    (".env", {".env": "DO_NOT_DISCLOSE"}),
    ("src/app/.env.tsx", {"src/app/.env.tsx": "DO_NOT_DISCLOSE"}),
    ("src/app/missing.tsx", {ENTRY: "DO_NOT_DISCLOSE"}),
    ("../src/app/page.tsx", {"../src/app/page.tsx": "DO_NOT_DISCLOSE"}),
    ("./src/app/page.tsx", {ENTRY: "DO_NOT_DISCLOSE"}),
    (ENTRY, {ENTRY: "sk-" + "s" * 32}),
    (ENTRY, {ENTRY: "export const value = '\ud800'"}),
    (ENTRY, {ENTRY: "z" * (64 * 1024 + 1)}),
    (ENTRY, {ENTRY: "я" * (32 * 1024 + 1)}),
    (ENTRY, {ENTRY: "\\" * 40_000}),  # JSON escaping must not overflow the wire cap.
    (ENTRY, {ENTRY: "export const value = process.env.DATABASE_URL;"}),
    (ENTRY, {ENTRY: "safe", "../other.tsx": "DO_NOT_DISCLOSE"}),
    (ENTRY, {ENTRY: None}),
    (ENTRY, None),
])
def test_repair_context_honestly_withholds_forbidden_unavailable_or_unbounded_source(path, files):
    rendered = ab._repair_source_context(path, files)
    context = json.loads(rendered.split(MARKER, 1)[1])
    assert context["complete"] is False
    assert context["withheld"]
    assert "content" not in context and "sha256" not in context
    assert "DO_NOT_DISCLOSE" not in rendered
    assert "sk-" not in rendered and "\ud800" not in rendered
    assert len(rendered.encode("utf-8")) <= 64 * 1024


def test_full_utf8_context_is_exact_and_does_not_use_observation_clipping():
    source = "export const title = 'Сохранить';\n" + "/*я*/\n" * 1500
    rendered = ab._repair_source_context(ENTRY, {ENTRY: source})
    context = json.loads(rendered.split(MARKER, 1)[1])
    assert context["content"] == source and context["complete"] is True
    assert context["utf8_bytes"] == len(source.encode("utf-8"))
    assert context["sha256"] == hashlib.sha256(source.encode("utf-8")).hexdigest()
    assert len(rendered) > ab._MAX_OBS_CHARS


@pytest.mark.parametrize("edit_mutation_flag", [False, True])
def test_exact_patch_failure_without_managed_gap_still_gets_owned_target(edit_mutation_flag):
    result, calls, _executed, _events, _checked, _snapshots = run_loop([
        action("edit_file", path=ENTRY, search="absent", replace="nope"),
        action("build"),
    ], checker=False, edit_mutation_flag=edit_mutation_flag)
    assert context_from(calls[1])["content"] == page_source()
    assert not result.done


@pytest.mark.parametrize("edit_mutation_flag", [False, True])
def test_failed_patch_feedback_does_not_earn_source_progress_or_budget(edit_mutation_flag):
    actions = [action("edit_file", path=ENTRY, search=f"absent-{i}", replace="nope")
               for i in range(ab._NO_WRITE_ABORT_AT + 5)]
    result, calls, executed, _events, _checked, snapshots = run_loop(
        actions, checker=False, edit_mutation_flag=edit_mutation_flag,
    )
    assert result.stop_reason == "exploring" and not result.done
    assert len(calls) == ab._NO_WRITE_ABORT_AT + 1
    assert len(executed) == ab._NO_WRITE_ABORT_AT
    assert snapshots == 2 * len(executed)
    assert all(context_from(messages)["content"] == page_source() for messages in calls[1:])


def test_repair_context_survives_recent_turn_window_without_pinning_old_observations():
    result, calls, _executed, _events, _checked, _snapshots = run_loop([
        action("build"), action("done", summary="missing selection"),
        *[action("build", fixture=i) for i in range(8)], action("done", summary="still missing"),
    ])
    assert not result.done
    assert len(calls[-1]) <= 15  # Existing system/task + last12, plus one bounded context.
    assert context_from(calls[-1])["content"] == page_source()


def test_forbidden_failed_patch_target_does_not_fall_back_to_another_source():
    result, calls, _executed, _events, _checked, _snapshots = run_loop([
        action("edit_file", path=SDK, search="absent", replace="nope"), action("build"),
    ], checker=False)
    context = context_from(calls[1])
    assert context["complete"] is False and context["withheld"]
    assert "content" not in context and "locked SDK fixture" not in str(calls[1])
    assert not result.done


def test_missing_snapshot_never_uses_model_write_inventory_as_current_source():
    result, calls, _executed, _events, _checked, _snapshots = run_loop([
        action("edit_file", path=ENTRY, search="absent", replace="nope"), action("build"),
    ], checker=False, snapshot_available=False)
    context = context_from(calls[1])
    assert context["complete"] is False and "content" not in context
    assert not result.done


def test_context_does_not_accept_missing_managed_selection_until_it_is_wired():
    from yleum_api.services.max_management_contract import build_management_contract
    from yleum_api.services.max_management_generation import management_source_gap

    source = """
import {useYleumAppProfile} from '@/lib/omnia/app-profile-client';
import {useYleumCatalog, useYleumCatalogSelection} from '@/lib/omnia/catalog-client';
export default function Page() {
  const profile = useYleumAppProfile();
  const {items} = useYleumCatalog();
  return <main>
    {items.map(item => <article key={item.id}>{item.title} {item.price}</article>)}
    <h1>{profile.profile?.app_name}</h1>
  </main>;
}
"""
    files = {ENTRY: source, ".omnia/management.json":
             build_management_contract(["catalog"]).model_dump_json()}
    gap = management_source_gap(files)
    assert gap and "useYleumCatalogSelection" in gap
    context = json.loads(ab._repair_source_context(ENTRY, files).split(MARKER, 1)[1])
    assert context["content"] == source
    assert management_source_gap(files) == gap  # Feedback grants no source/proof witness.
    wired = source.replace("  return <main>", """
  const selection = useYleumCatalogSelection(items);
  return <main>
    {items.map(item => <button key={item.category}
      onClick={() => selection.setCategory(item.category)}>{item.category}</button>)}""")
    assert management_source_gap({**files, ENTRY: wired}) is None


def test_cold_resume_preserves_exact_context_call_and_consumed_read_without_extra_progress():
    from types import SimpleNamespace
    from uuid import uuid4

    from yleum_api.services.generation.recovery_execution import GenerationYielded

    state = {ENTRY: page_source(), SDK: "locked SDK fixture"}
    saved, requests, executed = {}, [], []
    cold = False
    actions = [action("build"), action("done", summary="missing"),
               action("read_file", path=SDK), action("done", summary="still missing")]

    async def complete(messages, _model, **_kwargs):
        requests.append(deepcopy(messages))
        return actions[len(requests) - 1]

    async def execute(current):
        executed.append((current.name, current.path))
        return {"ok": True, "detail": "clean", "content": state.get(current.path, "")}

    async def snapshot():
        return dict(state)

    async def provider(frame, invoke):
        nonlocal saved
        if not cold and len(requests) == 3:
            saved = deepcopy(frame["value"])
            raise GenerationYielded(run_id=uuid4(), checkpoint=None, attempt_seq=1,
                                    phase="waiting_dependency")
        if cold:
            assert frame["value"]["call_messages"] == saved["call_messages"]
        return await invoke()

    driver = SimpleNamespace(restore_frame=lambda _kind: deepcopy(saved) if cold else None,
                             provider=provider)

    async def run():
        return await ab._run_agent_segment(
            system_prompt="fixture", user_prompt="repair", model="fixture", execute=execute,
            complete=complete, snapshot_files=snapshot, completion_check=lambda *_: GAP,
            coordinator_handoff=True, max_steps=len(actions), ship_green_on_abort=False,
            continuation=driver,
        )

    with pytest.raises(GenerationYielded):
        asyncio.run(run())
    assert saved["source_repair_context_path"] == ENTRY
    assert saved["source_repair_read_used"] is True
    assert saved["no_write_streak"] == 3
    assert context_from(saved["call_messages"])["content"] == state[ENTRY]
    cold = True
    result = asyncio.run(run())
    assert requests[-1] == saved["call_messages"]
    assert result.stop_reason == "max_steps" and not result.done
    assert len(requests) == len(actions) and len(executed) == 2
