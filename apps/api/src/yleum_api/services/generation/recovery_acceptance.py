"""Exact-request functional acceptance. Technical green is never a witness."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from yleum_api.core.config import get_settings
from yleum_api.models.generation_run import GenerationRun
from yleum_api.models.project import Project
from yleum_api.services import max_behavior_proof as behavior
from yleum_api.services.generation.recovery_contract import (
    RecoveryBinding,
    RecoveryContractError,
    RecoveryRequirement,
    RecoveryStateV2,
    RequirementWitness,
    Sha256,
)
from yleum_api.services.generation.recovery_execution import recovery_state
from yleum_api.services.generation.recovery_requirements import (
    STATE_KEY,
    frozen_requirements,
    load_requirement_witness,
    requirement_witness_is_trusted,
)


class AcceptanceSeal(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    version: Literal[1] = 1
    run_id: UUID
    project_id: UUID
    user_id: UUID
    prompt_hash: Sha256
    manifest_sha256: Sha256
    binding: RecoveryBinding
    requirements: tuple[Sha256, ...]
    candidate_id: UUID
    proof_key: Sha256
    build_ref: str
    receipt_digest: Sha256

    @field_validator("version", mode="before")
    @classmethod
    def exact_version(cls, value: object) -> object:
        if type(value) is not int or value != 1:
            raise ValueError("unsupported acceptance seal version")
        return value


class AcceptanceSealV2(AcceptanceSeal):
    version: Literal[2] = 2  # type: ignore[assignment]
    profile: Literal["notes_acceptance_v1"] = "notes_acceptance_v1"
    contract_digest: Sha256

    @field_validator("version", mode="before")
    @classmethod
    def exact_version(cls, value: object) -> object:
        if type(value) is not int or value != 2:
            raise ValueError("unsupported acceptance seal version")
        return value


@dataclass(frozen=True)
class AcceptanceVerdict:
    ready: bool
    missing: tuple[str, ...]
    failed: tuple[str, ...]


def check_requirement_acceptance(
    requirements: Sequence[RecoveryRequirement],
    witnesses: Sequence[RequirementWitness],
    *,
    binding: RecoveryBinding,
) -> AcceptanceVerdict:
    if not requirements or len({r.id for r in requirements}) != len(requirements):
        return AcceptanceVerdict(False, ("manifest",), ())
    missing, failed = [], []
    required = [r for r in requirements if r.required]
    for requirement in required:
        matching = [w for w in witnesses if w.requirement_id == requirement.id]
        supported_kind = requirement.check_kind == "named_behavior" or (
            requirement.check_kind == "manual"
            and any(w.receipt_id.startswith("notes_acceptance:") for w in matching)
        )
        if not matching or not supported_kind:
            missing.append(requirement.id)
        elif not any(
            requirement_witness_is_trusted(
                w,
                requirements=requirements,
                binding=binding,
            )
            for w in matching
        ):
            failed.append(requirement.id)
    return AcceptanceVerdict(
        bool(required) and not missing and not failed, tuple(missing), tuple(failed)
    )


def acceptance_required(run: GenerationRun) -> bool:
    # Persisted executable obligations survive restart/writer shutdown. Corrupt
    # present state raises; a flag cannot turn it into the legacy success path.
    state = recovery_state(run)
    if isinstance(state, RecoveryStateV2):
        return True
    if state is not None:
        return False  # V1 shadow is observational; never implicitly upgraded.
    settings = get_settings()
    return settings.generation_recovery_mode == "on" or (
        settings.generation_recovery_mode == "canary"
        and str(run.project_id)
        in {value.strip() for value in settings.generation_recovery_canary_project_ids.split(",")}
    )


class RequirementAcceptanceIncomplete(behavior.BehaviorProofError):
    def __init__(self, verdict: AcceptanceVerdict):
        self.verdict = verdict
        super().__init__("REQUIREMENT_ACCEPTANCE_INCOMPLETE")


async def require_current_acceptance(
    session: AsyncSession,
    *,
    run_id: UUID,
    project_id: UUID,
    binding: RecoveryBinding,
) -> dict[str, Any] | None:
    project = await session.get(Project, project_id, populate_existing=True, with_for_update=True)
    run = await session.get(GenerationRun, run_id, populate_existing=True, with_for_update=True)
    if (
        run is None
        or project is None
        or run.project_id != project.id
        or run.user_id != project.owner_id
    ):
        raise RecoveryContractError("acceptance owner conflict")
    if not acceptance_required(run):
        return None
    if run.status != "running":
        raise RecoveryContractError("acceptance run is not active")
    requirements = frozen_requirements(run)
    state = recovery_state(run)
    if isinstance(state, RecoveryStateV2) and (
        state.requirements != requirements or state.binding != binding
    ):
        raise RecoveryContractError("acceptance original scope or current source changed")
    from yleum_api.services.generation.notes_acceptance import reviewed_contract
    from yleum_api.services.generation.notes_acceptance_receipts import saved_receipt

    receipt: Any
    notes = reviewed_contract(run, project.template)
    if notes is not None:
        try:
            receipt = saved_receipt(run, notes)
        except RecoveryContractError:
            raise RequirementAcceptanceIncomplete(AcceptanceVerdict(
                False, tuple(r.id for r in requirements if r.required), ())) from None
    else:
        receipt = (run.agent_state or {}).get(behavior.RECEIPT_KEY)
    prefix = "notes_acceptance:" if notes is not None else "named_behavior:"
    witnesses = []
    for requirement in requirements:
        if (
            requirement.required
            and (requirement.check_kind == "named_behavior" or notes is not None)
            and isinstance(receipt, dict)
        ):
            ref = receipt.get("receipt_digest")
            if isinstance(ref, str):
                witnesses.append(
                    await load_requirement_witness(
                        session,
                        run_id=run.id,
                        requirement_id=requirement.id,
                        receipt_id=prefix + ref,
                        binding=binding,
                    )
                )
    verdict = check_requirement_acceptance(requirements, witnesses, binding=binding)
    if not verdict.ready:
        raise RequirementAcceptanceIncomplete(verdict)
    assert isinstance(receipt, dict)
    return acceptance_seal(run, binding=binding, receipt=receipt)


def acceptance_seal(
    run: GenerationRun,
    *,
    binding: RecoveryBinding,
    receipt: dict[str, Any],
) -> dict[str, Any]:
    """Canonical historical binding; mint only after owning receipt validation."""
    requirements = frozen_requirements(run)
    manifest = run.agent_state[STATE_KEY]
    assert isinstance(manifest, dict)
    result = {
        "version": 1,
        "run_id": str(run.id),
        "project_id": str(run.project_id),
        "user_id": str(run.user_id),
        "prompt_hash": run.prompt_hash,
        "manifest_sha256": manifest["manifest_sha256"],
        "binding": binding.model_dump(mode="json"),
        "requirements": [r.id for r in requirements if r.required],
        "candidate_id": receipt["binding"]["candidate_id"],
        "proof_key": receipt["binding"]["proof_key"],
        "build_ref": receipt["binding"]["build_ref"],
        "receipt_digest": receipt["receipt_digest"],
    }
    if receipt.get("profile") == "notes_acceptance_v1":
        result.update(version=2, profile="notes_acceptance_v1",
                      contract_digest=behavior.digest(receipt["contract"]))
    return result


async def require_coordinator_acceptance(coordinator: Any, identity: Any) -> None:
    # OFF legacy avoids new environment restrictions entirely.
    async with coordinator.session_factory() as session:
        run = await coordinator._locked_run(session)
        if not acceptance_required(run):
            return
    from yleum_api.services.generation.recovery_actions import recovery_binding

    async with coordinator.session_factory() as session:
        await require_current_acceptance(
            session,
            run_id=coordinator.generation_run_id,
            project_id=coordinator.project_id,
            binding=recovery_binding(identity),
        )
    if await coordinator._identity() != identity:
        raise RecoveryContractError("accepted candidate changed after functional proof")


def validate_acceptance_seal(
    run: GenerationRun,
    *,
    binding: RecoveryBinding,
    seal: AcceptanceSeal | AcceptanceSealV2,
) -> dict[str, Any]:
    """Pure check of already-sealed publication data; never grants execution."""
    import json

    requirements = frozen_requirements(run)
    state = recovery_state(run)
    if not isinstance(state, RecoveryStateV2) or state.requirements != requirements:
        raise RecoveryContractError("functional publication frozen scope changed")
    if isinstance(seal, AcceptanceSealV2):
        from yleum_api.services.generation.notes_acceptance import reviewed_contract
        from yleum_api.services.generation.notes_acceptance_receipts import saved_receipt

        # Template is sealed in the reviewed contract; no current execution or
        # discovery inference occurs while checking an immutable publication.
        record = run.agent_state.get("notes_acceptance_v1")
        if not isinstance(record, dict) or not isinstance(record.get("receipt"), dict):
            raise RecoveryContractError("publication notes receipt missing")
        raw = record["receipt"]
        if not isinstance(raw.get("contract"), dict):
            raise RecoveryContractError("publication notes contract missing")
        template = raw["contract"].get("template")
        contract = reviewed_contract(run, template)
        if contract is None:
            raise RecoveryContractError("publication retains unsupported notes obligations")
        receipt = saved_receipt(run, contract)
        expected_notes = AcceptanceSealV2.model_validate_json(
            json.dumps(acceptance_seal(run, binding=binding, receipt=receipt)), strict=True)
        if seal != expected_notes:
            raise RecoveryContractError("functional publication scope changed")
        return receipt
    if not any(r.required for r in requirements) or any(
        r.required and r.check_kind != "named_behavior" for r in requirements
    ):
        raise RecoveryContractError("publication retains unsupported obligations")
    named_receipt = (run.agent_state or {}).get(behavior.RECEIPT_KEY)
    if not isinstance(named_receipt, dict):
        raise RecoveryContractError("functional publication receipt missing")
    expected = AcceptanceSeal.model_validate_json(
        json.dumps(acceptance_seal(run, binding=binding, receipt=named_receipt)), strict=True
    )
    if seal != expected:
        raise RecoveryContractError("functional publication scope changed")
    return named_receipt


async def validate_historical_acceptance(
    session: AsyncSession,
    *,
    run: GenerationRun,
    binding: RecoveryBinding,
    seal: AcceptanceSeal | AcceptanceSealV2,
) -> None:
    """Validate the gated publication's sealed evidence after physical lease release.

    No new live authority is minted here. The caller must ALSO validate the
    owning confirmed publication chain, including the accepted snapshot.
    """
    from types import SimpleNamespace

    from sqlalchemy import select

    from yleum_api.models.project_cell import ProjectCellCandidate, ProjectCellProof
    from yleum_api.services.project_cell_proofs import proof_identity_from_model

    receipt = validate_acceptance_seal(run, binding=binding, seal=seal)
    if isinstance(seal, AcceptanceSealV2):
        from yleum_api.services.generation.notes_acceptance_receipts import validate_owned_notes

        await validate_owned_notes(session, run=run, binding=binding, historical=True)
        return
    candidate = await session.get(
        ProjectCellCandidate,
        seal.candidate_id,
        populate_existing=True,
        with_for_update=True,
    )
    if candidate is None or candidate.status != "accepted" or candidate.cancelled:
        raise RecoveryContractError("functional publication candidate missing")
    proof = await session.scalar(
        select(ProjectCellProof)
        .where(
            ProjectCellProof.workspace_id == candidate.workspace_id,
            ProjectCellProof.generation_run_id == run.id,
            ProjectCellProof.fencing_epoch == binding.fencing_epoch,
            ProjectCellProof.proof_key == seal.proof_key,
        )
        .execution_options(populate_existing=True)
        .with_for_update()
    )
    if proof is None:
        raise RecoveryContractError("functional publication proof missing")
    identity = proof_identity_from_model(proof)
    from yleum_api.services.generation.recovery_actions import recovery_binding

    if identity.proof_key != proof.proof_key or recovery_binding(identity) != binding:
        raise RecoveryContractError("functional publication proof changed")
    named = behavior.contract_from_json(run.agent_state[behavior.STATE_KEY])
    manifest = run.agent_state[STATE_KEY]
    assert isinstance(manifest, dict)
    if named.to_json() != manifest["named"] or named.request_sha256 != run.prompt_hash:
        raise RecoveryContractError("functional publication request changed")
    bound = behavior.candidate_binding(
        SimpleNamespace(project_id=run.project_id), candidate, identity, seal.build_ref, named
    )
    behavior.validate_saved_receipt(receipt, bound, named)


async def require_terminal_acceptance(session: AsyncSession, run: GenerationRun) -> None:
    if not acceptance_required(run):
        return
    from yleum_api.models.generation_recovery_effect import GenerationRecoveryEffect
    from yleum_api.models.message import Message
    from yleum_api.models.snapshot import Snapshot
    from yleum_api.services.generation.agent_publication import (
        PublicationBindingV2,
        _publication_id,
        decode_publication_binding,
        publication_committed,
    )

    project = await session.get(
        Project, run.project_id, populate_existing=True, with_for_update=True
    )
    state = recovery_state(run)
    if project is None or project.owner_id != run.user_id or not isinstance(state, RecoveryStateV2):
        raise RecoveryContractError("completed functional publication authority missing")
    # This caller can retain ORM objects from before publication in another
    # session. Refresh owning rows before reusing the read-only chain validator.
    if run.assistant_message_id is not None:
        await session.get(
            Message, run.assistant_message_id, populate_existing=True, with_for_update=True
        )
    if project.current_snapshot_id is not None:
        await session.get(
            Snapshot, project.current_snapshot_id, populate_existing=True, with_for_update=True
        )
    await session.get(
        GenerationRecoveryEffect,
        _publication_id(run.id),
        populate_existing=True,
        with_for_update=True,
    )
    if not await publication_committed(session, run=run, project=project, state=state):
        raise RecoveryContractError("completed functional publication is unconfirmed")
    row = await session.get(GenerationRecoveryEffect, _publication_id(run.id))
    assert row is not None
    bound = decode_publication_binding(row.pre_binding)
    if not isinstance(bound, PublicationBindingV2):
        raise RecoveryContractError("completed functional publication acceptance missing")
    await validate_historical_acceptance(
        session, run=run, binding=bound.source, seal=bound.acceptance
    )
