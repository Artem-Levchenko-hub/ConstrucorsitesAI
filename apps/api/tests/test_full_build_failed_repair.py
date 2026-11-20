"""A failed environment-only build may authorize repair, never a green proof."""

from __future__ import annotations

import hashlib
import json
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import JSON, Column, MetaData, Table, create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Session

from tests.test_full_build_startup_transition import evidence
from yleum_api.models.generation_run import GenerationRun
from yleum_api.models.project import Project
from yleum_api.models.project_cell import (
    ProjectCellActivityLease,
    ProjectCellProof,
    ProjectCellProofResult,
    ProjectCellWorkspace,
)
from yleum_api.services import max_finalization
from yleum_api.services import project_cell_executor as executor
from yleum_api.services.generation import recovery_requirements as requirements
from yleum_api.services.generation.recovery_actions import recovery_binding
from yleum_api.services.generation.recovery_contract import RecoveryContractError
from yleum_api.services.orchestrator_client import (
    OrchestratorUnavailable,
    ProjectCellAgentExecResponse,
    ProjectCellAgentOperationStatus,
    ProjectCellWorkspaceIdentity,
)
from yleum_api.services.project_cell_proofs import ProofDimension
from yleum_api.services.promotion_permit import workspace_revision_digest

FILES = {
    "src/app/page.tsx": "export default function Page() { return null; }",
    "tests/page-smoke.mjs": "import assert from 'node:assert/strict';\nassert.ok(false);\n",
    ".omnia/cell.json": json.dumps(
        {
            "version": 1,
            "tasks": [
                {
                    "name": "final-test",
                    "role": "full_build",
                    "cwd": ".",
                    "argv": ["sh", "-lc", "pnpm test && node tests/page-smoke.mjs"],
                }
            ],
        }
    ),
}
DETAIL = "src/app/page.tsx(1,1): error TS2322: Type 'string' is not assignable to type 'number'."
ASSERTION = "AssertionError [ERR_ASSERTION]: wrong rendered page\n    at file:///workspace/tests/page-smoke.mjs:2:3"
PARTIAL_ASSERTION = "[final-test] \nAssertionError [ERR_ASSERTION]: wrong rendered page\n    at file:///workspace/tests/page-sm"


def identities():
    before, after, _compilation, _migration, project, owner, operation = evidence()
    revision = workspace_revision_digest(FILES)
    return (
        replace(before, workspace_revision=revision),
        replace(after, workspace_revision=revision),
        project,
        owner,
        operation,
    )


def transition(before, after, operation, **changes):
    return executor.failed_full_build_environment_transition(
        before,
        after,
        operation_id=operation,
        **{"ok": False, "timed_out": False, "exit_code": 1, **changes},
    )


def physical(identity):
    return ProjectCellWorkspaceIdentity(
        identity.workspace_revision,
        identity.dependency_digest,
        identity.schema_data_digest,
        identity.cell_manifest_digest,
        identity.base_image_digest,
        identity.build_config_digest,
    )


def response(before, after, operation, **changes):
    return ProjectCellAgentExecResponse(
        **{
            "ok": False,
            "exit_code": 1,
            "detail": DETAIL,
            "timed_out": False,
            "workspace_revision": after.workspace_revision,
            "operation_id": operation,
            "before_identity": physical(before),
            "after_identity": physical(after),
            "environment_mutated": before != after,
            **changes,
        }
    )


def terminal_status(result, *, phase="final-test", state="failed"):
    now = datetime.now(UTC)
    return ProjectCellAgentOperationStatus(
        operation_id=result.operation_id,
        state=state,
        phase=phase,
        started_at=now,
        deadline_at=now + timedelta(minutes=5),
        heartbeat_at=now,
        log_bytes=len(result.detail.encode()),
        terminal_response=result,
    )


@pytest.mark.parametrize(
    "field",
    [
        "workspace_id",
        "generation_run_id",
        "fencing_epoch",
        "workspace_revision",
        "dependency_digest",
        "schema_data_digest",
        "cell_manifest_digest",
        "resource_profile_version",
        "build_config_digest",
    ],
)
def test_failed_rebind_refuses_every_non_environment_change(field):
    before, after, _project, _owner, operation = identities()
    value = (
        uuid4()
        if field.endswith("_id")
        else 4
        if field == "fencing_epoch"
        else "different-profile"
        if field == "resource_profile_version"
        else "f" * 64
    )
    assert transition(before, replace(after, **{field: value}), operation) is None


@pytest.mark.parametrize(
    "change",
    [
        {"ok": True},
        {"timed_out": True},
        {"exit_code": 0},
        {"exit_code": None},
        {"exit_code": True},
    ],
)
def test_failed_rebind_cannot_certify_success_or_unknown_command(change):
    before, after, _project, _owner, operation = identities()
    assert transition(before, after, operation, **change) is None
    assert transition(before, before, operation) is None
    assert transition(before, replace(after, toolchain_digest="e" * 64), operation) is None


def coordinator(before, after, operation, *, detail=DETAIL, witnessed=True):
    c = object.__new__(max_finalization.MaxFinalizationCoordinator)
    c.generation_run_id = before.generation_run_id
    c._proof = AsyncMock(side_effect=c._transient_proof)
    c._verified_workspace_files = AsyncMock(return_value=FILES)
    c._record = AsyncMock(
        side_effect=lambda **kw: SimpleNamespace(
            id=uuid4(),
            proof_id=kw["proof"].id,
            outcome=kw["outcome"].value,
            dimension=kw["dimension"].value,
            artifact_ref=kw["artifact_ref"],
            redacted_detail=kw["detail"],
            operation_id=kw["operation_id"],
        )
    )
    observation = executor.ProjectCellCommandObservation(
        operation_id=operation,
        role=executor.ProjectCellCommandRole.FULL_BUILD,
        ok=False,
        timed_out=False,
        redacted_detail=detail,
        before=before,
        after=after,
        invalidated_dimensions=executor.invalidated_dimensions(before, after),
        failed_full_build_environment_transition=transition(before, after, operation)
        if witnessed
        else None,
    )
    c._execute_role = AsyncMock(return_value=observation)
    c.executor = SimpleNamespace(
        operation_status=AsyncMock(
            return_value=terminal_status(
                response(before, after, operation, detail=detail),
            )
        ),
        replay_role_response=AsyncMock(return_value=observation),
    )
    return c


async def test_failed_rebind_records_only_after_red_and_actual_operation():
    before, after, _project, _owner, operation = identities()
    c = coordinator(before, after, operation)
    result, identity, proof = await c._run_full_build_result(await c._proof(before), before)
    assert identity == after and proof.proof_key == after.proof_key
    assert result.outcome == "red" and result.artifact_ref is None
    assert result.operation_id == operation
    assert "[source-repair:confirmed]" in result.redacted_detail
    assert "[failed-full-build-environment-transition:v1]" in result.redacted_detail
    assert (
        DETAIL in result.redacted_detail
        and "command changed the frozen" not in result.redacted_detail
    )
    c._verified_workspace_files.assert_awaited_once_with(after)
    assert c._record.await_count == c._execute_role.await_count == 1


async def test_owned_smoke_failure_can_request_repair_but_never_success():
    before, after, _project, _owner, operation = identities()
    c = coordinator(before, after, operation, detail=ASSERTION)
    result, identity, proof = await c._run_full_build_result(await c._proof(before), before)
    c._outcome = AsyncMock(side_effect=lambda status, *args: SimpleNamespace(status=status))
    c._log_terminal = AsyncMock()
    outcome = await c._failed(
        identity,
        proof,
        max_finalization.GenerationPhase.FINAL_BUILD,
        result,
        result.redacted_detail,
    )
    assert outcome.status is max_finalization.MaxFinalizationStatus.NEEDS_EDIT
    assert identity == after and result.outcome == "red" and result.artifact_ref is None


async def test_partial_frame_reconciles_terminal_task_without_granting_file_paths():
    before, after, _project, _owner, operation = identities()
    c = coordinator(before, after, operation, detail=PARTIAL_ASSERTION)
    result, identity, proof = await c._run_full_build_result(await c._proof(before), before)
    assert identity == after and proof.proof_key == after.proof_key
    assert result.outcome == "red" and result.artifact_ref is None
    assert "[source-repair:confirmed]" in result.redacted_detail
    c.executor.operation_status.assert_awaited_once_with(operation)
    c.executor.replay_role_response.assert_awaited_once()
    assert c._verified_workspace_files.await_count == 2
    failure = await c._full_build_source_failure(
        result.redacted_detail,
        after,
        FILES,
        operation_id=operation,
    )
    assert failure.kind == "test_assertion" and failure.importer is None
    assert failure.allowed_paths == () and max_finalization._repairable_build_failure(failure)


@pytest.mark.parametrize(
    "mutation", ["phase", "operation", "running", "timeout", "exit", "response", "replay"]
)
async def test_partial_frame_requires_exact_failed_terminal_replay(mutation):
    before, after, _project, _owner, operation = identities()
    c = coordinator(before, after, operation, detail=PARTIAL_ASSERTION)
    status = c.executor.operation_status.return_value
    if mutation == "phase":
        status = replace(status, phase="build")
    elif mutation == "operation":
        status = replace(status, operation_id=uuid4())
    elif mutation == "running":
        status = replace(status, state="running")
    elif mutation == "timeout":
        status = replace(
            status, terminal_response=replace(status.terminal_response, timed_out=True)
        )
    elif mutation == "exit":
        status = replace(status, terminal_response=replace(status.terminal_response, exit_code=0))
    elif mutation == "response":
        status = replace(status, terminal_response=None)
    elif mutation == "replay":
        c.executor.replay_role_response.return_value = replace(
            c._execute_role.return_value, ok=True
        )
    c.executor.operation_status.return_value = status
    result, identity, _ = await c._run_full_build_result(await c._proof(before), before)
    assert identity == before and "[source-repair:unconfirmed]" in result.redacted_detail
    c.executor.operation_status.assert_awaited_once()


@pytest.mark.parametrize("lookup", ["status", "replay", "source"])
async def test_partial_frame_transient_lookup_retries_reads_not_the_command(monkeypatch, lookup):
    before, after, _project, _owner, operation = identities()
    c = coordinator(before, after, operation, detail=PARTIAL_ASSERTION)
    sleep = AsyncMock()
    monkeypatch.setattr(max_finalization.asyncio, "sleep", sleep)
    unavailable = OrchestratorUnavailable("temporary diagnostic transport503")
    if lookup == "status":
        c.executor.operation_status.side_effect = [
            unavailable,
            c.executor.operation_status.return_value,
        ]
    elif lookup == "replay":
        c.executor.replay_role_response.side_effect = [unavailable, c._execute_role.return_value]
    else:
        c._verified_workspace_files.side_effect = [FILES, unavailable, FILES]
    result, identity, _ = await c._run_full_build_result(await c._proof(before), before)
    assert identity == after and result.outcome == "red" and result.artifact_ref is None
    assert "[source-repair:confirmed]" in result.redacted_detail
    assert c._execute_role.await_count == 1 and c._record.await_count == 1
    assert c.executor.operation_status.await_count == 2
    sleep.assert_awaited_once_with(1.0)


@pytest.mark.parametrize("lookup", ["status", "replay", "source"])
async def test_partial_frame_unavailable_lookup_waits_without_rejecting_candidate(
    monkeypatch, lookup
):
    before, after, _project, _owner, operation = identities()
    c = coordinator(before, after, operation, detail=PARTIAL_ASSERTION)
    sleep = AsyncMock()
    monkeypatch.setattr(max_finalization.asyncio, "sleep", sleep)
    unavailable = OrchestratorUnavailable("private transport503 AUTH_SECRET=not-for-output")
    if lookup == "status":
        c.executor.operation_status.side_effect = unavailable
    elif lookup == "replay":
        c.executor.replay_role_response.side_effect = unavailable
    else:
        c._verified_workspace_files.side_effect = [FILES, unavailable, unavailable, unavailable]
    with pytest.raises(max_finalization.MaxVerificationUnavailable) as caught:
        await c._run_full_build_result(await c._proof(before), before)
    assert caught.value.phase is max_finalization.GenerationPhase.FINAL_BUILD
    assert caught.value.operation_id == operation
    assert "not-for-output" not in str(caught.value)
    assert [call.args[0] for call in sleep.await_args_list] == [1.0, 3.0]
    assert c.executor.operation_status.await_count == 3
    c._record.assert_not_awaited()
    assert c._execute_role.await_count == 1  # only the original, already-failed command


@pytest.mark.parametrize(
    "detail,witnessed",
    [
        ("ECONNREFUSED from worker", True),
        ("src/foreign.ts(1,1): error TS2322: bad", True),
        ("unknown error", True),
        (DETAIL, False),
    ],
)
async def test_unknown_unowned_infrastructure_or_unwitnessed_build_stays_failed(detail, witnessed):
    before, after, _project, _owner, operation = identities()
    c = coordinator(before, after, operation, detail=detail, witnessed=witnessed)
    result, identity, proof = await c._run_full_build_result(await c._proof(before), before)
    assert identity == before and proof.proof_key == before.proof_key
    assert result.outcome == "red" and result.artifact_ref is None
    assert "[source-repair:unconfirmed]" in result.redacted_detail
    assert "command changed the frozen proof identity" in result.redacted_detail


async def test_after_drift_is_not_recorded_as_a_source_failure():
    before, after, _project, _owner, operation = identities()
    c = coordinator(before, after, operation)
    c._verified_workspace_files.side_effect = max_finalization.MaxFinalizationConflict("changed")
    with pytest.raises(max_finalization.MaxFinalizationConflict):
        await c._run_full_build_result(await c._proof(before), before)
    c._record.assert_not_awaited()


async def test_typed_transition_does_not_override_non_environment_identity_guard():
    before, after, _project, _owner, operation = identities()
    c = coordinator(before, after, operation)
    changed = replace(after, build_config_digest="e" * 64)
    observation = c._execute_role.return_value
    c._execute_role.return_value = replace(
        observation,
        after=changed,
        failed_full_build_environment_transition=executor.FailedFullBuildEnvironmentTransition(
            before,
            changed,
            operation,
            1,
        ),
    )
    result, identity, _ = await c._run_full_build_result(await c._proof(before), before)
    assert identity == before and "[source-repair:unconfirmed]" in result.redacted_detail
    c._verified_workspace_files.assert_not_awaited()


@pytest.fixture(
    params=[DETAIL, ASSERTION, PARTIAL_ASSERTION],
    ids=["typescript", "smoke-assertion", "partial-assertion"],
)
async def witness_case(monkeypatch, request):
    """Real ORM rows/queries; only owning physical reads are synthetic."""
    before, after, project_id, owner, operation = identities()
    engine, metadata = create_engine("sqlite://"), MetaData()
    models = (
        GenerationRun,
        Project,
        ProjectCellWorkspace,
        ProjectCellProof,
        ProjectCellProofResult,
        ProjectCellActivityLease,
    )
    for model in models:
        Table(
            model.__tablename__,
            metadata,
            *(
                Column(
                    column.name,
                    JSON() if isinstance(column.type, JSONB) else column.type,
                    primary_key=column.primary_key,
                )
                for column in model.__table__.columns
            ),
        )
    metadata.create_all(engine)
    sync = Session(engine, expire_on_commit=False)
    project = Project(id=project_id, owner_id=owner, template="max_miniapp")
    run = GenerationRun(
        id=before.generation_run_id,
        project_id=project_id,
        user_id=owner,
        status="running",
        prompt_hash="d" * 64,
        idempotency_key="failed-build",
    )
    workspace = ProjectCellWorkspace(
        id=before.workspace_id,
        project_id=project_id,
        owner_id=owner,
        generation_run_id=run.id,
        fencing_epoch=before.fencing_epoch,
        state="ready",
    )
    c = coordinator(before, after, operation, detail=request.param)
    result, actual, _ = await c._run_full_build_result(await c._proof(before), before)
    assert actual == after
    proof = ProjectCellProof(
        id=uuid4(),
        **{key: getattr(after, key) for key in after.__dataclass_fields__},
        proof_key=after.proof_key,
    )
    row = ProjectCellProofResult(
        id=result.id,
        proof_id=proof.id,
        workspace_id=after.workspace_id,
        dimension="full_build",
        dimension_key=after.dimension_key(ProofDimension.FULL_BUILD),
        outcome="red",
        operation_id=operation,
        artifact_ref=None,
        redacted_detail=result.redacted_detail,
        detail_digest=hashlib.sha256(result.redacted_detail.encode()).hexdigest(),
    )
    now = datetime.now(UTC)
    lease = ProjectCellActivityLease(
        operation_id=operation,
        workspace_id=before.workspace_id,
        generation_run_id=run.id,
        fencing_epoch=before.fencing_epoch,
        proof_key=before.proof_key,
        kind="command",
        phase="final_build",
        state="failed",
        started_at=now,
        deadline_at=now + timedelta(minutes=5),
        heartbeat_at=now,
        finished_at=now,
    )
    sync.add_all([project, run, workspace, proof, row, lease])
    sync.commit()

    class BoundarySession:
        async def get(self, model, key, **kw):
            return sync.get(model, key, **kw)

    @asynccontextmanager
    async def factory():
        yield BoundarySession()

    case = SimpleNamespace(
        before=before,
        after=after,
        owner=owner,
        project=project,
        run=run,
        workspace=workspace,
        proof=proof,
        row=row,
        lease=lease,
        sync=sync,
        factory=factory,
        response=response(before, after, operation, detail=request.param),
        reads=0,
        observation=c._execute_role.return_value,
    )

    async def status(workspace_id, operation_id):
        assert workspace_id == before.workspace_id and operation_id == operation
        case.reads += 1
        return terminal_status(case.response, state=case.status)

    case.status = "failed"
    case.operation_status = lambda op: status(before.workspace_id, op)
    monkeypatch.setattr(requirements, "project_cell_agent_operation_status", status)
    monkeypatch.setattr(
        requirements,
        "project_cell_agent_identity",
        AsyncMock(side_effect=lambda *a, **k: physical(case.after)),
    )
    yield case
    sync.close()
    engine.dispose()


async def load(case):
    async with case.factory() as session:
        return await requirements.load_compiler_witness(
            session,
            run_id=case.run.id,
            receipt_id=case.row.id,
            binding=recovery_binding(case.after),
        )


async def test_owned_after_red_witness_and_cold_source_failure_keep_before_lease(witness_case):
    c = witness_case
    witness = await load(c)
    assert not witness.passed and witness.operation_id == c.lease.operation_id
    assert c.lease.proof_key == c.before.proof_key != c.after.proof_key
    # A new coordinator loads persisted RED and the same terminal response. It
    # never reruns FULL_BUILD or upgrades the failed command into a success.
    for _ in range(2):
        cold = object.__new__(max_finalization.MaxFinalizationCoordinator)
        cold.session_factory, cold.project_id, cold.generation_run_id = (
            c.factory,
            c.project.id,
            c.run.id,
        )
        cold._identity = AsyncMock(return_value=c.after)
        cold._verified_workspace_files = AsyncMock(return_value=FILES)
        cold._execute_role = AsyncMock(side_effect=AssertionError("must not replay build"))
        cold.executor = SimpleNamespace(
            operation_status=c.operation_status,
            replay_role_response=AsyncMock(return_value=c.observation),
        )
        failure = await cold.source_failure(c.row)
        expected = "tests/page-smoke.mjs" if c.response.detail == ASSERTION else "src/app/page.tsx"
        assert failure is not None
        if c.response.detail == PARTIAL_ASSERTION:
            assert failure.kind == "test_assertion" and failure.allowed_paths == ()
        else:
            assert failure.allowed_paths == (expected,)
        cold._execute_role.assert_not_awaited()
    assert c.reads == (5 if c.response.detail == PARTIAL_ASSERTION else 3)
    assert c.row.outcome == "red" and c.row.artifact_ref is None


@pytest.mark.parametrize(
    "mutation",
    [
        "owner",
        "project",
        "run",
        "fence",
        "lease_before",
        "operation",
        "pending",
        "success",
        "timeout",
        "source_changed",
        "artifact",
        "unversioned_detail",
        "after_environment",
    ],
)
async def test_failed_witness_never_adopts_foreign_or_changed_response(witness_case, mutation):
    c = witness_case
    if mutation == "owner":
        c.workspace.owner_id = uuid4()
    elif mutation == "project":
        c.workspace.project_id = uuid4()
    elif mutation == "run":
        c.workspace.generation_run_id = uuid4()
    elif mutation == "fence":
        c.workspace.fencing_epoch += 1
    elif mutation == "lease_before":
        c.lease.proof_key = "f" * 64
    elif mutation == "operation":
        c.response = replace(c.response, operation_id=uuid4())
    elif mutation == "pending":
        c.status = "running"
    elif mutation == "success":
        c.response = replace(c.response, ok=True, exit_code=0)
    elif mutation == "timeout":
        c.response = replace(c.response, timed_out=True)
    elif mutation == "source_changed":
        other = replace(c.before, workspace_revision="e" * 64)
        c.response = response(other, c.after, c.lease.operation_id)
    elif mutation == "artifact":
        c.row.artifact_ref = "build/sha256/" + "a" * 64
    elif mutation == "unversioned_detail":
        c.row.redacted_detail = c.response.detail
        c.row.detail_digest = hashlib.sha256(c.row.redacted_detail.encode()).hexdigest()
    elif mutation == "after_environment":
        other = replace(c.after, base_image_digest="e" * 64, toolchain_digest="e" * 64)
        c.response = response(c.before, other, c.lease.operation_id)
    c.sync.commit()
    with pytest.raises(RecoveryContractError):
        await load(c)
