from types import SimpleNamespace
from uuid import uuid4

import pytest

from yleum_api.core.config import get_settings
from yleum_api.services import agent_native
from yleum_api.services.generation import agent_generation
from yleum_api.services.generation.agent_finalization import (
    unchanged_candidate_before_finalization,
    validate_edit_source_change,
)
from yleum_api.services.generation.contracts import GenerationIds, SourceBaseline


def turn(name, args):
    return {"content": [{"type": "tool_use", "id": str(uuid4()), "name": name,
                         "input": args}], "stop_reason": "tool_use"}


@pytest.fixture
def edit_workspace(monkeypatch):
    settings = get_settings().model_copy(update={"use_native_agent": True})
    monkeypatch.setattr(agent_generation, "get_settings", lambda: settings)
    monkeypatch.setattr(agent_native, "get_settings", lambda: settings)
    initial = {"src/app/page.tsx": "Catalog; History", "src/lib/auth.ts": "auth"}
    workspace = dict(initial)
    executed = []

    async def execute(action):
        executed.append(action.name)
        if action.name == "write_file":
            workspace[action.path] = action.args["content"]
        return {"ok": True, "content": workspace.get(action.path, ""), "detail": "clean"}

    async def export():
        return dict(workspace)

    async def run(provider, *, snapshot=True):
        monkeypatch.setattr(agent_native, "_call_messages", provider)
        handle = SimpleNamespace(is_portable=lambda: True, export_files=export)
        if snapshot:
            handle.snapshot_files = export
        return await agent_generation.execute_agent_turn(
            _agent_res=None, _is_edit=True, _max_has_generated_snapshot=True,
            _max_seed_files={}, _max_shell_enabled=True,
            baseline=SourceBaseline(uuid4(), "baseline", initial),
            ids=GenerationIds(*(uuid4() for _ in range(5))), is_free=False,
            project_info=SimpleNamespace(template="max_miniapp"),
            prompt_text="Remove History", runtime=SimpleNamespace(handle=handle, coordinator=None),
            plan=SimpleNamespace(stack_guide="", skills=None, user="Remove History", steps=24),
            operations=SimpleNamespace(execute=execute, emit=None),
        )

    return run, initial, workspace, executed


@pytest.mark.parametrize("path", [".omnia/notes.md", "notes.md", "src/notes.md"])
@pytest.mark.parametrize("snapshot", [False, True])
async def test_notes_do_not_unlock_existing_edit_or_build(edit_workspace, path, snapshot):
    run, initial, workspace, executed = edit_workspace
    calls = []

    async def provider(*args, **kwargs):
        calls.append(kwargs)
        if len(calls) <= 4:
            return turn("read_file", {"path": "src/app/page.tsx"})
        if len(calls) == 5:
            return turn("write_file", {"path": path, "content": "I checked History"})
        return turn("build", {})

    with pytest.raises(RuntimeError, match="edit produced no source changes"):
        await run(provider, snapshot=snapshot)
    assert len(calls) == 6
    assert "build" not in executed
    assert all(workspace[path] == content for path, content in initial.items())
    assert {tool["name"] for tool in calls[-1]["tools"]} == {"write_file", "edit_file"}


@pytest.mark.parametrize("bad_turn", [
    {"content": [], "stop_reason": "max_tokens"},
    turn("write_file", {"path": "src/app/page.tsx"}),
])
@pytest.mark.parametrize("bad_calls", [{1, 2}, {5, 6}, {2, 5}])
async def test_incomplete_edit_responses_share_six_turn_stall_budget(
    edit_workspace, bad_turn, bad_calls,
):
    run, initial, workspace, executed = edit_workspace
    calls = []

    async def provider(*args, **kwargs):
        calls.append(kwargs)
        if len(calls) in bad_calls:
            return bad_turn
        return turn("read_file", {"path": "src/app/page.tsx"})

    with pytest.raises(RuntimeError, match="edit produced no source changes"):
        await run(provider)
    assert len(calls) == 6
    assert "build" not in executed
    assert workspace == initial
    assert {tool["name"] for tool in calls[4]["tools"]} == {"write_file", "edit_file"}


@pytest.mark.parametrize("path", [
    ".omnia/notes.md", "notes.md", "src/notes.md", "NOTES.MD", "docs/notes.RST",
])
def test_auxiliary_notes_are_not_finalization_source_delta(path):
    baseline = {"src/app/page.tsx": "same"}
    candidate = {**baseline, path: "Only notes"}
    verdict = unchanged_candidate_before_finalization(
        baseline_files=baseline, workspace_files=candidate,
        requires_source_change=True, message="done",
    )
    assert verdict is not None and verdict.failure == "edit produced no source changes"
    patch_verdict = validate_edit_source_change(
        requires_source_change=True, baseline_files=baseline, candidate_files={path: "Only notes"},
        exact_tree=False, message="done",
    )
    assert patch_verdict.failure == "edit produced no source changes"


@pytest.mark.parametrize("path", [
    "src/app/api/orders/route.ts", "drizzle/0001_orders.sql", "tests/orders.test.ts",
    "src/app/globals.css", ".omnia/cell.json", "package.json",
    "src/components/Guide.mdx", "src/components/Guide.MDX",
    "src/content/article.md", "src/content/article.MD", "content/article.rst",
])
def test_product_source_changes_still_reach_finalization(path):
    assert unchanged_candidate_before_finalization(
        baseline_files={path: "before"}, workspace_files={path: "after"},
        requires_source_change=True, message="done",
    ) is None
    assert validate_edit_source_change(
        requires_source_change=True, baseline_files={path: "before"},
        candidate_files={path: "after"}, exact_tree=False, message="done",
    ).failure is None


@pytest.mark.parametrize("path", ["src/app/old/page.tsx", "obsolete.txt"])
def test_existing_file_deletion_still_counts_as_delta(path):
    assert unchanged_candidate_before_finalization(
        baseline_files={path: ""}, workspace_files={}, requires_source_change=True, message="done",
    ) is None
    assert validate_edit_source_change(
        requires_source_change=True, baseline_files={path: ""}, candidate_files={path: ""},
        exact_tree=False, message="done",
    ).failure is None


@pytest.mark.parametrize("path", [
    ".omnia/diagnostics.json", "next-env.d.ts", "typescript.tsbuildinfo",
    "src/lib/omnia/integration-client.ts",
])
def test_auxiliary_or_managed_changes_cannot_satisfy_source_delta(path):
    baseline = {"src/app/page.tsx": "same", path: "before"}
    candidate = {**baseline, path: "after"}
    assert unchanged_candidate_before_finalization(
        baseline_files=baseline, workspace_files=candidate,
        requires_source_change=True, message="done",
    ) is not None
    assert validate_edit_source_change(
        requires_source_change=True, baseline_files=baseline, candidate_files=candidate,
        exact_tree=True, message="done",
    ).failure == "edit produced no source changes"


@pytest.mark.parametrize("path", ["src/app/api/orders/route.ts", "tests/orders.test.ts"])
async def test_real_source_edit_unlocks_tools_after_auxiliary_note(edit_workspace, path):
    run, _, _, executed = edit_workspace
    calls = []

    async def provider(*args, **kwargs):
        calls.append(kwargs)
        if len(calls) <= 4:
            return turn("read_file", {"path": "src/app/page.tsx"})
        if len(calls) == 5:
            return turn("write_file", {"path": ".omnia/notes.md", "content": "Only notes"})
        if len(calls) == 6:
            return turn("write_file", {"path": path, "content": "real implementation"})
        if len(calls) == 7:
            return turn("build", {})
        return turn("done", {"summary": "applied"})

    result, failure = await run(provider)
    assert result.done and failure is None
    assert result.files[path] == "real implementation"
    assert "build" in executed
    assert calls[5]["tool_choice"] == {"type": "any"}
    assert calls[6]["tool_choice"] is None


@pytest.mark.parametrize("bad_turn", [
    {"content": [], "stop_reason": "max_tokens"},
    turn("write_file", {"path": "src/app/page.tsx"}),
])
async def test_third_incomplete_response_retains_native_terminal_diagnosis(monkeypatch, bad_turn):
    calls = 0

    async def provider(*args, **kwargs):
        nonlocal calls
        calls += 1
        return bad_turn

    async def execute(action):
        pytest.fail("No action from an incomplete batch may execute")

    monkeypatch.setattr(agent_native, "_call_messages", provider)
    result = await agent_native.run_native_build(
        system="generic", task="Remove History", execute=execute,
        edit_source_changed=lambda: False, max_steps=24, max_segments=3,
    )
    assert calls == 3
    assert result.stop_reason == "error"
    assert "неполные команды" in result.summary
    assert not result.done and not result.needs_finalization
