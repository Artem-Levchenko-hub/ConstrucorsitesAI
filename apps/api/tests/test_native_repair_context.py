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
def isolated_repair(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://qa:qa@127.0.0.1/unused")
    monkeypatch.setenv("JWT_SECRET", "qa-unit-test-key-never-used-for-production")
    monkeypatch.setenv("USE_MAX_FINALIZATION_COORDINATOR", "true")
    agent_native.get_settings.cache_clear()

    async def guard(runtime, ids, execute, completion):
        # This unit exercises source context and the real native loop, not runtime proof.
        return execute, lambda _files, _evidence: None

    monkeypatch.setattr(agent_runtime, "guard_native_source_contract", guard)
    yield
    agent_native.get_settings.cache_clear()


def source_from_task(task: str) -> dict:
    # One JSON document at the end keeps source bytes distinct from instructions.
    marker = "\nCURRENT_MANAGED_SOURCE_JSON\n"
    assert marker in task, "current source was never supplied to the repair model"
    return json.loads(task.split(marker, 1)[1])


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
