"""Immutable request scope and loader-verified references to functional receipts.

This records obligations only. The product completion gate remains unchanged.
Ordinary prose is unresolved until an actual whole-request acceptance exists.
"""
from __future__ import annotations

import hashlib
import json
import weakref
from collections.abc import Sequence
from dataclasses import replace
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
    ProjectCellProofResult,
    ProjectCellWorkspace,
)
from yleum_api.services import max_behavior_proof as behavior
from yleum_api.services.build_spec import FidelitySpec
from yleum_api.services.generation.recovery_contract import (
    CompilerWitness,
    RecoveryBinding,
    RecoveryContractError,
    RecoveryIdentity,
    RecoveryRequirement,
    RequirementWitness,
)
from yleum_api.services.orchestrator_client import (
    ProjectCellResourceResponse,
    project_cell_agent_identity,
    project_cell_agent_operation_status,
)
from yleum_api.services.project_cell_proofs import ProofDimension, proof_identity_from_model

STATE_KEY = "recovery_requirements_v1"
AMENDMENT_KEY = "generation_amendment_v1"
# Entire byte-exact request grammar; never a keyword/subtraction classifier.
_CANONICAL = frozenset({"task_density_v1", "header_theme_v1", "coffee_local_summary_v1"})
_trusted: dict[int, tuple[
    weakref.ReferenceType[RequirementWitness], RecoveryRequirement, RecoveryBinding,
    tuple[RecoveryRequirement, ...], tuple[UUID, UUID, UUID, str],
]] = {}
_trusted_compilers: dict[int, weakref.ReferenceType[CompilerWitness]] = {}


def _need(condition: object, message: str) -> None:
    if not condition:
        raise RecoveryContractError(message)


def freeze_requirements(
    prompt: str, spec: FidelitySpec, named: behavior.NamedBehaviorContract | None,
    *, semantic_prompt: str | None = None,
) -> tuple[RecoveryRequirement, ...]:
    """Preserve full scope without treating conservative selectors as exhaustive."""
    origin = hashlib.sha256(prompt.encode()).hexdigest()
    semantic = prompt if semantic_prompt is None else semantic_prompt
    if semantic != prompt:
        from yleum_api.services.reference_images import request_identity

        try:
            envelope = json.loads(prompt)
            _need(set(envelope) in ({'prompt', 'reference_images'},
                                   {'prompt', 'reference_images', 'font_selections'})
                  and envelope["prompt"] == semantic
                  and request_identity(semantic, envelope["reference_images"],
                                       envelope.get('font_selections', [])) == prompt,
                  "semantic request differs from admission identity")
        except (ValueError, TypeError, KeyError):
            raise RecoveryContractError("invalid semantic request binding") from None
    if named is not None:
        _need(behavior.frozen_contract_matches_request(
            named, semantic, template=named.template, request_sha256=origin),
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
    *, semantic_prompt: str | None = None,
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
                         for r in freeze_requirements(
                             prompt, spec, named, semantic_prompt=semantic_prompt)],
    }
    if semantic_prompt is not None and semantic_prompt != prompt:
        payload["semantic_prompt"] = semantic_prompt
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
    *, semantic_prompt: str | None = None,
) -> tuple[RecoveryRequirement, ...]:
    """Initialize once; never repair/replace a different or corrupt manifest."""
    expected = _manifest(run, prompt, spec, named, semantic_prompt=semantic_prompt)
    root = dict(run.agent_state or {})
    if STATE_KEY in root:
        _need(root[STATE_KEY] == expected, "frozen requirements changed or are corrupt")
    else:
        root[STATE_KEY] = expected
        run.agent_state = root
    return freeze_requirements(prompt, spec, named, semantic_prompt=semantic_prompt)


def frozen_requirements(run: GenerationRun) -> tuple[RecoveryRequirement, ...]:
    raw = (run.agent_state or {}).get(STATE_KEY)
    try:
        _need(type(raw) is dict and type(raw.get("version")) is int and raw["version"] == 1,
              "missing or unsupported frozen requirements")
        assert isinstance(raw, dict)
        named = behavior.contract_from_json(raw["named"]) if raw["named"] is not None else None
        spec = FidelitySpec.from_dict(raw["spec"])
        expected = _manifest(run, raw["original_prompt"], spec, named,
                             semantic_prompt=raw.get("semantic_prompt"))
        _need(raw == expected, "frozen requirements changed or are corrupt")
        return freeze_requirements(raw["original_prompt"], spec, named,
                                   semantic_prompt=raw.get("semantic_prompt"))
    except (KeyError, TypeError, ValueError, behavior.BehaviorProofError) as exc:
        raise RecoveryContractError("invalid frozen requirements") from exc


async def freeze_for_run(
    session: AsyncSession, *, run_id: UUID, project_id: UUID, user_id: UUID,
    prompt: str, template: str,
) -> str:
    """Freeze before planner/agent work and return the status read under the owner lock."""
    project = await session.get(Project, project_id, populate_existing=True, with_for_update=True)
    run = await session.get(GenerationRun, run_id, populate_existing=True, with_for_update=True)
    behavior.need(project is not None and project.owner_id == user_id and run is not None
          and run.project_id == project_id and run.user_id == user_id,
          "BEHAVIOR_REQUEST_BINDING_INVALID")
    assert run is not None and project is not None
    from yleum_api.services.generation.request_binding import original_request_binding

    request = await original_request_binding(session, run, prompt,
                                            project_id=project_id, run_id=run_id)
    status = run.status
    if STATE_KEY in (run.agent_state or {}):
        # Discovery/project context may evolve after initialization; the saved
        # contract remains authoritative, rather than recompiling it on retry.
        frozen_requirements(run)
    else:
        spec = FidelitySpec.from_dict(project.discovery_spec)
        named = behavior.required_contract(
            request.text, template=template, request_sha256=run.prompt_hash,
        )
        store_frozen_requirements(run, request.identity, spec, named, semantic_prompt=request.text)
        await session.commit()
    return status


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
    _need(requirement is not None, "requirement missing")
    assert requirement is not None
    if requirement.check_kind != "named_behavior":
        from yleum_api.services.generation.meal_acceptance import (
            RECEIPT_PREFIX,
            reviewed_meal_contract,
        )
        from yleum_api.services.generation.meal_acceptance_receipts import validate_owned_meal
        from yleum_api.services.generation.notes_acceptance import reviewed_contract
        from yleum_api.services.generation.notes_acceptance_receipts import validate_owned_notes

        meal = reviewed_meal_contract(run, project.template)
        if meal is not None:
            _need(requirement.id == meal.requirement_id, "meal requirement out of scope")
            meal_receipt = await validate_owned_meal(session, run=run, binding=binding)
            _need(receipt_id == RECEIPT_PREFIX + meal_receipt["receipt_digest"],
                  "meal receipt reference changed")
            return _trusted_witness(run, requirement, binding, manifest, receipt_id)
        contract = reviewed_contract(run, project.template)
        _need(contract is not None and requirement.id in contract.requirements,
              "requirement has no supported functional receipt")
        notes_receipt = await validate_owned_notes(session, run=run, binding=binding)
        _need(receipt_id == "notes_acceptance:" + notes_receipt["receipt_digest"],
              "notes receipt reference changed")
        return _trusted_witness(run, requirement, binding, manifest, receipt_id)
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
    return _trusted_witness(run, requirement, binding, manifest, receipt_id)


def _trusted_witness(
    run: GenerationRun, requirement: RecoveryRequirement, binding: RecoveryBinding,
    manifest: tuple[RecoveryRequirement, ...], receipt_id: str,
) -> RequirementWitness:
    witness = RequirementWitness(requirement_id=requirement.id, receipt_id=receipt_id,
        source_sha256=binding.source_sha256, schema_sha256=binding.schema_sha256,
        environment_sha256=binding.environment_sha256, passed=True)
    key = id(witness)
    _trusted[key] = (weakref.ref(witness, lambda _: _trusted.pop(key, None)),
                     requirement, binding, manifest,
                     (run.id, run.project_id, run.user_id, run.prompt_hash))
    return witness


def requirement_witness_is_trusted(
    witness: RequirementWitness, *, requirements: Sequence[RecoveryRequirement],
    binding: RecoveryBinding, identity: RecoveryIdentity | None = None,
) -> bool:
    """Reuse loader authority for one obligation without weakening full acceptance."""
    trusted = _trusted.get(id(witness))
    requirement = next((r for r in requirements if r.id == witness.requirement_id), None)
    return bool(
        trusted is not None and trusted[0]() is witness
        and requirement is not None and trusted[1] == requirement
        and trusted[2] == binding and trusted[3] == tuple(requirements) and witness.passed
        and (identity is None or trusted[4] == (
            identity.run_id, identity.project_id, identity.user_id, identity.prompt_hash))
    )


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
        if not any(
            witness.requirement_id == requirement.id and requirement_witness_is_trusted(
                witness, requirements=requirements, binding=binding,
            ) for witness in witnesses
        ):
            return False
    return any(r.required for r in requirements)


async def load_compiler_witness(
    session: AsyncSession, *, run_id: UUID, receipt_id: UUID, binding: RecoveryBinding,
) -> CompilerWitness:
    """Read the owning check journal and current physical identity; never model JSON.

    The bounded RED detail is deliberately not parsed into an alleged complete
    error set. The receipt proves only a completed red/green check, under the
    same project -> run -> workspace lock order as functional receipt loading.
    """
    hint = await session.get(GenerationRun, run_id)
    _need(hint is not None, "compiler run missing")
    assert hint is not None
    project = await session.get(Project, hint.project_id,
                                populate_existing=True, with_for_update=True)
    run = await session.get(GenerationRun, run_id, populate_existing=True, with_for_update=True)
    _need(project is not None and run is not None and project.id == run.project_id
          and project.owner_id == run.user_id, "compiler receipt owner conflict")
    assert project is not None and run is not None
    try:
        workspace_id = UUID(binding.workspace_id)
        _need(str(workspace_id) == binding.workspace_id, "invalid compiler workspace identity")
        workspace = await session.get(ProjectCellWorkspace, workspace_id,
                                     populate_existing=True, with_for_update=True)
        _need(workspace is not None and workspace.project_id == run.project_id
              and workspace.owner_id == run.user_id and workspace.generation_run_id == run.id
              and workspace.fencing_epoch == binding.fencing_epoch
              and workspace.state == "ready" and workspace.deleted_at is None,
              "compiler workspace ownership or fence conflict")
        result = await session.get(ProjectCellProofResult, receipt_id,
                                   populate_existing=True, with_for_update=True)
        _need(result is not None and result.workspace_id == workspace_id
              and result.dimension in {"fast_check", "full_build"}
              and result.outcome in {"red", "green"}, "compiler terminal receipt missing")
        assert result is not None
        proof = await session.get(ProjectCellProof, result.proof_id,
                                  populate_existing=True, with_for_update=True)
        _need(proof is not None and proof.workspace_id == workspace_id
              and proof.generation_run_id == run.id
              and proof.fencing_epoch == binding.fencing_epoch, "compiler proof scope conflict")
        assert proof is not None
        physical = proof_identity_from_model(proof)
        _need(physical.proof_key == proof.proof_key
              and physical.workspace_revision == binding.source_sha256
              and physical.schema_data_digest == binding.schema_sha256
              and physical.base_image_digest == binding.environment_sha256
              and physical.toolchain_digest == binding.environment_sha256
              and result.dimension_key == physical.dimension_key(ProofDimension(result.dimension))
              and result.detail_digest
              == hashlib.sha256(result.redacted_detail.encode()).hexdigest(),
              "compiler proof binding or receipt digest conflict")
        lease = await session.get(ProjectCellActivityLease, result.operation_id,
                                  populate_existing=True, with_for_update=True)
        _need(lease is not None and lease.workspace_id == workspace_id
              and lease.generation_run_id == run.id and lease.kind == "command"
              and lease.fencing_epoch == binding.fencing_epoch
              and lease.phase
              == ("fast_check" if result.dimension == "fast_check" else "final_build")
              and lease.state == ("completed" if result.outcome == "green" else "failed")
              and lease.finished_at is not None, "compiler operation is not terminal or owned")
        assert lease is not None
        activity_identity_matches = lease.proof_key == proof.proof_key
        if not activity_identity_matches and result.dimension == "full_build":
            from yleum_api.services.project_cell_executor import (
                FAILED_FULL_BUILD_ENVIRONMENT_TRANSITION,
                FailedFullBuildEnvironmentTransition,
                FullBuildStartupTransition,
                failed_full_build_response_transition,
                full_build_response_transition,
            )
            from yleum_api.services.promotion_permit import MAX_FULL_BUILD_CONTRACT_VERSION

            transition: (
                FullBuildStartupTransition | FailedFullBuildEnvironmentTransition | None
            ) = None
            failed_rebind = result.outcome == "red" and result.artifact_ref is None and (
                result.redacted_detail.startswith(
                    f"[build-contract:{MAX_FULL_BUILD_CONTRACT_VERSION}]\n"
                    "[source-repair:confirmed]\n" + FAILED_FULL_BUILD_ENVIRONMENT_TRANSITION + "\n"
                )
            )
            if result.outcome == "green" or failed_rebind:
                status = await project_cell_agent_operation_status(
                    workspace_id, result.operation_id,
                )
                response = status.terminal_response
                if (status.operation_id == result.operation_id and response is not None
                        and response.operation_id == result.operation_id):
                    if result.outcome == "green" and status.state == "completed":
                        transition = full_build_response_transition(
                            response, physical, project_id=run.project_id, owner_id=run.user_id,
                        )
                    elif failed_rebind and status.state == "failed":
                        transition = failed_full_build_response_transition(
                            response, physical, project_id=run.project_id, owner_id=run.user_id,
                        )
            activity_identity_matches = bool(
                transition is not None and transition.before.proof_key == lease.proof_key
                and transition.operation_id == result.operation_id
            )
        _need(activity_identity_matches, "compiler operation is not terminal or owned")
        current = await project_cell_agent_identity(workspace_id,
            generation_run_id=run.id, fencing_epoch=binding.fencing_epoch)
        _need(current.workspace_revision == physical.workspace_revision
              and current.schema_data_digest == physical.schema_data_digest
              and current.environment_digest == binding.environment_sha256
              and current.dependency_digest == physical.dependency_digest
              and current.cell_manifest_digest == physical.cell_manifest_digest
              and current.build_config_digest == physical.build_config_digest,
              "compiler current physical identity conflict")
    except (ValueError, TypeError, AttributeError, KeyError) as exc:
        raise RecoveryContractError("compiler receipt unavailable or invalid") from exc
    witness = CompilerWitness(receipt_id=result.id, operation_id=result.operation_id,
        run_id=run.id, project_id=run.project_id, user_id=run.user_id, prompt_hash=run.prompt_hash,
        binding=binding, dimension=cast(Literal["fast_check", "full_build"], result.dimension),
        comparison_sha256=replace(physical, workspace_revision="0" * 64).proof_key,
        passed=result.outcome == "green")
    key = id(witness)
    _trusted_compilers[key] = weakref.ref(witness, lambda _: _trusted_compilers.pop(key, None))
    return witness


def compiler_witness_is_trusted(
    witness: CompilerWitness, *, identity: RecoveryIdentity, binding: RecoveryBinding,
) -> bool:
    trusted = _trusted_compilers.get(id(witness))
    return bool(trusted is not None and trusted() is witness and witness.binding == binding
        and (witness.run_id, witness.project_id, witness.user_id, witness.prompt_hash)
        == (identity.run_id, identity.project_id, identity.user_id, identity.prompt_hash))


async def require_quiesced_predecessor(
    session: AsyncSession, *, project_id: UUID, user_id: UUID, predecessor_id: UUID,
    allow_quiescent_pre_effect_ensure: bool = False,
) -> None:
    """Serialize with resource operations before acquiring conflicting row/FK locks.

    Never perform cancellation/release here. Existing cleanup owns those effects.
    Legacy runs with no durable cleanup evidence fail closed for links.
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
                allow_quiescent_pre_effect_ensure=allow_quiescent_pre_effect_ensure,
            )
        except DBAPIError as exc:
            if getattr(exc.orig, "sqlstate", None) == "55P03":
                raise RecoveryContractError("amendment predecessor evidence is busy") from exc
            raise


async def _require_locked_predecessor_evidence(
    session: AsyncSession, *, project_id: UUID, user_id: UUID,
    predecessor_id: UUID, workspace_id: UUID, allow_quiescent_pre_effect_ensure: bool,
) -> None:
    """Fresh evidence under owning serialization; never wait behind a row owner."""
    from yleum_api.services.generation_deployment_drain import reconciliation_settles
    from yleum_api.services.orchestrator_client import OrchestratorUnavailable
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
    assert workspace is not None and parent is not None
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
    if allow_quiescent_pre_effect_ensure and _has_quiescent_pre_effect_cleanup(
        workspace, parent, operations, leases,
    ):
        return
    raise RecoveryContractError("amendment predecessor fenced release receipt missing")


def _has_quiescent_pre_effect_cleanup(
    workspace: ProjectCellWorkspace, parent: GenerationRun,
    operations: Sequence[ProjectCellOperation], leases: Sequence[ProjectCellActivityLease],
) -> bool:
    """Require the current capacity no-lease proof and account for every newer fence."""
    from yleum_api.services.orchestrator_client import OrchestratorUnavailable
    from yleum_api.services.project_cells import (
        ProjectCellValidationError,
        _hashed_error,
        _stored_request_payload,
    )

    # The lifecycle dispatcher writes this digest only after matching the
    # typed pre-effect rejection to the dispatched operation, fence and request.
    rejection = _hashed_error("orchestrator_rejected:409")

    def rejected_before_effect(operation: ProjectCellOperation) -> bool:
        return (
            operation.status == "failed" and operation.error == rejection
            and _single_dispatch_finished(operation) and operation.result_payload is None
        )

    ensures = [op for op in operations if op.generation_run_id == parent.id
               and op.kind == "ensure"]
    if parent.status != "failed" or parent.finished_at is None or len(ensures) != 1:
        return False
    ensure = ensures[0]
    fence = ensure.fencing_epoch
    current = workspace.fencing_epoch
    if fence is None or not 0 < fence < current or not rejected_before_effect(ensure):
        return False
    if any(
        lease.generation_run_id == parent.id or lease.fencing_epoch >= fence for lease in leases
    ):
        return False

    history = sorted((
        op for op in operations if op.id != ensure.id and (
            op.generation_run_id == parent.id
            or (op.fencing_epoch is None and op.attempt_count > 0)
            or (op.fencing_epoch is not None and op.fencing_epoch >= fence)
        )
    ), key=lambda op: op.fencing_epoch or 0)
    release_seen = False
    observation_seen = False
    positive_fence = None
    pending_status: ProjectCellOperation | None = None
    maintenance: list[ProjectCellOperation] = []
    try:
        ensure_request = _stored_request_payload(ensure)
        profile = ensure_request.get("profile_version")
        if set(ensure_request) != {"profile_version"} or type(profile) is not str or not profile:
            return False
        for epoch, operation in enumerate(history, start=fence + 1):
            if operation.fencing_epoch != epoch or epoch > current:
                return False
            if pending_status is not None:
                if not _settles_capacity_status(workspace, pending_status, operation):
                    return False
                pending_status = None
                continue
            if operation.generation_run_id is None:
                if not release_seen or operation.execution_run_id is not None:
                    return False
                maintenance.append(operation)
                continue
            if operation.generation_run_id != parent.id or _stored_request_payload(operation) != {}:
                return False
            if (
                operation.kind == "release" and not observation_seen and not maintenance
                and rejected_before_effect(operation)
            ):
                release_seen = True
                continue
            if (
                not release_seen or operation.kind != "status"
                or operation.idempotency_key
                != f"capacity:lease-observe:{workspace.id}:{parent.id}:{epoch - 1}"
            ):
                return False
            observation_seen = True
            if operation.status == "indeterminate":
                if (
                    operation.execution_run_id is not None or operation.error is None
                    or operation.result_payload is not None
                    or not _single_dispatch_finished(operation)
                ):
                    return False
                pending_status = operation
                continue
            observation = _bound_capacity_observation(workspace, operation)
            if observation is None:
                return False
            if observation == ("resources_ready", True):
                positive_fence = epoch
        return pending_status is None and positive_fence == current and (
            not maintenance or _completed_current_checkpoint_cycle(maintenance, parent.id)
        )
    except (ValueError, TypeError, OrchestratorUnavailable, ProjectCellValidationError):
        return False


def _single_dispatch_finished(operation: ProjectCellOperation) -> bool:
    return (
        operation.attempt_count == 1 and operation.started_at is not None
        and operation.finished_at is not None and operation.started_at <= operation.finished_at
    )


def _completed_resource_response(
    operation: ProjectCellOperation, *, reconciles: UUID | None = None,
) -> ProjectCellResourceResponse | None:
    payload = operation.result_payload
    if (
        operation.status != "completed" or operation.error is not None
        or not _single_dispatch_finished(operation) or type(payload) is not dict
    ):
        return None
    if reconciles is not None:
        payload = dict(payload)
        if payload.pop("reconciles_operation_id", None) != str(reconciles):
            return None
    response = ProjectCellResourceResponse.from_json(payload)
    if (
        response.workspace_id != operation.workspace_id
        or response.fencing_epoch != operation.fencing_epoch
    ):
        return None
    return response


def _bound_capacity_observation(
    workspace: ProjectCellWorkspace, operation: ProjectCellOperation, *,
    reconciles: UUID | None = None,
) -> tuple[str, bool] | None:
    response = _completed_resource_response(operation, reconciles=reconciles)
    proof = response.lease_observation if response is not None else None
    if (
        response is None or proof is None or proof.workspace_id != workspace.id
        or proof.project_id != workspace.project_id or proof.owner_id != workspace.owner_id
        or proof.operation_id != operation.id or proof.fencing_epoch != operation.fencing_epoch
        or proof.request_digest != operation.request_digest
        or proof.active_generation_run_id is not None
        or proof.active_generation_fencing_epoch is not None
    ):
        return None
    return response.state, proof.quiescent


def _settles_capacity_status(
    workspace: ProjectCellWorkspace, target: ProjectCellOperation, receipt: ProjectCellOperation,
) -> bool:
    """Account for a lost observation using the worker's exact next reconciliation."""
    from yleum_api.services.generation_deployment_drain import reconciliation_settles

    prefix, _, requester = receipt.idempotency_key.rpartition(":")
    if (
        receipt.generation_run_id != target.generation_run_id
        or receipt.execution_run_id is not None
        or prefix != f"capacity:reconcile:{target.id}" or len(requester) != 12
        or any(char not in "0123456789abcdef" for char in requester)
        or not reconciliation_settles(target, receipt)
    ):
        return False
    observation = _bound_capacity_observation(workspace, receipt, reconciles=target.id)
    return observation is not None and observation[0] in {"resources_ready", "resources_paused"}


def _completed_current_checkpoint_cycle(
    operations: Sequence[ProjectCellOperation], predecessor_id: UUID,
) -> bool:
    """Verify support paused, restored that same checkpoint, and woke the workspace."""
    from yleum_api.services.project_cells import _stored_request_payload

    checkpoint = f"support-current-{predecessor_id.hex[:16]}"
    expected = (
        ("pause", "current-checkpoint", checkpoint, "resources_paused"),
        ("restore", "restore-current", checkpoint, "resources_paused"),
        ("wake", "wake-current", None, "resources_ready"),
    )
    if len(operations) != len(expected):
        return False
    for operation, (kind, key, ref, state) in zip(operations, expected, strict=True):
        request = {"checkpoint_ref": ref} if ref is not None else {}
        if (
            operation.kind != kind or operation.idempotency_key != f"support:{key}:{predecessor_id}"
            or _stored_request_payload(operation) != request
        ):
            return False
        response = _completed_resource_response(operation)
        if (
            response is None or response.lease_observation is not None
            or response.state != state or response.checkpoint_ref != ref
            or not all((response.has_workspace, response.has_agent_home,
                        response.has_postgres, response.has_redis))
        ):
            return False
    return True
