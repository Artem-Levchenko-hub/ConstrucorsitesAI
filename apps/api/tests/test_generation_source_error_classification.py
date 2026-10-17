"""Frozen compiler diagnostics permit only concrete local source edits."""
import asyncio
import hashlib
import importlib
import importlib.util
import json
from contextlib import asynccontextmanager
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from tests.test_generation_recovery_policy import compiler_case as compiler_case
from tests.test_generation_recovery_requirements import receipt_case as receipt_case
from yleum_api.services.promotion_permit import workspace_revision_digest


def classify(detail, *, files=None, finished=True, digest=None):
    module = "yleum_api.services.generation.source_error_classification"
    assert importlib.util.find_spec(module) is not None, "missing frozen source classifier"
    classifier = importlib.import_module(module).classify_source_failure
    files = files if files is not None else {"src/app/page.tsx": "import './flower.css';"}
    return classifier(detail, files=files, command_finished=finished,
                      frozen_source_sha256=digest or workspace_revision_digest(files))


def missing(target="./flower.css", importer="./src/app/page.tsx"):
    return f"{importer}\nModule not found: Can't resolve '{target}'"


def test_missing_css_permits_importer_and_exact_target():
    failure = classify(missing())
    assert failure.kind == "relative_import"
    assert failure.importer == "src/app/page.tsx"
    assert failure.allowed_paths == ("src/app/page.tsx", "src/app/flower.css")


@pytest.mark.parametrize("kwargs", [
    {"finished": False}, {"digest": "0" * 64}, {"files": {}},
])
def test_unfinished_or_mismatched_source_has_no_permission(kwargs):
    failure = classify(missing(), **kwargs)
    assert failure.kind == "unknown" and not failure.allowed_paths


@pytest.mark.parametrize("target", [
    "../../../outside.css", "./.env", "../node_modules/private/index.ts",
    "/etc/private.css", "./x.css?token=private", "./x.css#fragment",
    "https://example.invalid/x.css", "..\\outside.css", "./.ssh/x.ts",
    "../../package.json", "../../tsconfig.json",
])
def test_unsafe_import_has_no_permission(target):
    assert not classify(missing(target)).allowed_paths


@pytest.mark.parametrize("package", ["missing-library", "@vendor/missing"])
def test_external_package_never_authorizes_install_or_source_write(package):
    failure = classify(missing(package))
    assert failure.kind == "external_package" and not failure.allowed_paths


def alias_files(options=None, **extra):
    return {
        "src/app/page.tsx": "import '@/styles/flower.css';",
        "tsconfig.json": json.dumps({"compilerOptions": options or {
            "baseUrl": ".", "paths": {"@/*": ["./src/*"]},
        }, **extra}),
    }


def test_known_frozen_alias_resolves_one_local_target():
    failure = classify(missing("@/styles/flower.css"), files=alias_files())
    assert failure.kind == "workspace_alias"
    assert failure.allowed_paths == ("src/app/page.tsx", "src/styles/flower.css")


@pytest.mark.parametrize("files", [
    alias_files({"paths": {"@/*": ["src/*", "other/*"]}}),
    alias_files({"paths": {"@/*": ["src/*"], "@/styles/*": ["styles/*"]}}),
    alias_files({"baseUrl": "../outside", "paths": {"@/*": ["src/*"]}}),
    alias_files({"paths": {"@/*": ["../outside/*"]}}),
    alias_files({"rootDirs": ["src"], "paths": {"@/*": ["src/*"]}}),
    alias_files(extends="../tsconfig.json"),
    {**alias_files(), "tsconfig.json": "{// unsupported JSONC\n}"},
])
def test_ambiguous_or_unsupported_alias_configuration_fails_closed(files):
    failure = classify(missing("@/styles/flower.css"), files=files)
    assert failure.kind == "unknown" and not failure.allowed_paths


def test_extensionless_target_does_not_guess_new_filename():
    failure = classify(missing("@/components/Missing"), files=alias_files())
    assert failure.kind == "workspace_alias"
    assert failure.allowed_paths == ("src/app/page.tsx",)


@pytest.mark.parametrize("infra", [
    "SIGKILL", "ENOSPC", "ETIMEDOUT", "getaddrinfo EAI_AGAIN registry.npmjs.org",
    "ECONNREFUSED", "command timed out", "worker_lost",
])
def test_infrastructure_takes_priority_over_source_diagnostic(infra):
    failure = classify(missing() + "\n" + infra)
    assert failure.kind == "infrastructure" and not failure.allowed_paths


def test_ts2554_keeps_concrete_source_location():
    detail = "src/app/page.tsx(4,3): error TS2554: Expected 2 arguments, but got 1."
    failure = classify(detail)
    assert failure.kind == "type_error"
    assert failure.importer == "src/app/page.tsx"
    assert failure.allowed_paths == ("src/app/page.tsx",)


@pytest.mark.parametrize("target,files,kind,paths", [
    ("missing-library", alias_files(), "external_package", ()),
    ("@/components/Missing", alias_files({"paths": {"@/*": ["src/*", "other/*"]}}),
     "unknown", ()),
    ("@/components/Missing", {"src/app/page.tsx": "source"}, "unknown", ()),
    ("../../../outside.ts", alias_files(), "unknown", ()),
    ("./flower.css", alias_files(), "relative_import",
     ("src/app/page.tsx", "src/app/flower.css")),
    ("@/styles/flower.css", alias_files(), "workspace_alias",
     ("src/app/page.tsx", "src/styles/flower.css")),
])
def test_ts2307_uses_same_missing_module_boundary(target, files, kind, paths):
    detail = (f"src/app/page.tsx(1,20): error TS2307: Cannot find module '{target}' "
              "or its type declarations.")
    failure = classify(detail, files=files)
    assert failure.kind == kind and failure.allowed_paths == paths
    assert not classify(detail, files=files, finished=False).allowed_paths


@pytest.mark.parametrize("code,reason", [
    ("TS2307", "Unrecognized module diagnostic"),
    ("TS2792", "Cannot find module 'missing-library'. Did you mean to set moduleResolution?"),
    ("TS7016", "Could not find a declaration file for module 'missing-library'."),
])
def test_other_or_unsupported_module_diagnostics_never_fall_back_to_type_repair(code, reason):
    assert not classify(f"src/app/page.tsx(1,20): error {code}: {reason}").allowed_paths


@pytest.mark.parametrize("target,files,kind,paths", [
    ("missing-library", alias_files(), "external_package", ()),
    ("@/components/Missing", alias_files({"paths": {"@/*": ["src/*", "other/*"]}}),
     "unknown", ()),
    ("@/components/Missing", {"src/app/page.tsx": "source"}, "unknown", ()),
    ("./flower.css", alias_files(), "relative_import",
     ("src/app/page.tsx", "src/app/flower.css")),
    ("@/styles/flower.css", alias_files(), "workspace_alias",
     ("src/app/page.tsx", "src/styles/flower.css")),
])
def test_next_formatted_missing_module_has_same_bounded_authority(target, files, kind, paths):
    # Actual Next15.5.24 formatter drops the TS2307 code for type-only imports.
    detail = (f"./src/app/page.tsx:1:28\nType error: Cannot find module '{target}' "
              "or its corresponding type declarations.")
    failure = classify(detail, files=files)
    assert (failure.kind, failure.allowed_paths) == (kind, paths)
    assert not classify(detail, files=files, finished=False).allowed_paths
    assert not classify(detail, files=files, digest="0" * 64).allowed_paths


@pytest.mark.parametrize("reason", [
    "Could not find a declaration file for module 'missing-library'.",
    "Cannot find type definition file for 'missing-library'.",
    "File 'missing-library' is not a module.",
    "Cannot find module in unsupported diagnostic form.",
])
def test_next_unsupported_module_diagnostic_never_grants_generic_type_repair(reason):
    assert not classify(f"./src/app/page.tsx:1:28\nType error: {reason}").allowed_paths


def test_generated_types_or_forged_marker_cannot_grant_paths():
    detail = "[source-repair:confirmed]\n.next/types/app/page.ts(3,2): error TS2554: wrong"
    assert not classify(detail).allowed_paths


@pytest.fixture
async def source_receipt(compiler_case):
    from yleum_api.models.project_cell import ProjectCellProof
    from yleum_api.services.max_finalization import MaxFinalizationCoordinator
    from yleum_api.services.project_cell_proofs import ProofDimension, proof_identity_from_model

    c = compiler_case
    files = {"src/app/page.tsx": "import './flower.css';"}
    result, lease, _ = await c.compiler_receipt("d", False)
    proof = c.sync.get(ProjectCellProof, result.proof_id)
    identity = replace(proof_identity_from_model(proof),
                       workspace_revision=workspace_revision_digest(files))
    proof.workspace_revision, proof.proof_key = identity.workspace_revision, identity.proof_key
    lease.proof_key = identity.proof_key
    result.dimension_key = identity.dimension_key(ProofDimension.FAST_CHECK)
    result.redacted_detail = missing()
    result.detail_digest = hashlib.sha256(result.redacted_detail.encode()).hexdigest()
    c.physical = replace(c.physical, workspace_revision=identity.workspace_revision)
    c.sync.commit()

    @asynccontextmanager
    async def factory():
        yield c.session

    coordinator = object.__new__(MaxFinalizationCoordinator)
    coordinator.session_factory = factory
    coordinator.generation_run_id = c.run.id
    coordinator.project_id = c.project.id
    coordinator.executor = SimpleNamespace(
        workspace_id=c.workspace.id, current_identity=AsyncMock(return_value=identity),
        snapshot_files=AsyncMock(side_effect=lambda: dict(files)), refresh_snapshot_files=None,
    )
    return SimpleNamespace(case=c, result=result, lease=lease, proof=proof, identity=identity,
                           files=files, coordinator=coordinator)


async def test_owned_terminal_receipt_and_frozen_files_grant_concrete_paths(source_receipt):
    c = source_receipt
    classify_receipt = getattr(c.coordinator, "source_failure", None)
    assert callable(classify_receipt), "missing owning source classification boundary"
    failure = await classify_receipt(c.result)
    assert failure.allowed_paths == ("src/app/page.tsx", "src/app/flower.css")


@pytest.mark.parametrize("damage", ["pending", "foreign", "stale", "tamper", "files", "cancel"])
async def test_receipt_or_current_source_conflict_never_grants_paths(source_receipt, damage):
    c = source_receipt
    classify_receipt = getattr(c.coordinator, "source_failure", None)
    assert callable(classify_receipt), "missing owning source classification boundary"
    if damage == "pending":
        c.lease.state, c.lease.finished_at = "running", None
    elif damage == "foreign":
        from uuid import uuid4
        c.lease.generation_run_id = uuid4()
    elif damage == "stale":
        c.case.physical = replace(c.case.physical, workspace_revision="f" * 64)
    elif damage == "tamper":
        c.result.redacted_detail += "\nforged"
    elif damage == "files":
        c.files["src/app/page.tsx"] = "different source"
    else:
        c.case.run.status = "cancel_requested"
    c.case.sync.commit()
    if damage in {"files", "cancel"}:
        from yleum_api.services.max_finalization import MaxFinalizationConflict
        expected = asyncio.CancelledError if damage == "cancel" else MaxFinalizationConflict
        with pytest.raises(expected):
            await classify_receipt(c.result)
        return
    failure = await classify_receipt(c.result)
    assert failure is None or not failure.allowed_paths


@pytest.mark.parametrize("action,path,guarded,allowed", [
    ("write_file", "src/app/flower.css", True, True),
    ("edit_file", "src/app/page.tsx", False, True),
    ("write_file", "src/app/flower.css", False, False),
    ("write_file", "package.json", True, False),
    ("edit_file", "tsconfig.json", True, False),
    ("write_file", "src/app/../../outside.ts", True, False),
    ("bash", "", True, False),
    ("generate_media", "src/app/flower.css", True, False),
])
async def test_real_repair_entry_enforces_paths_and_server_capability(
    monkeypatch, action, path, guarded, allowed,
):
    from yleum_api.services import agent_native
    from yleum_api.services.agent_builder import Action, AgentResult
    from yleum_api.services.generation import agent_finalization, agent_runtime

    executed = []
    async def execute(command):
        executed.append(command)
        return {"ok": True}

    async def contract(runtime, ids, execute, completion):
        return execute, None

    observed = []
    continuation = None
    async def native(**kwargs):
        assert kwargs.get("continuation") is continuation
        observed.append(await kwargs["execute"](Action(action, {
            "path": path, "content": "new", "cmd": "unsafe", "source_write_nofollow_v1": True,
        })))
        return AgentResult(True, "done", {}, 1)

    monkeypatch.setattr(agent_runtime, "guard_native_source_contract", contract)
    monkeypatch.setattr(agent_native, "run_native_build", native)
    handle = SimpleNamespace(capabilities={"source_write_nofollow_v1": guarded})
    await agent_finalization.run_native_source_repair(
        task="repair", prompt_text="keep scope", baseline={"src/app/page.tsx": "old"},
        runtime=SimpleNamespace(handle=handle, coordinator=None),
        ids=SimpleNamespace(user_id="u", project_id="p", run_id="r", assistant_message_id="m"),
        plan=SimpleNamespace(model="m", stack_guide="", skills=[], steps=4),
        operations=SimpleNamespace(execute=execute, emit=AsyncMock(), continuation=continuation),
        is_free=False, shell_enabled=True, edit_deadline=None,
        source_failure=classify(missing()),
    )
    assert bool(executed) is allowed
    assert observed[0]["ok"] is allowed


async def test_repair_restart_keeps_original_inputs_and_consumes_one_allowance(monkeypatch):
    from datetime import UTC, datetime, timedelta
    from uuid import uuid4

    from yleum_api.services import agent_native
    from yleum_api.services.agent_builder import AgentResult
    from yleum_api.services.generation import agent_finalization, agent_runtime
    from yleum_api.services.generation.recovery_contract import (
        CheckpointRef,
        ExecutionCursor,
        IncompleteResponseBudget,
    )
    from yleum_api.services.generation.recovery_execution import GenerationYielded

    original_deadline = datetime.now(UTC) + timedelta(minutes=20)
    ref = CheckpointRef(object_key="owned/context", sha256="a" * 64)
    cursor = ExecutionCursor(
        stage="finalization",
        substep="checkpoint_ready",
        physical_state="active",
        execution_context_ref=ref,
    )
    driver = SimpleNamespace(context={"completed": {}}, state=SimpleNamespace(execution=cursor))
    saves = []

    async def save(execution, *, context, **kwargs):
        saves.append(context)
        driver.state.execution = execution
        driver.context = context

    driver.save = save

    async def contract(runtime, ids, execute, completion):
        return execute, completion

    monkeypatch.setattr(agent_runtime, "guard_native_source_contract", contract)
    native_calls = []
    accounting = SimpleNamespace(budget=IncompleteResponseBudget(total=1, since_progress=1))

    async def native(**kwargs):
        native_calls.append(kwargs)
        assert kwargs["continuation"] is driver
        assert saves, "repair inputs were not committed before the model boundary"
        if len(native_calls) == 1:
            raise GenerationYielded(
                run_id=uuid4(), checkpoint=None, attempt_seq=0, phase="waiting_dependency"
            )
        accounting.budget = IncompleteResponseBudget(total=2, since_progress=2)
        return AgentResult(False, "finish checks", {"src/я.ts": "изменено"}, 1)

    monkeypatch.setattr(agent_native, "run_native_build", native)
    kwargs = dict(
        task="original full repair",
        prompt_text="original goal",
        baseline={"src/я.ts": "original"},
        runtime=SimpleNamespace(handle=None, coordinator=None),
        ids=SimpleNamespace(user_id="u", project_id="p", run_id="r", assistant_message_id="m"),
        plan=SimpleNamespace(model="m", stack_guide="", skills=[], steps=4),
        operations=SimpleNamespace(execute=AsyncMock(), emit=AsyncMock(), continuation=driver,
                                   response_recovery=accounting),
        is_free=False,
        shell_enabled=False,
        edit_deadline=original_deadline,
    )
    with pytest.raises(GenerationYielded):
        await agent_finalization.run_native_source_repair(**kwargs)
    result = await agent_finalization.run_native_source_repair(
        **{
            **kwargs,
            "task": "fresh diagnostic must not rewrite original paid request",
            "baseline": {"src/я.ts": "new observation"},
            "edit_deadline": original_deadline + timedelta(minutes=5),
        }
    )
    assert result.files == {"src/я.ts": "изменено"}
    assert native_calls[1]["task"] == native_calls[0]["task"] == kwargs["task"]
    assert native_calls[1]["initial_files"] == kwargs["baseline"]
    assert native_calls[1]["edit_deadline"] == original_deadline
    assert driver.state.execution.budget.repair_calls == 1
    assert driver.context["completed"]["source_repair"]["status"] == "complete"
    accounting.budget = IncompleteResponseBudget()
    restored = await agent_finalization.run_native_source_repair(**kwargs)
    assert restored == result
    assert len(native_calls) == 2
    assert accounting.budget == IncompleteResponseBudget(total=2, since_progress=2)
    assert driver.context["completed"]["response_budget"] == {"total": 2, "since_progress": 2}


@pytest.fixture
async def adaptation_source(source_receipt, monkeypatch):
    from datetime import UTC, datetime, timedelta
    from uuid import uuid4

    from yleum_api.models.project_cell import ProjectCellWorkspace
    from yleum_api.services import max_finalization as mf
    from yleum_api.services.orchestrator_client import (
        ProjectCellAgentExecResponse,
        ProjectCellAgentOperationStatus,
        RestorationAdaptationWorkspace,
    )
    from yleum_api.services.project_cell_executor import ProjectCellCommandObservation

    c = source_receipt
    coordinator = c.coordinator
    source_identity = c.identity
    c.identity = replace(c.identity, workspace_id=uuid4(), fencing_epoch=11)
    assert c.case.sync.get(ProjectCellWorkspace, c.identity.workspace_id) is None
    assert source_identity.fencing_epoch != c.identity.fencing_epoch
    operation_id = uuid4()
    coordinator.executor.workspace_id = c.identity.workspace_id
    coordinator.executor.control_workspace_id = source_identity.workspace_id
    coordinator.executor.control_fencing_epoch = source_identity.fencing_epoch
    coordinator.executor.restoration_adaptation_workspace = RestorationAdaptationWorkspace(
        source_identity.workspace_id, c.identity.workspace_id, operation_id,
        c.case.project.id, c.case.run.user_id, c.case.run.id, c.identity.fencing_epoch,
        "a" * 64, "b" * 64, {},
    )
    c.case.run.agent_state = {**(c.case.run.agent_state or {}), "restoration_adaptation": {
        "operation_id": str(operation_id), "adaptation_run_id": str(c.case.run.id),
    }}
    receipts = {}
    now = datetime.now(UTC)
    c.case.run.started_at = c.case.run.created_at = now
    c.case.sync.commit()
    c.case.session.commit = AsyncMock(side_effect=c.case.sync.commit)

    async def physical():
        current = replace(c.identity, workspace_revision=workspace_revision_digest(c.files))
        c.case.physical = replace(c.case.physical, workspace_revision=current.workspace_revision)
        return current

    async def role(role, operation):
        identity = await physical()
        passed = "src/app/flower.css" in c.files
        detail = "clean" if passed else getattr(c, "diagnostic", missing())
        response = ProjectCellAgentExecResponse(
            passed, 0 if passed else 1, detail, False, identity.workspace_revision,
            operation, c.case.physical, c.case.physical,
        )
        observed = ProjectCellCommandObservation(
            operation, role, passed, False, detail, identity, identity, frozenset(),
        )
        receipts[operation] = (ProjectCellAgentOperationStatus(
            operation, "completed" if passed else "failed", role.value, now,
            now + timedelta(minutes=5), now, 0, response,
        ), observed)
        return observed

    async def replay(role, response, identity):
        observed = receipts[response.operation_id][1]
        assert observed.before == identity and observed.role == role
        return observed

    coordinator.executor.current_identity = physical
    coordinator.executor.run_role = role
    coordinator.executor.operation_status = AsyncMock(side_effect=lambda op: receipts[op][0])
    coordinator.executor.replay_role_response = replay
    coordinator._raise_persisted_infrastructure_failure = AsyncMock()
    coordinator._repair_progress_receipts = AsyncMock(return_value=frozenset())
    coordinator._log_terminal = AsyncMock()
    coordinator._outcome = AsyncMock(side_effect=lambda *args: mf.MaxFinalizationOutcome(*args))

    def deadline(run):
        # SQLite drops timezone information on reload; production timestamptz is UTC-aware.
        for name in ("started_at", "created_at"):
            value = getattr(run, name)
            if value is not None and value.tzinfo is None:
                setattr(run, name, value.replace(tzinfo=UTC))
        return SimpleNamespace(at=now + timedelta(hours=1))

    monkeypatch.setattr(mf, "generation_deadline", deadline)
    c.receipts = receipts
    return c


@pytest.mark.parametrize("change,sealed", [(True, False), (False, False), (True, True)])
async def test_unsealed_adaptation_source_failure_enters_same_run_repair(
    adaptation_source, monkeypatch, change, sealed,
):
    from yleum_api.services import max_finalization as mf
    from yleum_api.services.generation_metrics import GenerationPhase
    from yleum_api.services.project_cell_executor import ProjectCellCommandRole
    from yleum_api.services.project_cell_proofs import ProofDimension

    c = adaptation_source
    coordinator = c.coordinator
    builds, edits = [], []

    async def finalize(**kwargs):
        identity = await coordinator._identity()
        proof = coordinator._transient_proof(identity)
        result, _, _ = await coordinator._adaptation_role_result(
            identity=identity, proof=proof, dimension=ProofDimension.FULL_BUILD,
            role=ProjectCellCommandRole.FULL_BUILD,
        )
        builds.append(result)
        if result.outcome == "green":
            return SimpleNamespace(status=mf.MaxFinalizationStatus.COMPLETE)
        return await coordinator._failed(
            identity, proof, GenerationPhase.FINAL_BUILD, result, result.redacted_detail,
            bundle=mf.ProofBundle(identity=proof, full_build=result),
        )

    async def repair(detail, failure):
        edits.append(failure)
        assert failure.allowed_paths == ("src/app/page.tsx", "src/app/flower.css")
        if change:
            c.files["src/app/flower.css"] = "body { color: blue; }"

    coordinator.finalize = finalize
    if sealed:
        monkeypatch.setattr(mf, "generation_deadline", lambda _: SimpleNamespace(at=None))
        with pytest.raises(mf.MaxFinalizationConflict, match="sealed restoration"):
            await coordinator.finalize_with_repair(prompt="scope", repair=AsyncMock(),
                                                   repair_source=repair)
        assert not edits
    else:
        result = await coordinator.finalize_with_repair(prompt="scope", repair=AsyncMock(),
                                                        repair_source=repair)
        assert len(edits) == 1 and len(builds) == (2 if change else 1)
        assert result.status == (mf.MaxFinalizationStatus.COMPLETE if change
                                 else mf.MaxFinalizationStatus.NEEDS_EDIT)


@pytest.mark.parametrize("damage", [
    "none", "copy", "detail", "pending", "timeout", "stale", "cancel",
    "project_owner", "workspace_owner", "workspace_project", "workspace_run", "fence",
    "workspace_state", "workspace_deleted", "control_id", "control_fence",
    "candidate_id", "candidate_fence", "envelope_owner", "envelope_project", "envelope_run",
    "envelope_source", "envelope_operation", "missing_envelope"])
async def test_transient_fast_check_uses_only_current_exact_terminal_receipt(
    adaptation_source, damage,
):
    from copy import copy

    from yleum_api.services.project_cell_executor import ProjectCellCommandRole
    from yleum_api.services.project_cell_proofs import ProofDimension

    c = adaptation_source
    coordinator = c.coordinator
    result, _, _ = await coordinator._adaptation_role_result(
        identity=c.identity, proof=coordinator._transient_proof(c.identity),
        dimension=ProofDimension.FAST_CHECK, role=ProjectCellCommandRole.FAST_CHECK,
    )
    if damage == "copy":
        result = copy(result)
    elif damage == "detail":
        result.redacted_detail += "forged"
    elif damage in {"pending", "timeout"}:
        status, observation = c.receipts[result.operation_id]
        c.receipts[result.operation_id] = (replace(status, state=(
            "running" if damage == "pending" else "timed_out"
        )), observation)
    elif damage == "stale":
        c.files["src/app/page.tsx"] += "new source"
    elif damage == "cancel":
        c.case.run.status = "cancel_requested"
        c.case.sync.commit()
    if damage in {"project_owner", "workspace_owner", "workspace_project", "workspace_run",
                  "fence", "workspace_state", "workspace_deleted"}:
        from datetime import UTC, datetime
        from uuid import uuid4
        changes = {
            "project_owner": (c.case.project, "owner_id", uuid4()),
            "workspace_owner": (c.case.workspace, "owner_id", uuid4()),
            "workspace_project": (c.case.workspace, "project_id", uuid4()),
            "workspace_run": (c.case.workspace, "generation_run_id", uuid4()),
            "fence": (c.case.workspace, "fencing_epoch", c.case.workspace.fencing_epoch + 1),
            "workspace_state": (c.case.workspace, "state", "stopped"),
            "workspace_deleted": (c.case.workspace, "deleted_at", datetime.now(UTC)),
        }
        target, field, value = changes[damage]
        setattr(target, field, value)
        c.case.sync.commit()
    if damage in {"control_id", "control_fence"}:
        from uuid import uuid4
        if damage == "control_id":
            coordinator.executor.control_workspace_id = uuid4()
        else:
            coordinator.executor.control_fencing_epoch += 1
    envelope_fields = {
        "candidate_id": "candidate_workspace_id", "candidate_fence": "candidate_fencing_epoch",
        "envelope_owner": "owner_id", "envelope_project": "project_id",
        "envelope_run": "generation_run_id", "envelope_source": "source_workspace_id",
        "envelope_operation": "operation_id",
    }
    if damage in envelope_fields:
        from uuid import uuid4
        coordinator.executor.restoration_adaptation_workspace = replace(
            coordinator.executor.restoration_adaptation_workspace,
            **{envelope_fields[damage]: 99 if damage == "candidate_fence" else uuid4()},
        )
    elif damage == "missing_envelope":
        coordinator.executor.restoration_adaptation_workspace = None
    if damage == "cancel":
        with pytest.raises(asyncio.CancelledError):
            await coordinator.source_failure(result)
        return
    failure = await coordinator.source_failure(result)
    assert bool(failure and failure.allowed_paths) is (damage == "none")


async def test_owned_transient_assertion_keeps_existing_repair_semantics(adaptation_source):
    from tests.test_generation_source_check_repair import FAILURE
    from yleum_api.services.generation.agent_finalization import source_check_is_repairable
    from yleum_api.services.project_cell_executor import ProjectCellCommandRole
    from yleum_api.services.project_cell_proofs import ProofDimension

    c = adaptation_source
    c.diagnostic = FAILURE
    result, _, _ = await c.coordinator._adaptation_role_result(
        identity=c.identity, proof=c.coordinator._transient_proof(c.identity),
        dimension=ProofDimension.FAST_CHECK, role=ProjectCellCommandRole.FAST_CHECK,
    )
    disposition = await c.coordinator.source_failure(result)
    assert disposition is not None
    assert source_check_is_repairable(result.dimension, result.outcome,
                                     result.redacted_detail, disposition)
