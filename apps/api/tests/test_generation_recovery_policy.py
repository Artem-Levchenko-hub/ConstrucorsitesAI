"""Only independently verified progress may buy another recovery strategy."""
import hashlib
import importlib
import importlib.util
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import Column, MetaData, Table

from tests import test_generation_recovery_requirements as requirement_tests
from yleum_api.services.generation.recovery_contract import (
    RecoveryBinding,
    RecoveryIdentity,
    RecoveryObservation,
    RecoveryState,
)

receipt_case = requirement_tests.receipt_case
loaded = requirement_tests.loaded


def policy():
    name = "yleum_api.services.generation.recovery_policy"
    assert importlib.util.find_spec(name) is not None, "semantic recovery policy missing"
    return importlib.import_module(name).observe_recovery


def state():
    return RecoveryState(identity=RecoveryIdentity(
        run_id=uuid4(), project_id=uuid4(), user_id=uuid4(), prompt_hash="a" * 64,
        accepted_snapshot_id=None, supersedes_run_id=None,
    ), binding=binding(), active_work_ms=900, stage_active_work_ms={"edit": 900})


def binding(source="a"):
    return RecoveryBinding(source_sha256=source * 64, schema_sha256="b" * 64,
        environment_sha256="c" * 64, workspace_id="workspace", fencing_epoch=1)


def observation(kind="source", source="a", action="edit", errors=(), witness=None):
    return RecoveryObservation(kind=kind, binding=binding(source),
        requirement_id=witness.requirement_id if witness else None,
        action_signature=action, diagnostic_signatures=errors, witness=witness)


def test_equivalent_stalls_escalate_every_two_observations_without_losing_history():
    observe = policy()
    original = current = state()
    directives = []
    for _ in range(8):
        current, directive = observe(current, observation())
        directives.append(directive.action)
    assert directives == ["continue", "nudge", "continue", "narrow",
                          "continue", "reset_context", "continue", "needs_input"]
    assert len(current.observations) == 8
    assert current.active_work_ms == 900 and current.stage_active_work_ms == {"edit": 900}
    assert original.consecutive_no_progress == 0
    assert current.consecutive_no_progress == 8


@pytest.mark.parametrize("kind", ["source", "tool", "proof", "dependency", "truncated"])
def test_comments_aba_green_builds_and_counters_are_not_progress(kind):
    observe = policy()
    current = state()
    for source in ("a", "b", "a", "c"):
        current, _ = observe(current, observation(kind, source))
    assert current.consecutive_no_progress == 4
    assert current.active_work_ms == 900


def test_different_failure_restarts_equivalence_pair_but_not_work_accounting():
    observe = policy()
    current, _ = observe(state(), observation(action="read"))
    current, directive = observe(current, observation(action="edit"))
    assert directive.action == "continue"
    current, directive = observe(current, observation(action="edit"))
    assert directive.action == "nudge"
    assert current.consecutive_no_progress == 3


def test_smaller_unverified_or_excerpted_compiler_diagnostics_never_reset_progress():
    observe = policy()
    current, _ = observe(state(), observation("diagnostics", errors=("TS1", "TS2")))
    current, _ = observe(current, observation("diagnostics", "b", errors=("TS1",)))
    assert current.consecutive_no_progress == 2
    for source, errors in (("a", ("TS1", "TS2")), ("b", ("TS1",))):
        current, _ = observe(current, observation("diagnostics", source, errors=errors))
    assert current.consecutive_no_progress == 4
    current, _ = observe(current, observation("diagnostics", "c", errors=()))
    assert current.consecutive_no_progress == 5
    current, _ = observe(current, observation("diagnostics", "c", errors=()))
    assert current.consecutive_no_progress == 6


def test_replaced_errors_or_changed_environment_cannot_claim_reduction():
    observe = policy()
    current, _ = observe(state(), observation("diagnostics", errors=("TS1", "TS2")))
    other = observation("diagnostics", "b", errors=("TS3",))
    current, _ = observe(current, other)
    assert current.consecutive_no_progress == 2
    other = observation("diagnostics", "c", errors=()).model_copy(update={
        "binding": binding("c").model_copy(update={"environment_sha256": "d" * 64})})
    current, _ = observe(current, other)
    assert current.consecutive_no_progress == 3


def test_history_roundtrip_retains_no_progress_and_rejects_corruption():
    from pydantic import ValidationError
    observe = policy()
    current, _ = observe(state(), observation())
    decoded = RecoveryState.model_validate_json(current.model_dump_json())
    assert decoded == current
    with pytest.raises(ValidationError):
        RecoveryState.model_validate({**current.model_dump(), "observations": ({"kind": "bad"},)})


async def test_new_trusted_pass_resets_streak_but_replayed_or_serialized_receipt_does_not(
    receipt_case,
):
    observe = policy()
    c = receipt_case
    witness = await loaded(c)
    current = state().model_copy(update={
        "identity": RecoveryIdentity(run_id=c.run.id, project_id=c.project.id,
            user_id=c.run.user_id, prompt_hash=c.run.prompt_hash,
            accepted_snapshot_id=None, supersedes_run_id=None),
        "requirements": c.requirements, "binding": c.binding,
        "consecutive_no_progress": 5,
    })
    proof = observation("proof", witness=witness).model_copy(update={"binding": c.binding})
    original = current
    decoded = type(witness).model_validate_json(witness.model_dump_json())
    untrusted, _ = observe(current, proof.model_copy(update={"witness": decoded}))
    assert untrusted.consecutive_no_progress == 6
    current, directive = observe(current, proof)
    assert current.consecutive_no_progress == 0 and directive.action == "continue"
    assert original.consecutive_no_progress == 5
    current, _ = observe(current, proof.model_copy(update={"witness": await loaded(c)}))
    assert current.consecutive_no_progress == 1
    assert current.active_work_ms == 900
    changed = proof.model_copy(update={"binding": c.binding.model_copy(update={
        "source_sha256": "f" * 64})})
    current, _ = observe(current, changed)
    assert current.consecutive_no_progress == 2


async def test_compiler_reduction_cannot_hide_regression_of_prior_verified_requirement(
    compiler_case,
):
    observe = policy()
    c = compiler_case
    witness = await loaded(c)
    current = state().model_copy(update={"requirements": c.requirements,
        "binding": c.binding, "witnesses": (witness,),
        "identity": RecoveryIdentity(run_id=c.run.id, project_id=c.project.id,
            user_id=c.run.user_id, prompt_hash=c.run.prompt_hash,
            accepted_snapshot_id=None, supersedes_run_id=None)})
    red, _, _ = await compiler_observation(c, "d", False)
    current, _ = observe(current, red)
    green, _, _ = await compiler_observation(c, "e", True)
    current, _ = observe(current, green)
    assert current.consecutive_no_progress == 2


async def test_loader_receipt_cannot_reset_another_runs_progress(receipt_case):
    observe = policy()
    c = receipt_case
    witness = await loaded(c)
    current = state().model_copy(update={"requirements": c.requirements,
        "identity": state().identity.model_copy(update={"prompt_hash": c.run.prompt_hash}),
        "binding": c.binding, "consecutive_no_progress": 3})
    proof = observation("proof", witness=witness).model_copy(update={"binding": c.binding})
    current, _ = observe(current, proof)
    assert current.consecutive_no_progress == 4


@pytest.fixture
async def compiler_case(receipt_case):
    from yleum_api.models.project_cell import (
        ProjectCellActivityLease,
        ProjectCellProof,
        ProjectCellProofResult,
    )
    from yleum_api.services.project_cell_proofs import ProofDimension, proof_identity_from_model

    c = receipt_case
    metadata = MetaData()
    Table(ProjectCellProofResult.__tablename__, metadata, *(
        Column(col.name, col.type, primary_key=col.primary_key)
        for col in ProjectCellProofResult.__table__.columns))
    metadata.create_all(c.sync.bind)

    async def receipt(source, passed):
        identity = replace(proof_identity_from_model(c.proof), workspace_revision=source * 64)
        proof = ProjectCellProof(id=uuid4(), **{
            key: getattr(identity, key) for key in identity.__dataclass_fields__
        }, proof_key=identity.proof_key)
        now = datetime.now(UTC)
        operation_id = uuid4()
        lease = ProjectCellActivityLease(operation_id=operation_id,
            workspace_id=c.workspace.id, generation_run_id=c.run.id, kind="command",
            state="completed" if passed else "failed", fencing_epoch=7,
            proof_key=identity.proof_key, phase="fast_check", started_at=now,
            heartbeat_at=now, finished_at=now, deadline_at=now + timedelta(minutes=1), log_bytes=0)
        detail = "check passed" if passed else "TS1234: compiler error"
        result = ProjectCellProofResult(id=uuid4(), proof_id=proof.id,
            workspace_id=c.workspace.id, dimension="fast_check",
            dimension_key=identity.dimension_key(ProofDimension.FAST_CHECK),
            outcome="green" if passed else "red", operation_id=operation_id,
            artifact_ref=None, detail_digest=hashlib.sha256(detail.encode()).hexdigest(),
            redacted_detail=detail)
        c.sync.add_all([proof, lease, result])
        c.sync.commit()
        c.physical = replace(c.physical, workspace_revision=source * 64)
        current_binding = c.binding.model_copy(update={"source_sha256": source * 64})
        return result, lease, current_binding

    c.compiler_receipt = receipt
    return c


async def compiler_observation(c, source, passed):
    loader = getattr(c.rr, "load_compiler_witness", None)
    assert callable(loader), "current server compiler receipt loader missing"
    result, lease, current_binding = await c.compiler_receipt(source, passed)
    witness = await loader(c.session, run_id=c.run.id, receipt_id=result.id,
                           binding=current_binding)
    observed = observation("diagnostics", source, action="fast_check").model_copy(update={
        "binding": current_binding, "compiler": witness})
    return observed, result, lease


async def test_completed_server_red_to_green_progress_once_and_not_aba(compiler_case):
    observe = policy()
    c = compiler_case
    current = state().model_copy(update={"identity": RecoveryIdentity(
        run_id=c.run.id, project_id=c.project.id, user_id=c.run.user_id,
        prompt_hash=c.run.prompt_hash, accepted_snapshot_id=None, supersedes_run_id=None)})
    red, _, _ = await compiler_observation(c, "d", False)
    current, _ = observe(current, red)
    green, _, _ = await compiler_observation(c, "e", True)
    current, _ = observe(current, green)
    assert current.consecutive_no_progress == 0
    current, _ = observe(current, green)
    assert current.consecutive_no_progress == 1
    current, _ = observe(current, red)
    current, _ = observe(current, green)
    assert current.consecutive_no_progress == 3


async def test_serialized_prior_red_cannot_authorize_reduction_with_a_current_green(compiler_case):
    observe = policy()
    c = compiler_case
    current = state().model_copy(update={"identity": RecoveryIdentity(
        run_id=c.run.id, project_id=c.project.id, user_id=c.run.user_id,
        prompt_hash=c.run.prompt_hash, accepted_snapshot_id=None, supersedes_run_id=None)})
    red, _, _ = await compiler_observation(c, "d", False)
    current, _ = observe(current, red)
    current = RecoveryState.model_validate_json(current.model_dump_json())
    green, _, _ = await compiler_observation(c, "e", True)
    current, _ = observe(current, green)
    assert current.consecutive_no_progress == 2


async def test_refreshed_same_row_green_receipt_earns_progress_only_once(compiler_case):
    from yleum_api.models.project_cell import ProjectCellActivityLease

    observe = policy()
    c = compiler_case
    current = state().model_copy(update={"identity": RecoveryIdentity(
        run_id=c.run.id, project_id=c.project.id, user_id=c.run.user_id,
        prompt_hash=c.run.prompt_hash, accepted_snapshot_id=None, supersedes_run_id=None)})
    red, result, old_lease = await compiler_observation(c, "d", False)
    current, _ = observe(current, red)
    new_operation = uuid4()
    c.sync.add(ProjectCellActivityLease(operation_id=new_operation,
        workspace_id=old_lease.workspace_id, generation_run_id=c.run.id, kind="command",
        state="completed", fencing_epoch=old_lease.fencing_epoch,
        proof_key=old_lease.proof_key, phase=old_lease.phase,
        started_at=old_lease.started_at, heartbeat_at=old_lease.heartbeat_at,
        finished_at=old_lease.finished_at, deadline_at=old_lease.deadline_at, log_bytes=0))
    result.operation_id = new_operation
    result.outcome = "green"
    result.redacted_detail = "current completed check passed"
    result.detail_digest = hashlib.sha256(result.redacted_detail.encode()).hexdigest()
    c.sync.commit()
    witness = await c.rr.load_compiler_witness(c.session, run_id=c.run.id,
        receipt_id=result.id, binding=red.binding)
    green = red.model_copy(update={"compiler": witness})
    current, _ = observe(current, green)
    assert current.consecutive_no_progress == 0
    current, _ = observe(current, green)
    assert current.consecutive_no_progress == 1
    assert len(current.compiler_witnesses) == 2
    assert len(current.observations) == 3


@pytest.mark.parametrize("mutation", ["incomplete", "foreign_run", "foreign_owner", "fence",
    "source", "schema", "environment", "operation", "digest", "dimension", "serialized"])
async def test_compiler_receipts_fail_closed_on_invalid_authority(compiler_case, mutation):
    from yleum_api.services.generation.recovery_contract import RecoveryContractError
    c = compiler_case
    loader = getattr(c.rr, "load_compiler_witness", None)
    assert callable(loader), "current server compiler receipt loader missing"
    result, lease, current_binding = await c.compiler_receipt("d", True)
    if mutation == "incomplete":
        lease.finished_at = None
    elif mutation == "foreign_run":
        lease.generation_run_id = uuid4()
    elif mutation == "foreign_owner":
        c.workspace.owner_id = uuid4()
    elif mutation == "fence":
        lease.fencing_epoch += 1
    elif mutation in {"source", "schema", "environment"}:
        current_binding = current_binding.model_copy(update={mutation + "_sha256": "f" * 64})
    elif mutation == "operation":
        result.operation_id = uuid4()
    elif mutation == "digest":
        result.detail_digest = "f" * 64
    elif mutation == "dimension":
        result.dimension = "bootstrap"
    c.sync.commit()
    if mutation == "serialized":
        witness = await loader(c.session, run_id=c.run.id, receipt_id=result.id,
                               binding=current_binding)
        decoded = type(witness).model_validate_json(witness.model_dump_json())
        checker = getattr(c.rr, "compiler_witness_is_trusted")
        assert not checker(decoded, identity=RecoveryIdentity(run_id=c.run.id,
            project_id=c.project.id, user_id=c.run.user_id, prompt_hash=c.run.prompt_hash,
            accepted_snapshot_id=None, supersedes_run_id=None), binding=current_binding)
    else:
        with pytest.raises(RecoveryContractError):
            await loader(c.session, run_id=c.run.id, receipt_id=result.id, binding=current_binding)
