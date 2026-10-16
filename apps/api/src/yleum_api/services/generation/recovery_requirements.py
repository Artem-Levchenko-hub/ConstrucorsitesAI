"""Immutable request scope and loader-verified references to functional receipts.

This records obligations only. The product completion gate remains unchanged.
Ordinary prose is unresolved until an actual whole-request acceptance exists.
"""
from __future__ import annotations

import hashlib
import weakref
from collections.abc import Sequence
from types import SimpleNamespace
from typing import Any, Literal, cast
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

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
    RecoveryRequirement,
    RequirementWitness,
)
from yleum_api.services.orchestrator_client import project_cell_agent_identity
from yleum_api.services.project_cell_proofs import proof_identity_from_model

STATE_KEY = "recovery_requirements_v1"
AMENDMENT_KEY = "generation_amendment_v1"
# Entire byte-exact request grammar; never a keyword/subtraction classifier.
_CANONICAL = frozenset({"task_density_v1", "header_theme_v1", "coffee_local_summary_v1"})
_trusted: dict[int, tuple[
    weakref.ReferenceType[RequirementWitness], RecoveryRequirement, RecoveryBinding,
    tuple[RecoveryRequirement, ...],
]] = {}


def _need(condition: object, message: str) -> None:
    if not condition:
        raise RecoveryContractError(message)


def freeze_requirements(
    prompt: str, spec: FidelitySpec, named: behavior.NamedBehaviorContract | None,
) -> tuple[RecoveryRequirement, ...]:
    """Preserve full scope without treating conservative selectors as exhaustive."""
    origin = hashlib.sha256(prompt.encode()).hexdigest()
    if named is not None:
        _need(named == behavior.required_contract(prompt, template=named.template),
              "frozen named contract differs from original request")
    canonical = (
        prompt in _CANONICAL and named is not None and named.capabilities == (prompt,)
        and named.request_sha256 == origin
    )
    records: list[RecoveryRequirement] = []

    def add(
        label: str, text: str, kind: Literal["manual", "named_behavior"], required: bool,
    ) -> None:
        identifier = behavior.digest({"origin": origin, "label": label,
                                      "text": text, "kind": kind, "required": required})
        records.append(RecoveryRequirement(id=identifier, text=text,
            origin_prompt_sha256=origin, check_kind=kind, required=required))

    add("original_request", prompt, "manual", False)
    if not canonical:
        add("unresolved_whole_request", prompt, "manual", True)
    for label, text in spec.requirement_axes():
        add(label, text, "manual", True)
    if named is not None:
        for capability in sorted(named.capabilities):
            add("named:" + capability, capability, "named_behavior", True)
    return tuple(records)


def _manifest(
    run: GenerationRun, prompt: str, spec: FidelitySpec,
    named: behavior.NamedBehaviorContract | None,
) -> dict[str, Any]:
    _need(hashlib.sha256(prompt.encode()).hexdigest() == run.prompt_hash,
          "requirements must use original admission prompt")
    parent = superseded_run_id(run)
    payload: dict[str, Any] = {
        "version": 1, "run_id": str(run.id), "prompt_hash": run.prompt_hash,
        "supersedes_run_id": str(parent) if parent else None,
        "original_prompt": prompt, "spec": spec.to_dict(),
        "named": named.to_json() if named is not None else None,
        "requirements": [r.model_dump(mode="json")
                         for r in freeze_requirements(prompt, spec, named)],
    }
    return {**payload, "manifest_sha256": behavior.digest(payload)}


def superseded_run_id(run: GenerationRun) -> UUID | None:
    root = run.agent_state or {}
    if AMENDMENT_KEY not in root:
        return None
    raw = root[AMENDMENT_KEY]
    try:
        _need(type(raw) is dict and set(raw) == {"version", "supersedes_run_id"}
              and type(raw["version"]) is int and raw["version"] == 1,
              "corrupt amendment identity")
        assert isinstance(raw, dict)
        parent = UUID(raw["supersedes_run_id"])
        _need(str(parent) == raw["supersedes_run_id"] and parent != run.id,
              "invalid amendment parent")
        return parent
    except (KeyError, ValueError, TypeError, AttributeError) as exc:
        raise RecoveryContractError("invalid amendment identity") from exc


def store_frozen_requirements(
    run: GenerationRun, prompt: str, spec: FidelitySpec,
    named: behavior.NamedBehaviorContract | None,
) -> tuple[RecoveryRequirement, ...]:
    """Initialize once; never repair/replace a different or corrupt manifest."""
    expected = _manifest(run, prompt, spec, named)
    root = dict(run.agent_state or {})
    if STATE_KEY in root:
        _need(root[STATE_KEY] == expected, "frozen requirements changed or are corrupt")
    else:
        root[STATE_KEY] = expected
        run.agent_state = root
    return freeze_requirements(prompt, spec, named)


def frozen_requirements(run: GenerationRun) -> tuple[RecoveryRequirement, ...]:
    raw = (run.agent_state or {}).get(STATE_KEY)
    try:
        _need(type(raw) is dict and type(raw.get("version")) is int and raw["version"] == 1,
              "missing or unsupported frozen requirements")
        assert isinstance(raw, dict)
        named = behavior.contract_from_json(raw["named"]) if raw["named"] is not None else None
        spec = FidelitySpec.from_dict(raw["spec"])
        expected = _manifest(run, raw["original_prompt"], spec, named)
        _need(raw == expected, "frozen requirements changed or are corrupt")
        return freeze_requirements(raw["original_prompt"], spec, named)
    except (KeyError, TypeError, ValueError, behavior.BehaviorProofError) as exc:
        raise RecoveryContractError("invalid frozen requirements") from exc


async def freeze_for_run(
    session: AsyncSession, *, run_id: UUID, project_id: UUID, user_id: UUID,
    prompt: str, template: str,
) -> None:
    """Freeze before planner/agent work; this never executes acceptance or a model."""
    project = await session.get(Project, project_id, populate_existing=True, with_for_update=True)
    run = await session.get(GenerationRun, run_id, populate_existing=True, with_for_update=True)
    behavior.need(project is not None and project.owner_id == user_id and run is not None
          and run.project_id == project_id and run.user_id == user_id,
          "BEHAVIOR_REQUEST_BINDING_INVALID")
    assert run is not None and project is not None
    original = await behavior.original_request(session, run, prompt,
                                               project_id=project_id, run_id=run_id)
    if STATE_KEY in (run.agent_state or {}):
        # Discovery/project context may evolve after initialization; the saved
        # contract remains authoritative, rather than recompiling it on retry.
        frozen_requirements(run)
        return
    spec = FidelitySpec.from_dict(project.discovery_spec)
    store_frozen_requirements(run, original, spec,
                              behavior.required_contract(original, template=template))
    await session.commit()


async def load_requirement_witness(
    session: AsyncSession, *, run_id: UUID, requirement_id: str, receipt_id: str,
    binding: RecoveryBinding,
) -> RequirementWitness:
    """Reload the owning server receipt; model data/technical green are not proof."""
    # Scope hint only; all facts are refreshed under project -> run -> workspace locks.
    hint = await session.get(GenerationRun, run_id)
    _need(hint is not None, "requirement run missing")
    assert hint is not None
    project = await session.get(Project, hint.project_id,
                                populate_existing=True, with_for_update=True)
    run = await session.get(GenerationRun, run_id, populate_existing=True, with_for_update=True)
    _need(project is not None and run is not None and project.id == run.project_id
          and project.owner_id == run.user_id, "requirement receipt owner conflict")
    assert run is not None and project is not None
    manifest = frozen_requirements(run)
    requirement = next((r for r in manifest if r.id == requirement_id), None)
    _need(requirement is not None and requirement.check_kind == "named_behavior",
          "requirement has no supported functional receipt")
    assert requirement is not None
    try:
        workspace_id = UUID(binding.workspace_id)
        _need(str(workspace_id) == binding.workspace_id, "invalid workspace identity")
        workspace = await session.get(ProjectCellWorkspace, workspace_id,
                                     populate_existing=True, with_for_update=True)
        _need(workspace is not None and workspace.project_id == run.project_id
              and workspace.owner_id == run.user_id and workspace.generation_run_id == run.id
              and workspace.fencing_epoch == binding.fencing_epoch
              and workspace.state == "ready" and workspace.deleted_at is None,
              "requirement workspace ownership or fence conflict")
        named = behavior.contract_from_json(run.agent_state[behavior.STATE_KEY])
        manifest_named = cast(dict[str, Any], run.agent_state[STATE_KEY])["named"]
        _need(named.to_json() == manifest_named and named.request_sha256 == run.prompt_hash
              and named.template == project.template
              and requirement.text in named.capabilities, "requirement request contract conflict")
        receipt = run.agent_state[behavior.RECEIPT_KEY]
        _need(type(receipt) is dict and receipt_id == "named_behavior:" + receipt["receipt_digest"],
              "functional receipt reference missing")
        assert isinstance(receipt, dict)
        candidate_id = UUID(receipt["binding"]["candidate_id"])
        candidates = list((await session.scalars(select(ProjectCellCandidate).where(
            ProjectCellCandidate.workspace_id == workspace_id,
            ProjectCellCandidate.generation_run_id == run.id,
            ProjectCellCandidate.fencing_epoch == binding.fencing_epoch,
            ProjectCellCandidate.source_revision == binding.source_sha256,
            ProjectCellCandidate.cancelled.is_(False),
            ProjectCellCandidate.status.in_(("prepared", "accepted")),
        ).execution_options(populate_existing=True).with_for_update())).all())
        # Binding has no candidate/build ID. Never let an old receipt choose its
        # own candidate when the server has multiple eligible artifacts.
        _need(len(candidates) == 1 and candidates[0].id == candidate_id,
              "functional current candidate missing or ambiguous")
        candidate = candidates[0]
        proof = await session.scalar(select(ProjectCellProof).where(
            ProjectCellProof.workspace_id == workspace_id,
            ProjectCellProof.generation_run_id == run.id,
            ProjectCellProof.fencing_epoch == binding.fencing_epoch,
            ProjectCellProof.proof_key == receipt["binding"]["proof_key"],
        ).execution_options(populate_existing=True).with_for_update())
        _need(proof is not None, "functional proof identity missing")
        assert proof is not None and candidate is not None
        identity = proof_identity_from_model(proof)
        _need(identity.proof_key == proof.proof_key
              and identity.workspace_revision == binding.source_sha256
              and identity.schema_data_digest == binding.schema_sha256
              and identity.base_image_digest == binding.environment_sha256
              and identity.toolchain_digest == binding.environment_sha256,
              "functional proof source/schema/environment conflict")
        current = await project_cell_agent_identity(workspace_id,
            generation_run_id=run.id, fencing_epoch=binding.fencing_epoch)
        _need(current.workspace_revision == binding.source_sha256
              and current.schema_data_digest == binding.schema_sha256
              and current.environment_digest == binding.environment_sha256
              and current.dependency_digest == identity.dependency_digest
              and current.cell_manifest_digest == identity.cell_manifest_digest
              and current.build_config_digest == identity.build_config_digest,
              "functional current candidate conflict")
        bound = behavior.candidate_binding(SimpleNamespace(project_id=project.id), candidate,
                                            identity, candidate.build_ref, named)
        behavior.validate_saved_receipt(receipt, bound, named)
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise RecoveryContractError("functional receipt unavailable or invalid") from exc
    witness = RequirementWitness(requirement_id=requirement.id, receipt_id=receipt_id,
        source_sha256=binding.source_sha256, schema_sha256=binding.schema_sha256,
        environment_sha256=binding.environment_sha256, passed=True)
    key = id(witness)
    _trusted[key] = (weakref.ref(witness, lambda _: _trusted.pop(key, None)),
                     requirement, binding, manifest)
    return witness


def requirements_complete(
    requirements: Sequence[RecoveryRequirement], witnesses: Sequence[RequirementWitness],
    binding: RecoveryBinding,
) -> bool:
    """Only current loader-minted evidence can satisfy a required obligation."""
    if not requirements or len({r.id for r in requirements}) != len(requirements):
        return False
    for requirement in requirements:
        if not requirement.required:
            continue
        if requirement.check_kind == "manual":
            return False
        found = False
        for witness in witnesses:
            trusted = _trusted.get(id(witness))
            if (trusted is not None and trusted[0]() is witness
                    and trusted[1] == requirement and trusted[2] == binding
                    and trusted[3] == tuple(requirements) and witness.passed):
                found = True
                break
        if not found:
            return False
    return any(r.required for r in requirements)


async def require_quiesced_predecessor(
    session: AsyncSession, *, project_id: UUID, user_id: UUID, predecessor_id: UUID,
) -> None:
    """Serialize with resource operations before acquiring conflicting row/FK locks.

    Never perform cancellation/release here. Existing cleanup owns those effects.
    Legacy runs with no durable physical release evidence fail closed for links.
    """
    from yleum_api.services.project_cells import lock_workspace_operations

    # This is a lookup hint only. Project ownership, parent, workspace and all
    # evidence are refreshed below after joining the owning advisory namespace.
    workspace_id = await session.scalar(select(ProjectCellWorkspace.id).where(
        ProjectCellWorkspace.project_id == project_id, ProjectCellWorkspace.owner_id == user_id,
    ))
    _need(workspace_id is not None, "amendment predecessor physical release is unproven")
    assert workspace_id is not None
    await lock_workspace_operations(session, workspace_id)
    # Older lifecycle callers may enter reservation while holding rows. Busy
    # evidence is not quiescence: NOWAIT avoids reversing their order. A
    # savepoint restores transaction usability after a 55P03 refusal; locks
    # from successful validation remain held until outer admission commits.
    async with session.begin_nested():
        try:
            await _require_locked_predecessor_evidence(
                session, project_id=project_id, user_id=user_id,
                predecessor_id=predecessor_id, workspace_id=workspace_id,
            )
        except DBAPIError as exc:
            if getattr(exc.orig, "sqlstate", None) == "55P03":
                raise RecoveryContractError("amendment predecessor evidence is busy") from exc
            raise


async def _require_locked_predecessor_evidence(
    session: AsyncSession, *, project_id: UUID, user_id: UUID,
    predecessor_id: UUID, workspace_id: UUID,
) -> None:
    """Fresh evidence under owning serialization; never wait behind a row owner."""
    from yleum_api.services.generation_deployment_drain import reconciliation_settles
    from yleum_api.services.orchestrator_client import (
        OrchestratorUnavailable,
        ProjectCellResourceResponse,
    )
    from yleum_api.services.project_cells import ProjectCellValidationError, _stored_request_payload

    project = await session.get(Project, project_id, populate_existing=True,
                                with_for_update={"nowait": True})
    parent = await session.scalar(select(GenerationRun).where(
        GenerationRun.id == predecessor_id, GenerationRun.project_id == project_id,
        GenerationRun.user_id == user_id,
    ).execution_options(populate_existing=True).with_for_update(nowait=True))
    _need(project is not None and project.owner_id == user_id and parent is not None
          and parent.status in {"completed", "failed", "cancelled"},
          "amendment predecessor ownership or terminal state conflict")
    workspace = await session.scalar(select(ProjectCellWorkspace).where(
        ProjectCellWorkspace.id == workspace_id,
        ProjectCellWorkspace.project_id == project_id, ProjectCellWorkspace.owner_id == user_id,
    ).execution_options(populate_existing=True).with_for_update(nowait=True))
    _need(workspace is not None and workspace.generation_run_id is None
          and workspace.deleted_at is None and workspace.state == "ready",
          "amendment predecessor physical release is unproven")
    assert workspace is not None
    leases = list((await session.scalars(select(ProjectCellActivityLease).where(
        ProjectCellActivityLease.workspace_id == workspace.id,
    ).execution_options(populate_existing=True).with_for_update(nowait=True))).all())
    _need(not any(lease.state == "active" or lease.finished_at is None for lease in leases),
          "amendment predecessor activity cleanup is pending")
    operations = list((await session.scalars(select(ProjectCellOperation).where(
        ProjectCellOperation.workspace_id == workspace.id,
    ).execution_options(populate_existing=True).with_for_update(nowait=True))).all())
    _need(not any(op.status in {"pending", "waiting_capacity", "running"} for op in operations),
          "amendment predecessor resource cleanup is pending")
    _need(not any(op.status == "indeterminate" and not any(
        reconciliation_settles(op, receipt) for receipt in operations
    ) for op in operations), "amendment predecessor resource effect remains unknown")
    releases = [op for op in operations if op.generation_run_id == predecessor_id
                and op.kind == "release" and op.status == "completed"
                and op.finished_at is not None and op.fencing_epoch == workspace.fencing_epoch]
    for release in releases:
        try:
            _need(_stored_request_payload(release) == {}, "release request invalid")
            response = ProjectCellResourceResponse.from_json(dict(release.result_payload or {}))
            if (response.workspace_id == workspace.id and response.state == "resources_ready"
                    and response.fencing_epoch == workspace.fencing_epoch):
                return
        except (RecoveryContractError, ValueError, TypeError,
                OrchestratorUnavailable, ProjectCellValidationError):
            continue
    raise RecoveryContractError("amendment predecessor fenced release receipt missing")
