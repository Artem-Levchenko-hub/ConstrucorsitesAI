"""A managed repair receives exact current source without buying more exploration."""

import json
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from yleum_api.services import agent_native
from yleum_api.services.agent_builder import AgentResult
from yleum_api.services.generation import agent_finalization, agent_runtime
from yleum_api.services.max_management_contract import build_management_contract

from .test_agent_native_truncation import turn
from .test_source_repair_context import full_design_task

ENTRY = "src/app/page.tsx"
MANIFEST = ".omnia/management.json"


def managed_files(source: str) -> dict[str, str]:
    return {MANIFEST: build_management_contract(["catalog"]).model_dump_json(), ENTRY: source}


def repair_arguments(files, *, execute=None, continuation=None):
    return dict(
        task="Repair the managed profile connection; preserve existing product and data.",
        prompt_text="Preserve the product",
        baseline=files,
        runtime=SimpleNamespace(handle=None, coordinator=None),
        ids=SimpleNamespace(user_id="u", project_id="p", run_id="r", assistant_message_id="m"),
        plan=SimpleNamespace(
            model="m", stack_guide="MAX PLATFORM CORE CONTRACT", skills=[], steps=8,
        ),
        operations=SimpleNamespace(
            execute=execute or AsyncMock(), emit=AsyncMock(), continuation=continuation,
        ),
        is_free=False,
        shell_enabled=False,
        edit_deadline=None,
    )


@pytest.fixture
def repair_settings(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://qa:qa@127.0.0.1/unused")
    monkeypatch.setenv("JWT_SECRET", "qa-unit-test-key-never-used-for-production")
    monkeypatch.setenv("USE_MAX_FINALIZATION_COORDINATOR", "true")
    agent_native.get_settings.cache_clear()
    yield
    agent_native.get_settings.cache_clear()


@pytest.fixture
def isolated_repair(monkeypatch, repair_settings):
    async def guard(runtime, ids, execute, completion):
        # This unit exercises source context and the real native loop, not runtime proof.
        return execute, lambda _files, _evidence: None

    monkeypatch.setattr(agent_runtime, "guard_native_source_contract", guard)


def source_from_task(task: str) -> dict:
    # One JSON document at the end keeps source bytes distinct from instructions.
    marker = "\nCURRENT_MANAGED_SOURCE_JSON\n"
    assert marker in task, "current source was never supplied to the repair model"
    return json.loads(task.split(marker, 1)[1])


@pytest.mark.parametrize("fallback", ["json", "code", "unchanged"])
async def test_missing_entry_repairs_same_draft_with_one_action_and_text_fallback(
    monkeypatch, isolated_repair, fallback,
):
    before = {
        "src/components/ration/BottomNav.tsx": "export default function Nav() { return null; }",
        "src/lib/ration/domain.ts": "export const dishCount = 10;",
        "src/app/ration.css": ".meal { display: grid; }",
        ".env": "MUST_NOT_SEND_TO_MODEL",
    }
    files = dict(before)
    page = (
        '"use client";\n'
        'import {useYleumAppProfile} from "@/lib/omnia/app-profile-client";\n'
        'export default function Page() {\n'
        '  const {profile} = useYleumAppProfile();\n'
        '  return <main>{profile?.app_name ?? "Рацион на месяц"}</main>;\n'
        '}\n'
    )
    calls, executed, checkpoints = [], [], []

    async def provider(*args, **kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            assert [tool["name"] for tool in kwargs["tools"]] == ["write_file"]
            context = str(args[2])
            assert "useYleumAppProfile" in context
            assert "export const dishCount = 10;" in context
            assert "MUST_NOT_SEND_TO_MODEL" not in context
            return turn(
                ("write_file", {"path": ENTRY, "content": page}),
                ("read_file", {"path": "src/lib/ration/domain.ts"}),
            )
        if len(calls) == 2:
            assert kwargs["tools"] == []
            return {"stop_reason": "end_turn", "content": [
                {"type": "text", "text": (
                    json.dumps({"content": page}) if fallback != "code" else "I'll write it next."
                )},
            ]}
        if len(calls) == 3 and fallback == "code":
            assert kwargs["tools"] == [] and "fenced TSX code block" in str(args[2])
            return {"stop_reason": "end_turn", "content": [
                {"type": "text", "text": "```tsx\n" + page + "```"},
            ]}
        return turn(("done", {"summary": "Проверено"}))

    async def execute(action):
        executed.append(action.name)
        if action.name == "write_file":
            assert action.path == ENTRY
            if fallback != "unchanged":
                files[ENTRY] = action.args["content"]
            return {"ok": True}
        assert action.name == "build"
        assert files[ENTRY] == page
        return {"ok": True}

    async def snapshot():
        return dict(files)

    async def checkpoint(result):
        checkpoints.append(result.files)

    async def guard(runtime, ids, execute, completion):
        return execute, lambda written, _evidence: None if written.get(ENTRY) == page else "no page"

    monkeypatch.setattr(agent_native, "_call_messages", provider)
    monkeypatch.setattr(agent_runtime, "guard_native_source_contract", guard)
    args = repair_arguments(dict(before), execute=execute)
    args["plan"].model = "claude-sonnet-5.5"
    args["operations"].snapshot_files = snapshot
    args["operations"].checkpoint = checkpoint
    result = await agent_finalization.run_native_source_repair(**args)
    assert all(call["run_id"] == "r" for call in calls)
    if fallback == "unchanged":
        assert not result.done and not result.needs_finalization
        assert not checkpoints and files == before
        assert executed == ["write_file"]
    else:
        assert result.done or result.needs_finalization
        assert executed == ["write_file", "build"]
        assert checkpoints == [{**before, ENTRY: page}]
        assert result.files == {ENTRY: page}


@pytest.mark.parametrize("saved_limit", [8192, 128_000])
async def test_directed_text_strategy_resumes_original_request(
    monkeypatch, isolated_repair, saved_limit,
):
    from yleum_api.services.generation.contracts import AgentPromptPlan, StackPrompt
    from yleum_api.services.generation.recovery_execution import GenerationYielded
    from yleum_api.services.generation.recovery_pipeline import PreparedAgentEntry

    task = full_design_task()
    files = {"src/lib/ration/domain.ts": "export const dishCount = 10;"}
    page = "export default function Page() { return <main>Рацион</main>; }"
    frames, effects, calls = [], [], []
    resume = False
    run_id = uuid4()
    entry = PreparedAgentEntry(
        ("Continue saved ration", True, False, False), None,
        StackPrompt("seed", None, None, None, "system", False),
        AgentPromptPlan("system", "brief", "claude-sonnet-5.5", None, 8, "", None, "", None,
                        False),
        {}, None, None, restored_files=dict(files),
    )

    async def recorded_provider(frame, invoke):
        frames.append(deepcopy(frame))
        if len(frames) == 2 and saved_limit == 8192:
            frames[-1]["value"]["request"]["max_tokens"] = saved_limit
        return await invoke()

    def restore_frame(kind):
        assert kind == "native"
        return deepcopy(frames[-1]["value"]) if resume else None

    async def model(*args, **kwargs):
        calls.append((deepcopy(args[2]), deepcopy(kwargs["tools"]), kwargs["max_tokens"]))
        if len(calls) == 1:
            return turn(("read_file", {"path": "src/lib/ration/domain.ts"}))
        if len(calls) == 2:
            raise GenerationYielded(run_id=run_id, checkpoint=None, attempt_seq=1,
                                   phase="waiting_dependency")
        if len(calls) == 3:
            return {"stop_reason": "end_turn", "content": [
                {"type": "text", "text": json.dumps({"content": page})},
            ]}
        return turn(("done", {"summary": "done"}))

    async def snapshot():
        return dict(files)

    async def execute(action):
        effects.append(action.name)
        if action.name == "write_file":
            files[action.path] = action.args["content"]
        return {"ok": True}

    monkeypatch.setattr(agent_native, "_call_messages", model)
    arguments = dict(
        system="MAX VERIFICATION OVERRIDE", task=task, execute=execute,
        model="claude-sonnet-5.5", run_id=run_id, directed_path=ENTRY, source_repair=True,
        portable_cell=True, initial_files=dict(files), max_steps=8, snapshot_files=snapshot,
        continuation=SimpleNamespace(provider=recorded_provider, restore_frame=restore_frame),
        completion_check=lambda written, _evidence: None if written.get(ENTRY) == page else "gap",
    )
    with pytest.raises(GenerationYielded):
        await agent_native._run_native_segment(**arguments)
    assert effects == []
    resume = True
    arguments["initial_files"] = PreparedAgentEntry.from_json(entry.to_json()).restored_files
    result = await agent_native._run_native_segment(**arguments)
    assert calls[1][1:] == ([], 128_000)
    assert calls[2] == (*calls[1][:2], saved_limit)
    assert task in calls[2][0][0]["content"]
    assert calls[3][2] == 128_000
    assert effects == ["write_file", "build"]
    assert result.done and result.files == {ENTRY: page}


async def test_missing_entry_limits_file_operation_and_completes_full_design_task(
    monkeypatch, isolated_repair,
):
    domain = "src/lib/ration/domain.ts"
    report = "src/app/api/report/route.ts"
    files = {domain: "export const dishCount = 10;"}
    page = ('import {dishCount} from "@/lib/ration/domain";\n'
            "export default function Page() { return <main>{dishCount}</main>; }\n")
    report_source = "export const GET = () => new Response('report');"
    calls, effects, checkpoints = [], [], []
    task = full_design_task() + "\nUse the saved meal domain. Complete the requested report."

    async def model(*args, **kwargs):
        calls.append(kwargs)
        seq = len(calls)
        if seq == 1:
            return turn(("read_file", {"path": domain}))
        if seq in {2, 3}:
            return {"stop_reason": "max_tokens", "content": [{"type": "text", "text": "partial"}]}
        if seq == 4:
            context = args[2][0]["content"]
            assert kwargs["tools"] == []
            assert task in context and files[domain] in context
            return {"stop_reason": "end_turn", "content": [
                {"type": "text", "text": "```tsx\n" + page + "```"},
            ]}
        assert "Complete the requested report." in str(args[2])
        assert files[ENTRY] == page
        if seq == 5:  # The first screen alone must not satisfy the original task.
            return turn(("done", {"summary": "Too early"}))
        if seq == 6:
            return turn(("write_file", {"path": report, "content": report_source}))
        return turn(("build", {}), ("done", {"summary": "Product and report verified"}))

    async def snapshot():
        return dict(files)

    async def execute(action):
        effects.append((action.name, action.path))
        if action.name == "write_file":
            files[action.path] = action.args["content"]
        return {"ok": True}

    async def checkpoint(result):
        checkpoints.append(result.files)

    from yleum_api.services.generation.agent_generation import checkpoint_source_actions

    monkeypatch.setattr(agent_native, "_call_messages", model)
    result = await agent_native._run_native_segment(
        system="MAX VERIFICATION OVERRIDE", task=task, model="claude-sonnet-5.5",
        run_id="r", portable_cell=True, source_repair=True, max_steps=9,
        initial_files=dict(files), directed_path=ENTRY, snapshot_files=snapshot,
        execute=checkpoint_source_actions(
            execute=execute, snapshot=snapshot, checkpoint=checkpoint,
        ),
        completion_check=lambda written, _evidence: (
            None if written.get(report) == report_source else "Report missing"
        ),
    )
    assert result.done and result.summary == "Product and report verified"
    assert all(call["run_id"] == "r" for call in calls)
    assert effects == [
        ("write_file", ENTRY), ("build", ""), ("write_file", report), ("build", ""),
    ]
    assert checkpoints == [{domain: files[domain], ENTRY: page}, files]


async def test_recovery_uses_full_product_task_and_current_source_not_attached_old_export(
    monkeypatch, repair_settings,
):
    from yleum_api.services.generation.source_repair_context import with_managed_source_context

    monkeypatch.setattr(agent_native.get_settings(), "use_max_finalization_coordinator", False)
    task = full_design_task()
    files = managed_files("export const version = 'old snapshot';")
    attached = with_managed_source_context(task, files)
    calls, effects = [], []

    async def model(_client, _url, convo, _system, **kwargs):
        calls.append(deepcopy(convo))
        if len(calls) == 1:
            return turn(("edit_file", {
                "path": ENTRY, "search": "old snapshot", "replace": "current snapshot",
            }))
        if len(calls) == 2:
            return turn(("write_file", {"path": ENTRY, "content": "NEVER_APPLY"}),
                        stop="max_tokens")
        return turn(("build", {}), ("done", {}))

    async def execute(action):
        effects.append(action.name)
        if action.name == "edit_file":
            files[ENTRY] = files[ENTRY].replace(action.args["search"], action.args["replace"])
            return {"ok": True, "content": files[ENTRY]}
        return {"ok": True}

    async def snapshot():
        return dict(files)

    monkeypatch.setattr(agent_native, "_call_messages", model)
    result = await agent_native._run_native_segment(
        system="MAX VERIFICATION OVERRIDE", task=attached, execute=execute,
        initial_files=dict(files), source_repair=True, snapshot_files=snapshot, max_steps=5,
    )
    assert result.done
    assert len(calls) == 3 and effects == ["edit_file", "build"]
    assert attached in calls[0][0]["content"]
    context = calls[2][0]["content"]
    assert len(calls[2]) == 1 and task in context
    assert "old snapshot" not in context and "current snapshot" in context
    assert "NEVER_APPLY" not in str(result.transcript)


async def test_late_exact_edit_uses_complete_loaded_source_without_any_read(
    monkeypatch, isolated_repair,
):
    original = "// Keep this existing feature.\n" * 800 + (
        'export default function Page() { return <h1>ACTUAL_LATE_TITLE</h1>; }\r\n'
    )
    assert original.index("ACTUAL_LATE_TITLE") > 16_000
    files = managed_files(original)
    before = deepcopy(files)
    calls, executed = [], []

    async def provider(*args, **kwargs):
        calls.append(deepcopy(args[2]))
        if len(calls) == 1:
            payload = source_from_task(args[2][0]["content"])
            assert payload == {
                "path": ENTRY, "sha256": sha256(original.encode()).hexdigest(),
                "utf8_bytes": len(original.encode()), "line_count": len(original.splitlines()),
                "complete": True, "content": original,
            }
            return turn(("edit_file", {
                "path": ENTRY, "search": "ACTUAL_LATE_TITLE", "replace": "CURRENT_PROFILE_TITLE",
            }))
        return turn(("done", {"summary": "candidate, not published"}))

    async def execute(action):
        executed.append(action.name)
        if action.name == "edit_file":
            assert action.path == ENTRY
            assert files[ENTRY].count(action.args["search"]) == 1
            files[ENTRY] = files[ENTRY].replace(action.args["search"], action.args["replace"])
            return {"ok": True, "content": files[ENTRY]}
        assert action.name == "build"
        return {"ok": True}

    monkeypatch.setattr(agent_native, "_call_messages", provider)
    result = await agent_finalization.run_native_source_repair(
        **repair_arguments(files, execute=execute),
    )
    assert executed == ["edit_file", "build"] and len(calls) == 2
    assert result.done and result.files == {ENTRY: files[ENTRY]}
    assert files[MANIFEST] == before[MANIFEST]
    assert files[ENTRY] == original.replace("ACTUAL_LATE_TITLE", "CURRENT_PROFILE_TITLE")


@pytest.mark.parametrize("case", ["removed", "present", "unavailable", "no_op"])
async def test_source_repair_done_checks_the_current_tree_after_shell_deletion(
    monkeypatch, repair_settings, case,
):
    from tests.test_max_generation_contract import _portable_crm_files
    from tests.test_max_management_generation import connection_example
    from yleum_api.services import max_generation_contract as contract
    from yleum_api.services.max_generation_contract import max_source_completion_gap

    before_page = (
        'import {prepare} from "@/app/shared";\n'
        + connection_example(("catalog",)).replace(
            "const {profile}", "prepare();\nconst {profile}", 1,
        )
    )
    page = before_page.replace("</h1>", " — каталог</h1>", 1)
    removed = "src/app/shared.tsx"
    clean = {
        **managed_files(page),
        ".omnia/cell.json": _portable_crm_files()[".omnia/cell.json"],
        "src/app/shared.ts": "export function prepare() { return true; }",
    }
    files = {
        **clean,
        ENTRY: before_page,
        removed: 'export function prepare() { throw new Error("retired source"); }',
    }
    brief = "Remove the obsolete src/app/shared.tsx and preserve the product"

    def completion_gap(prompt, current, *, portable=False):
        gap = max_source_completion_gap(prompt, current, portable=portable)
        if gap:
            return gap
        # An explicit removal requirement remains independent of the optional
        # SDK interpreter. This test exercises current-tree reconciliation.
        return f"Remove obsolete {removed}" if removed in current else None

    monkeypatch.setattr(contract, "max_source_completion_gap", completion_gap)
    assert max_source_completion_gap(brief, clean, portable=True) is None
    ghost_gap = completion_gap(brief, files, portable=True)
    assert ghost_gap and removed in ghost_gap

    calls = []
    commands = []
    snapshot_unavailable = False

    async def snapshot():
        if snapshot_unavailable:
            raise RuntimeError("snapshot unavailable")
        return dict(files)

    async def execute(action):
        nonlocal snapshot_unavailable
        commands.append(action.name)
        if action.name == "write_file":
            files[action.path] = action.args["content"]
        elif action.name == "bash" and case != "present":
            files.pop(removed)
        elif action.name == "build" and case == "unavailable" and removed not in files:
            snapshot_unavailable = True
        return {"ok": True, "environment_mutated": action.name == "bash"}

    async def provider(*args, **kwargs):
        calls.append(deepcopy(args[2]))
        match len(calls):
            case 1:
                return turn(("write_file", {"path": ENTRY, "content": page}))
            case 3:
                assert removed in str(calls[-1]) and "Not done yet" in str(calls[-1])
                return turn(("bash", {"cmd": "Remove the obsolete duplicate module"}))
            case _:
                return turn(("build", {}), ("done", {"summary": "Проверено"}))

    monkeypatch.setattr(agent_native, "_call_messages", provider)
    args = repair_arguments(dict(files), execute=execute)
    args["runtime"].handle = SimpleNamespace(snapshot_files=snapshot)
    args["plan"].steps = 4
    args["shell_enabled"] = True
    result = await agent_finalization.run_native_source_repair(
        **args, require_edit_from=clean if case == "no_op" else None,
    )

    assert commands == ["write_file", "build", "build", "bash", "build"]
    assert len(calls) == 4
    assert result.done is (case == "removed")
    assert set(files) == (set(clean) | ({removed} if case == "present" else set()))
    if case == "present":
        assert removed in result.summary
    elif case == "unavailable":
        assert "Product acceptance check failed" in result.summary
    elif case == "no_op":
        assert "Apply the requested edit" in result.summary


@pytest.mark.parametrize("source", ["x" * 70_000, "я" * 40_000], ids=["ascii", "utf8"])
async def test_context_overflow_stops_before_provider_or_executor(monkeypatch, source):
    native, execute = AsyncMock(), AsyncMock()
    monkeypatch.setattr(agent_native, "run_native_build", native)
    files = managed_files(source)
    before = deepcopy(files)
    with pytest.raises(RuntimeError, match="SOURCE_REPAIR_CONTEXT_UNAVAILABLE"):
        await agent_finalization.run_native_source_repair(
            **repair_arguments(files, execute=execute),
        )
    native.assert_not_called()
    execute.assert_not_called()
    assert files == before


async def test_resume_retains_enriched_task_and_original_budget(monkeypatch, isolated_repair):
    from yleum_api.services.generation.recovery_contract import (
        CheckpointRef,
        ExecutionCursor,
        IncompleteResponseBudget,
    )
    from yleum_api.services.generation.recovery_execution import ExecutionDriver, GenerationYielded

    cursor = ExecutionCursor(
        stage="finalization", substep="checkpoint_ready", physical_state="active",
        execution_context_ref=CheckpointRef(object_key="owned/context", sha256="a" * 64),
    )
    driver = SimpleNamespace(context={"completed": {}}, state=SimpleNamespace(execution=cursor))
    driver.soft_edit_deadline = ExecutionDriver.soft_edit_deadline.__get__(driver)
    saves = []

    async def save(execution, *, context, **kwargs):
        saves.append(deepcopy(context))
        driver.state.execution, driver.context = execution, context

    driver.save = save
    files = managed_files('export default () => <h1>ORIGINAL_CONTEXT</h1>;')
    deadline = datetime.now(UTC) + timedelta(minutes=20)
    kwargs = repair_arguments(files, continuation=driver)
    kwargs["edit_deadline"] = deadline
    accounting = SimpleNamespace(budget=IncompleteResponseBudget(total=1, since_progress=1))
    kwargs["operations"].response_recovery = accounting
    calls = []

    async def native(**request):
        calls.append(request)
        assert source_from_task(request["task"])["content"] == files[ENTRY]
        assert saves[-1]["completed"]["source_repair"]["task"] == request["task"]
        if len(calls) == 1:
            raise GenerationYielded(
                run_id=uuid4(), checkpoint=None, attempt_seq=0, phase="waiting_dependency",
            )
        accounting.budget = IncompleteResponseBudget(total=2, since_progress=2)
        return AgentResult(False, "candidate needs coordinator proof", {}, 1)

    monkeypatch.setattr(agent_native, "run_native_build", native)
    with pytest.raises(GenerationYielded):
        await agent_finalization.run_native_source_repair(**kwargs)
    # Even an oversized later observation must not rebuild or reject an owned paid request.
    changed = {
        **kwargs, "task": "DO NOT SUBSTITUTE THIS TASK", "baseline": managed_files("я" * 80_000),
        "edit_deadline": deadline + timedelta(minutes=10),
    }
    result = await agent_finalization.run_native_source_repair(**changed)
    assert calls[0]["task"] == calls[1]["task"]
    assert calls[1]["initial_files"] == files and calls[1]["edit_deadline"] == deadline
    assert driver.state.execution.budget.repair_calls == 1
    assert accounting.budget == IncompleteResponseBudget(total=2, since_progress=2)
    assert await agent_finalization.run_native_source_repair(**changed) == result
    assert len(calls) == 2


@pytest.mark.parametrize("unsafe", [
    {".env": "DATABASE_PASSWORD=PRIVATE_VALUE"},
    {"src/lib/omnia/business-client.ts": "SDK_PRIVATE_NOT_CONTEXT"},
    {"src/lib/omnia/app-profile-client.ts": "PROFILE_SDK_NOT_CONTEXT"},
    {"src/app/layout.tsx": "PROTECTED_LAYOUT_NOT_CONTEXT"},
    {"src/app/../page.tsx": "TRAVERSAL_NOT_CONTEXT"},
    {"src/components/.secrets.tsx": "HIDDEN_NOT_CONTEXT"},
])
async def test_context_never_expands_to_protected_or_secret_files(monkeypatch, unsafe):
    files = {**managed_files("export default () => <main>PUBLIC_PRODUCT</main>;"), **unsafe}
    native = AsyncMock(return_value=AgentResult(False, "candidate", {}, 0))
    monkeypatch.setattr(agent_native, "run_native_build", native)
    await agent_finalization.run_native_source_repair(**repair_arguments(files))
    task = native.call_args.kwargs["task"]
    assert source_from_task(task)["path"] == ENTRY
    assert all(content not in task for content in unsafe.values())


@pytest.mark.parametrize("previous_repairs, eligible, expected", [
    (0, True, "claude-sonnet-5.5"),
    (1, False, "claude-sonnet-5.5"),
    (1, True, "kimi-k3.1-pro"),
    (2, True, "claude-sonnet-5.5"),
])
async def test_pilot_uses_kimi_only_for_one_targeted_repair_after_sonnet(
    monkeypatch, previous_repairs, eligible, expected,
):
    from yleum_api.services.generation.recovery_contract import (
        CheckpointRef,
        ExecutionBudget,
        ExecutionCursor,
    )
    from yleum_api.services.generation.source_error_classification import SourceFailure

    project_id = uuid4()
    cursor = ExecutionCursor(
        stage="finalization", substep="checkpoint_ready", physical_state="active",
        budget=ExecutionBudget(repair_calls=previous_repairs),
        execution_context_ref=CheckpointRef(object_key="owned/context", sha256="a" * 64),
    )
    driver = SimpleNamespace(
        context={"completed": {}}, state=SimpleNamespace(execution=cursor),
        soft_edit_deadline=lambda deadline: deadline,
    )

    async def save(execution, *, context, **_):
        driver.state.execution, driver.context = execution, context

    driver.save = save
    files = managed_files("export default () => <main>Draft</main>;")
    args = repair_arguments(files, continuation=driver)
    args["ids"].project_id = project_id
    args["plan"].model = "claude-sonnet-5.5"
    args["source_failure"] = SourceFailure("type_error", ENTRY, (ENTRY,))
    monkeypatch.setattr(agent_finalization, "get_settings", lambda: SimpleNamespace(
        max_kimi_repair_project_ids=str(project_id if eligible else uuid4()),
    ))
    native = AsyncMock(return_value=AgentResult(True, "candidate", {}, 1))
    monkeypatch.setattr(agent_native, "run_native_build", native)

    await agent_finalization.run_native_source_repair(**args)

    assert native.await_args.kwargs["model"] == expected
    if expected == "kimi-k3.1-pro":
        assert native.await_args.kwargs["kimi_repair"] is True
        assert driver.context["completed"]["source_repair"]["repair_model"] == expected


@pytest.mark.parametrize("source", [
    'const leaked = "sk-0123456789abcdefghijklmnop";',
    "-----BEGIN PRIVATE KEY-----\nNEVER_INCLUDE\n-----END PRIVATE KEY-----",
], ids=["provider-token", "private-key"])
async def test_secret_in_entry_is_not_redacted_into_an_inexact_edit(monkeypatch, source):
    native = AsyncMock()
    monkeypatch.setattr(agent_native, "run_native_build", native)
    with pytest.raises(RuntimeError, match="SOURCE_REPAIR_CONTEXT_UNAVAILABLE"):
        await agent_finalization.run_native_source_repair(**repair_arguments(managed_files(source)))
    native.assert_not_called()


@pytest.mark.parametrize("diagnostic", ["advisory", "compiler"])
async def test_repair_reaches_compiler_even_with_a_previous_source_advisory(
    monkeypatch, isolated_repair, diagnostic,
):
    from yleum_api.services import max_management_generation as management
    from yleum_api.services.agent_builder import Action

    from .test_max_management_generation import connection_example, connection_files

    modules = ("catalog", "leads")
    source = connection_example(modules)
    before = connection_files(source.replace("{profile?.app_name}", "Рацион"), modules)
    after = connection_files(source, modules)
    analyze = management.management_source_advisory
    initial_gap = analyze(before)
    assert initial_gap is not None and "useYleumAppProfile" in initial_gap
    assert analyze(after) is None
    files = dict(before)
    physical = []
    def unavailable_interpreter(*_args, **_kwargs):
        raise AssertionError("generation must not invoke the optional AST interpreter")

    monkeypatch.setattr(management, "_Connections", unavailable_interpreter)
    compiler_failure = {"ok": False, "detail": "TS2300 duplicate identifier"}

    async def snapshot():
        return dict(files)

    async def execute(action):
        physical.append(action.name)
        if action.name == "write_file":
            files[ENTRY] = action.args["content"]
        if action.name == "build" and files[ENTRY] == before[ENTRY]:
            return compiler_failure
        return {"ok": True, "detail": "physical check"}

    async def native(**request):
        assert request["run_id"] == "r"
        build = Action("build", {}, "")
        first = await request["execute"](build)
        assert first == compiler_failure
        await request["execute"](Action("write_file", {"path": ENTRY, "content": source}))
        assert await request["execute"](build) == {"ok": True, "detail": "physical check"}
        return AgentResult(False, "candidate still needs physical finalization", {ENTRY: source}, 3)

    monkeypatch.setattr(agent_native, "run_native_build", native)
    args = repair_arguments(before, execute=execute)
    args["runtime"].coordinator = object()
    args["runtime"].handle = SimpleNamespace(snapshot_files=snapshot, is_portable=lambda: True)
    args["operations"].snapshot_files = snapshot
    feedback = "TS2300 duplicate identifier" if diagnostic == "compiler" else str(initial_gap)
    result = await agent_finalization.run_native_source_repair(**args, feedback=feedback)
    assert result.files == {ENTRY: source}
    assert physical == ["build", "write_file", "build"]


async def test_legacy_repair_request_is_unchanged(monkeypatch):
    native = AsyncMock(return_value=AgentResult(False, "candidate", {}, 0))
    monkeypatch.setattr(agent_native, "run_native_build", native)
    args = repair_arguments({ENTRY: "existing legacy source"})
    await agent_finalization.run_native_source_repair(**args)
    assert native.call_args.kwargs["task"] == args["task"]


def test_context_limit_counts_complete_utf8_document_including_metadata(monkeypatch):
    from yleum_api.services.generation import source_repair_context

    task = "original instruction"
    files = managed_files('export default () => <main>Керамика 😀</main>;\r\n')
    enriched = source_repair_context.with_managed_source_context(task, files)
    appended = enriched[len(task):]
    size = len(appended.encode("utf-8"))
    assert size > len(appended)
    monkeypatch.setattr(source_repair_context, "MAX_REPAIR_CONTEXT_BYTES", size)
    assert source_repair_context.with_managed_source_context(task, files) == enriched
    monkeypatch.setattr(source_repair_context, "MAX_REPAIR_CONTEXT_BYTES", size - 1)
    with pytest.raises(source_repair_context.SourceRepairContextError):
        source_repair_context.with_managed_source_context(task, files)


@pytest.mark.parametrize("damage", ["json", "hash", "unicode", "protected-entry"])
async def test_invalid_context_stops_before_dispatch(monkeypatch, damage):
    from yleum_api.services.generation import source_repair_context

    files = managed_files("export default () => <main>DO_NOT_LEAK_BODY</main>;")
    if damage == "json":
        files[MANIFEST] = "DO_NOT_LEAK_INVALID_MANIFEST"
    elif damage == "hash":
        manifest = json.loads(files[MANIFEST])
        manifest["contract_hash"] = "f" * 64
        files[MANIFEST] = json.dumps(manifest)
    elif damage == "unicode":
        files[ENTRY] = "\ud800"
    else:
        monkeypatch.setattr(
            source_repair_context, "MAX_SECURITY_LOCKED_FILES", frozenset({ENTRY}),
        )
    native = AsyncMock()
    monkeypatch.setattr(agent_native, "run_native_build", native)
    with pytest.raises(source_repair_context.SourceRepairContextError) as failure:
        await agent_finalization.run_native_source_repair(**repair_arguments(files))
    assert "DO_NOT_LEAK" not in str(failure.value)
    native.assert_not_called()


async def test_missing_entry_does_not_invent_or_quote_an_alias(monkeypatch):
    files = {MANIFEST: build_management_contract([]).model_dump_json(),
             "src/app/../page.tsx": "MUST_NOT_QUOTE_ALIAS"}
    native = AsyncMock(return_value=AgentResult(False, "candidate", {}, 0))
    monkeypatch.setattr(agent_native, "run_native_build", native)
    args = repair_arguments(files)
    await agent_finalization.run_native_source_repair(**args)
    assert native.call_args.kwargs["task"] == args["task"]
    assert native.call_args.kwargs["initial_files"] == files


async def test_current_entry_context_never_expands_targeted_write_authority(
    monkeypatch, isolated_repair,
):
    from yleum_api.services.agent_builder import Action
    from yleum_api.services.generation.source_error_classification import SourceFailure

    files = managed_files("export default () => <main>EXISTING_PRODUCT</main>;")
    files["src/app/only-authorized.ts"] = "export const value = 1;"
    execute = AsyncMock()

    async def native(**kwargs):
        assert source_from_task(kwargs["task"])["path"] == ENTRY
        denied = await kwargs["execute"](Action("edit_file", {
            "path": ENTRY, "search": "EXISTING_PRODUCT", "replace": "UNAUTHORIZED_CHANGE",
        }))
        assert denied == {"ok": False, "detail": "Source repair path is not authorized."}
        return AgentResult(False, "candidate still needs an authorized repair", {}, 1)

    monkeypatch.setattr(agent_native, "run_native_build", native)
    await agent_finalization.run_native_source_repair(
        **repair_arguments(files, execute=execute),
        source_failure=SourceFailure(
            kind="type_error", importer="src/app/only-authorized.ts",
            allowed_paths=("src/app/only-authorized.ts",),
        ),
    )
    execute.assert_not_called()
