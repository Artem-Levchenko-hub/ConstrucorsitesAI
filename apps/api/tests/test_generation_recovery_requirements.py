"""Frozen scope and server-owned receipts, without any service/database fixture."""
from __future__ import annotations

import copy
import hashlib
import importlib
import importlib.util
from contextlib import asynccontextmanager
from types import SimpleNamespace as NS
from uuid import uuid4

import pytest
from sqlalchemy import JSON, Column, MetaData, Table, create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from tests.test_max_behavior_proof import driver
from yleum_api.models.generation_run import GenerationRun
from yleum_api.models.project import Project
from yleum_api.models.project_cell import (
    ProjectCellActivityLease,
    ProjectCellCandidate,
    ProjectCellOperation,
    ProjectCellProof,
    ProjectCellWorkspace,
)
from yleum_api.services import max_behavior_proof as behavior
from yleum_api.services.build_spec import FidelitySpec
from yleum_api.services.generation.recovery_contract import (
    RecoveryBinding,
    RecoveryContractError,
    RequirementWitness,
)
from yleum_api.services.orchestrator_client import ProjectCellWorkspaceIdentity
from yleum_api.services.project_cell_proofs import ProofIdentity

PROMPT = "task_density_v1"


def requirements_module():
    name = "yleum_api.services.generation.recovery_requirements"
    assert importlib.util.find_spec(name) is not None, "missing frozen request requirements"
    return importlib.import_module(name)


def frozen(prompt=PROMPT, spec=None):
    return requirements_module().freeze_requirements(
        prompt, spec or FidelitySpec(),
        behavior.required_contract(prompt, template="max_miniapp"),
    )


def current_binding():
    return RecoveryBinding(source_sha256="b" * 64, schema_sha256="c" * 64,
                           environment_sha256="e" * 64, workspace_id=str(uuid4()),
                           fencing_epoch=7)


def test_unknown_requirement_is_pending_not_dropped():
    text = "task_density_v1; export encrypted payroll to my private system"
    items = frozen(text)
    assert any(r.required and r.check_kind == "manual" and r.text == text for r in items)
    assert any(r.required and r.check_kind == "named_behavior" for r in items)
    assert any(not r.required and r.text == text for r in items)
    assert not requirements_module().requirements_complete(items, [], current_binding())
    assert items == frozen(text)
    assert len({r.id for r in items}) == len(items)
    assert all(r.origin_prompt_sha256 == hashlib.sha256(text.encode()).hexdigest() for r in items)


@pytest.mark.parametrize("text", [PROMPT + " ", " " + PROMPT, PROMPT.upper(),
                                  PROMPT + ".", "Please add " + PROMPT, "unrecognized wish"])
def test_only_exact_finite_requests_can_omit_manual_whole_scope(text):
    assert any(r.required and r.check_kind == "manual" and r.text == text for r in frozen(text))


def test_canonical_scope_keeps_all_existing_fidelity_obligations():
    items = frozen(spec=FidelitySpec(dark_mode=False, primary_family="violet",
                                    sections=("catalog", "reviews"), tone="calm"))
    assert len([r for r in items if r.required and r.check_kind == "manual"]) == 5
    assert sum(r.required and r.check_kind == "named_behavior" for r in items) == 1


@pytest.mark.parametrize("text", ["task_density_v1", "header_theme_v1", "coffee_local_summary_v1"])
def test_exact_supported_grammar_has_no_invented_manual_obligation(text):
    items = frozen(text)
    assert any(not r.required and r.text == text for r in items)
    assert [(r.text, r.check_kind) for r in items if r.required] == [(text, "named_behavior")]


def test_manifest_digest_is_stable_but_binds_every_original_byte():
    rr = requirements_module()
    run = NS(id=uuid4(), prompt_hash=hashlib.sha256(PROMPT.encode()).hexdigest(), agent_state={})
    named = behavior.required_contract(PROMPT, template="max_miniapp")
    rr.store_frozen_requirements(run, PROMPT, FidelitySpec(), named)
    saved = copy.deepcopy(run.agent_state)
    assert rr.frozen_requirements(run) == frozen()
    rr.store_frozen_requirements(run, PROMPT, FidelitySpec(), named)
    assert run.agent_state == saved
    run.agent_state[rr.STATE_KEY]["original_prompt"] += " "
    with pytest.raises(RecoveryContractError):
        rr.frozen_requirements(run)


def test_model_cannot_mark_requirement_passed():
    items, binding = frozen(), current_binding()
    requirement = next(r for r in items if r.required)
    witness = RequirementWitness(requirement_id=requirement.id, receipt_id="invented",
                                 source_sha256=binding.source_sha256,
                                 schema_sha256=binding.schema_sha256,
                                 environment_sha256=binding.environment_sha256, passed=True)
    assert not requirements_module().requirements_complete(items, [witness], binding)
    decoded = RequirementWitness.model_validate_json(witness.model_dump_json())
    assert not requirements_module().requirements_complete(items, [decoded], binding)


@pytest.fixture
async def receipt_case(monkeypatch):
    rr = requirements_module()
    engine, metadata = create_engine("sqlite://"), MetaData()
    models = (GenerationRun, Project, ProjectCellWorkspace, ProjectCellCandidate,
              ProjectCellProof, ProjectCellOperation, ProjectCellActivityLease)
    for model in models:
        Table(model.__tablename__, metadata, *(
            Column(c.name, JSON() if isinstance(c.type, JSONB) else c.type,
                   primary_key=c.primary_key) for c in model.__table__.columns
        ))
    metadata.create_all(engine)
    sync = Session(engine, expire_on_commit=False)

    class BoundarySession:
        @asynccontextmanager
        async def begin_nested(self):
            with sync.begin_nested():
                yield

        async def get(self, model, key, **kwargs):
            return sync.get(model, key, **kwargs)

        async def scalar(self, statement):
            return sync.scalar(statement)

        async def scalars(self, statement):
            return sync.scalars(statement)

    owner, project_id, run_id, workspace_id, candidate_id = (uuid4() for _ in range(5))
    binding = current_binding().model_copy(update={"workspace_id": str(workspace_id)})
    named = behavior.required_contract(PROMPT, template="max_miniapp")
    identity = ProofIdentity(workspace_id, run_id, 7, binding.source_sha256, "d" * 64,
                             binding.schema_sha256, "a" * 64, "e" * 64, "e" * 64,
                             "profile-v1", "f" * 64)
    bound = behavior.CandidateBehaviorBinding(candidate_id, project_id, workspace_id, run_id,
        7, identity.proof_key, binding.source_sha256, "build/sha256/" + "c" * 64,
        named.request_sha256, named.contract_digest, "owner_preview")
    registered, _ = driver(bound)
    receipt = await behavior.observe_candidate(bound, named, registered,
                                               behavior.PrivatePreviewCapability(NS()))
    project = Project(id=project_id, owner_id=owner, template="max_miniapp")
    run = GenerationRun(id=run_id, project_id=project_id, user_id=owner, status="running",
                        prompt_hash=named.request_sha256, idempotency_key="original")
    run.agent_state = {behavior.STATE_KEY: named.to_json(), behavior.RECEIPT_KEY: receipt}
    workspace = ProjectCellWorkspace(id=workspace_id, project_id=project_id, owner_id=owner,
                                     generation_run_id=run_id, fencing_epoch=7, state="ready")
    candidate = ProjectCellCandidate(id=candidate_id, workspace_id=workspace_id,
        generation_run_id=run_id, fencing_epoch=7, source_revision=binding.source_sha256,
        build_ref=bound.build_ref, status="prepared", cancelled=False)
    proof = ProjectCellProof(id=uuid4(), **{
        key: getattr(identity, key) for key in identity.__dataclass_fields__
    }, proof_key=identity.proof_key)
    sync.add_all([project, run, workspace, candidate, proof])
    sync.commit()
    rr.store_frozen_requirements(run, PROMPT, FidelitySpec(), named)
    sync.commit()
    physical = ProjectCellWorkspaceIdentity(binding.source_sha256, "d" * 64,
        binding.schema_sha256, "a" * 64, binding.environment_sha256, "f" * 64)

    async def identity_reader(*args, **kwargs):
        return case.physical

    monkeypatch.setattr(rr, "project_cell_agent_identity", identity_reader)

    async def sqlite_advisory_boundary(session, workspace_arg):
        # PostgreSQL locking is tested with actual owning callers separately.
        assert workspace_arg == workspace_id

    monkeypatch.setattr("yleum_api.services.project_cells._advisory_lock",
                        sqlite_advisory_boundary)
    case = NS(rr=rr, sync=sync, session=BoundarySession(), project=project, run=run,
              workspace=workspace, candidate=candidate, proof=proof, binding=binding,
              physical=physical, receipt=receipt, requirements=frozen(),
              receipt_id="named_behavior:" + receipt["receipt_digest"])
    yield case
    sync.close()
    engine.dispose()


async def loaded(case):
    requirement = next(r for r in case.requirements if r.check_kind == "named_behavior")
    return await case.rr.load_requirement_witness(case.session, run_id=case.run.id,
        requirement_id=requirement.id, receipt_id=case.receipt_id, binding=case.binding)


async def test_server_loaded_current_receipt_completes_exact_supported_contract(receipt_case):
    c = receipt_case
    witness = await loaded(c)
    assert c.rr.requirements_complete(c.requirements, [witness], c.binding)
    assert not c.rr.requirements_complete(c.requirements, [copy.copy(witness)], c.binding)
    decoded = RequirementWitness.model_validate_json(witness.model_dump_json())
    assert not c.rr.requirements_complete(c.requirements, [decoded], c.binding)
    assert c.rr.requirements_complete(c.requirements, [await loaded(c)], c.binding)


async def test_receipt_cannot_certify_a_caller_reduced_manifest(receipt_case):
    c = receipt_case
    witness = await loaded(c)
    subset = tuple(r for r in c.requirements if r.required)
    assert not c.rr.requirements_complete(subset, [witness], c.binding)


async def test_old_receipt_cannot_choose_itself_over_another_current_candidate(receipt_case):
    c = receipt_case
    c.sync.add(ProjectCellCandidate(id=uuid4(), workspace_id=c.workspace.id,
        generation_run_id=c.run.id, fencing_epoch=7, source_revision=c.binding.source_sha256,
        build_ref="build/sha256/" + "0" * 64, status="prepared", cancelled=False))
    c.sync.commit()
    with pytest.raises(RecoveryContractError):
        await loaded(c)


async def test_trusted_named_pass_does_not_approve_manual_design_obligations(receipt_case):
    c = receipt_case
    named = behavior.contract_from_json(c.run.agent_state[behavior.STATE_KEY])
    c.run.agent_state = {key: value for key, value in c.run.agent_state.items()
                         if key != c.rr.STATE_KEY}
    c.requirements = c.rr.store_frozen_requirements(c.run, PROMPT, FidelitySpec(tone="calm"), named)
    c.sync.commit()
    witness = await loaded(c)
    assert witness.passed
    assert not c.rr.requirements_complete(c.requirements, [witness], c.binding)


async def test_manual_reference_cannot_load_a_technical_or_named_pass(receipt_case):
    c = receipt_case
    manual = next(r for r in c.requirements if r.check_kind == "manual")
    with pytest.raises(RecoveryContractError):
        await c.rr.load_requirement_witness(c.session, run_id=c.run.id,
            requirement_id=manual.id, receipt_id=c.receipt_id, binding=c.binding)


async def test_retry_cannot_replace_frozen_scope_with_later_additions(receipt_case):
    c = receipt_case
    original = copy.deepcopy(c.run.agent_state[c.rr.STATE_KEY])
    c.project.discovery_spec = {"tone": "a later proposed design"}
    c.sync.commit()
    await c.rr.freeze_for_run(c.session, run_id=c.run.id, project_id=c.project.id,
        user_id=c.run.user_id, prompt=PROMPT, template="max_miniapp")
    assert c.run.agent_state[c.rr.STATE_KEY] == original


def test_parent_link_is_immutable_and_separate_from_original_prompt_hash():
    rr = requirements_module()
    parent = uuid4()
    run = NS(id=uuid4(), prompt_hash=hashlib.sha256(PROMPT.encode()).hexdigest(),
             agent_state={rr.AMENDMENT_KEY: {"version": 1, "supersedes_run_id": str(parent)}})
    named = behavior.required_contract(PROMPT, template="max_miniapp")
    rr.store_frozen_requirements(run, PROMPT, FidelitySpec(), named)
    assert rr.superseded_run_id(run) == parent
    run.agent_state[rr.AMENDMENT_KEY] = {"version": 1, "supersedes_run_id": str(uuid4())}
    with pytest.raises(RecoveryContractError):
        rr.frozen_requirements(run)
    assert run.prompt_hash == hashlib.sha256(PROMPT.encode()).hexdigest()


@pytest.mark.parametrize("field,value", [("source_sha256", "0" * 64),
    ("schema_sha256", "0" * 64), ("environment_sha256", "0" * 64),
    ("workspace_id", str(uuid4())), ("fencing_epoch", 8)])
async def test_old_source_witness_does_not_certify_new_candidate(receipt_case, field, value):
    c = receipt_case
    witness = await loaded(c)
    changed = c.binding.model_copy(update={field: value})
    assert not c.rr.requirements_complete(c.requirements, [witness], changed)


@pytest.mark.parametrize("broken", ["owner", "run", "candidate", "proof", "physical",
                                     "receipt", "requirement", "request", "template"])
async def test_loader_rejects_wrong_authoritative_binding(receipt_case, broken):
    c = receipt_case
    if broken == "owner":
        c.project.owner_id = uuid4()
    elif broken == "run":
        c.workspace.generation_run_id = uuid4()
    elif broken == "candidate":
        c.candidate.build_ref = "build/sha256/" + "0" * 64
    elif broken == "proof":
        c.proof.schema_data_digest = "0" * 64
    elif broken == "physical":
        from dataclasses import replace
        c.physical = replace(c.physical, environment_digest="0" * 64)
    elif broken == "receipt":
        c.receipt_id = "named_behavior:" + "0" * 64
    elif broken == "request":
        c.run.prompt_hash = "0" * 64
    elif broken == "template":
        c.project.template = "blank"
    else:
        c.requirements = frozen("header_theme_v1")
    c.sync.commit()
    with pytest.raises((RecoveryContractError, behavior.BehaviorProofError)):
        await loaded(c)


def test_frozen_manifest_cannot_change_or_be_replaced_on_retry():
    rr = requirements_module()
    run = NS(id=uuid4(), prompt_hash=hashlib.sha256(PROMPT.encode()).hexdigest(), agent_state={})
    named = behavior.required_contract(PROMPT, template="max_miniapp")
    rr.store_frozen_requirements(run, PROMPT, FidelitySpec(), named)
    original = copy.deepcopy(run.agent_state)
    rr.store_frozen_requirements(run, PROMPT, FidelitySpec(), named)
    assert run.agent_state == original
    with pytest.raises(RecoveryContractError):
        rr.store_frozen_requirements(run, PROMPT, FidelitySpec(tone="new"), named)
    assert run.agent_state == original


def test_amendment_link_is_optional_and_explicitly_typed():
    from yleum_api.schemas.message import PromptRequest

    predecessor = uuid4()
    assert PromptRequest(prompt="change").supersedes_run_id is None
    payload = PromptRequest(prompt="change", supersedes_run_id=predecessor)
    assert payload.supersedes_run_id == predecessor


def released(case):
    from datetime import UTC, datetime

    from yleum_api.services.orchestrator_client import ProjectCellResourceResponse
    from yleum_api.services.project_cells import _canonical_operation_envelope

    case.run.status = "cancelled"
    case.workspace.generation_run_id = None
    envelope, digest = _canonical_operation_envelope(case.workspace.id, case.run.id, "release", {})
    response = ProjectCellResourceResponse(case.workspace.id, "resources_ready", None, 7,
                                          None, True, True, True, True)
    release = ProjectCellOperation(id=uuid4(), workspace_id=case.workspace.id,
        generation_run_id=case.run.id, idempotency_key="release", request_digest=digest,
        request_payload=envelope, kind="release", status="completed", fencing_epoch=7,
        result_payload=response.to_wire_json(), finished_at=datetime.now(UTC))
    case.sync.add(release)
    case.sync.commit()
    return release


async def test_terminal_predecessor_requires_real_physical_release(receipt_case):
    c = receipt_case
    c.run.status = "cancelled"
    c.sync.commit()
    with pytest.raises(RecoveryContractError):
        await c.rr.require_quiesced_predecessor(c.session, project_id=c.project.id,
            user_id=c.run.user_id, predecessor_id=c.run.id)
    released(c)
    await c.rr.require_quiesced_predecessor(c.session, project_id=c.project.id,
        user_id=c.run.user_id, predecessor_id=c.run.id)


@pytest.mark.parametrize("sqlstate", ["55P03", "40P01", "08006"])
async def test_only_busy_evidence_lock_becomes_an_amendment_refusal(
    receipt_case, monkeypatch, sqlstate,
):
    c = receipt_case
    released(c)

    class DatabaseFailure(Exception):
        pass

    original_error = DatabaseFailure("evidence row acquisition failed")
    original_error.sqlstate = sqlstate
    failure = DBAPIError("SELECT locked evidence", None, original_error)
    original_scalar = c.session.scalar

    async def acquire(statement):
        if statement.column_descriptions[0]["entity"] is GenerationRun:
            raise failure
        return await original_scalar(statement)

    monkeypatch.setattr(c.session, "scalar", acquire)
    expected = RecoveryContractError if sqlstate == "55P03" else DBAPIError
    with pytest.raises(expected) as rejected:
        await c.rr.require_quiesced_predecessor(c.session, project_id=c.project.id,
            user_id=c.run.user_id, predecessor_id=c.run.id)
    if sqlstate != "55P03":
        assert rejected.value is failure


@pytest.mark.parametrize("broken", ["owner", "project", "active", "workspace", "lease",
                                     "operation", "unknown", "release", "epoch"])
async def test_link_rejects_unsettled_or_foreign_predecessor(receipt_case, broken):
    from datetime import UTC, datetime

    c = receipt_case
    release = released(c)
    if broken == "owner":
        c.project.owner_id = uuid4()
    elif broken == "project":
        c.run.project_id = uuid4()
    elif broken == "active":
        c.run.status = "running"
    elif broken == "workspace":
        c.workspace.generation_run_id = c.run.id
    elif broken == "lease":
        c.sync.add(ProjectCellActivityLease(operation_id=uuid4(), workspace_id=c.workspace.id,
            generation_run_id=c.run.id, kind="promotion", state="active", fencing_epoch=7,
            started_at=datetime.now(UTC), heartbeat_at=datetime.now(UTC),
            deadline_at=datetime.now(UTC)))
    elif broken in {"operation", "unknown"}:
        c.sync.add(ProjectCellOperation(id=uuid4(), workspace_id=c.workspace.id,
            generation_run_id=c.run.id, kind="ensure", idempotency_key="unknown",
            request_digest="a" * 64, status="indeterminate" if broken == "unknown" else "running"))
    elif broken == "epoch":
        c.workspace.fencing_epoch = 8
    else:
        release.result_payload = {**release.result_payload, "workspace_id": str(uuid4())}
    c.sync.commit()
    with pytest.raises(RecoveryContractError):
        await c.rr.require_quiesced_predecessor(c.session, project_id=c.project.id,
            user_id=c.run.user_id, predecessor_id=c.run.id)
