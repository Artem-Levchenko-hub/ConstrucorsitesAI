"""Owning SQL lifecycle for one private notes observation, including lost ACK."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from uuid import UUID, uuid5

from sqlalchemy import select

from yleum_api.models.generation_run import GenerationRun
from yleum_api.models.project import Project
from yleum_api.models.project_cell import (
    ProjectCellActivityLease,
    ProjectCellCandidate,
    ProjectCellProof,
    ProjectCellWorkspace,
)
from yleum_api.services import max_behavior_proof as behavior
from yleum_api.services.generation.notes_acceptance import (
    PROFILE,
    RECEIPT_KEY,
    NotesContract,
    need,
    reviewed_contract,
    validate_outcomes,
    validate_receipt,
)
from yleum_api.services.generation.recovery_actions import recovery_binding
from yleum_api.services.generation.recovery_contract import RecoveryBinding, RecoveryContractError
from yleum_api.services.notes_acceptance_browser import NotesDriver, runner_digest
from yleum_api.services.orchestrator_client import project_cell_agent_identity
from yleum_api.services.project_cell_activity import (
    ActivityKind,
    ActivityState,
    finish_activity,
    start_activity,
)
from yleum_api.services.project_cell_proofs import proof_identity_from_model


def _continuation_basis(run: Any) -> object:
    from yleum_api.services.generation.recovery_contract import RecoveryStateV2
    from yleum_api.services.generation.recovery_execution import recovery_state

    state = recovery_state(run)
    return (
        (state.identity, state.attempt_seq, state.continuation_claim)
        if isinstance(state, RecoveryStateV2)
        else None
    )


async def _locked_notes_run(session: Any, coordinator: Any, identity: Any) -> tuple[Any, Any]:
    from yleum_api.services.generation_deployment_drain import require_generation_admission

    await require_generation_admission(session)
    project = await session.get(
        Project, coordinator.project_id, populate_existing=True, with_for_update=True
    )
    run = await session.get(
        GenerationRun, coordinator.generation_run_id, populate_existing=True, with_for_update=True
    )
    need(
        project is not None
        and run is not None
        and run.project_id == project.id
        and run.user_id == project.owner_id
        and run.status == "running",
        "notes owner changed",
    )
    workspace = await session.get(
        ProjectCellWorkspace, identity.workspace_id, populate_existing=True, with_for_update=True
    )
    need(
        workspace is not None
        and workspace.project_id == project.id
        and workspace.owner_id == run.user_id
        and workspace.generation_run_id == run.id
        and workspace.fencing_epoch == identity.fencing_epoch
        and workspace.state == "ready"
        and workspace.deleted_at is None,
        "notes current workspace changed",
    )
    continuation = getattr(coordinator, "continuation", None)
    from yleum_api.services.generation.recovery_contract import RecoveryStateV2
    from yleum_api.services.generation.recovery_execution import (
        _require_session_owner,
        recovery_state,
    )

    state = recovery_state(run)
    if isinstance(state, RecoveryStateV2):
        await _require_session_owner(run.id)
        need(
            state.continuation_claim is not None
            and state.continuation_claim.expires_at > datetime.now(UTC)
            and state.binding == recovery_binding(identity)
            and state.phase in {"working", "repairing", "verifying"},
            "notes continuation changed",
        )
        if continuation is not None:
            need(
                state.identity == continuation.state.identity
                and state.continuation_claim == continuation.state.continuation_claim
                and state.attempt_seq == continuation.state.attempt_seq,
                "notes continuation caller changed",
            )
    return run, project


@dataclass(frozen=True)
class NotesRequest:
    binding: behavior.CandidateBehaviorBinding
    contract: NotesContract
    preview: behavior.PrivatePreviewCapability


async def require_notes_observation(
    coordinator: Any,
    candidate: Any,
    identity: Any,
    build_ref: str,
) -> None:
    """Reserve before any mutation; unknown outcome remains held, never replayed."""
    # Only the explicitly installed writer profile enters this new lifecycle.
    # Saved receipts remain readable through require_current_acceptance even
    # when the operator removes the driver; absence never grants acceptance.
    if type(coordinator.behavior_driver) is not NotesDriver:
        return
    async with coordinator.session_factory() as session:
        # Ordinary legacy runs do not acquire the new notes reservation locks.
        run = await session.get(GenerationRun, coordinator.generation_run_id)
        need(run is not None, "notes run missing")
        from yleum_api.services.generation.recovery_acceptance import acceptance_required

        if not acceptance_required(run):
            return
        project = await session.get(Project, coordinator.project_id)
        need(project is not None, "notes project missing")
        if reviewed_contract(run, project.template) is None:
            return
        run, project = await _locked_notes_run(session, coordinator, identity)
        claim_basis = _continuation_basis(run)
        contract = reviewed_contract(run, project.template)
        need(contract is not None, "notes current obligations changed")
        assert contract is not None
        bound = behavior.candidate_binding(coordinator, candidate, identity, build_ref, contract)
        source = recovery_binding(identity)
        envelope = {
            "binding": bound.to_json(),
            "source_binding": source.model_dump(mode="json"),
            "contract": contract.to_json(),
            "owner_id": str(run.user_id),
        }
        if RECEIPT_KEY in run.agent_state:
            prior = run.agent_state[RECEIPT_KEY]
            if type(prior) is dict and set(prior) == {"state", "envelope", "receipt"}:
                need(
                    prior["state"] == "confirmed" and prior["envelope"] == envelope,
                    "notes attempt changed",
                )
                validate_receipt(
                    prior["receipt"],
                    run=run,
                    bound=bound,
                    contract=contract,
                    binding=source.model_dump(mode="json"),
                )
                return
            raise behavior.BehaviorProofError("NOTES_ACCEPTANCE_OUTCOME_UNKNOWN")
        driver = coordinator.behavior_driver
        if type(driver) is not NotesDriver:
            raise behavior.BehaviorProofError("NOTES_ACCEPTANCE_DRIVER_UNAVAILABLE")
        operation_id = uuid5(run.id, "notes-acceptance:" + behavior.digest(envelope))
        root = dict(run.agent_state)
        root[RECEIPT_KEY] = {
            "state": "started",
            "envelope": envelope,
            "operation_id": str(operation_id),
        }
        run.agent_state = root
        now = datetime.now(UTC)
        await start_activity(
            session,
            workspace_id=identity.workspace_id,
            generation_run_id=run.id,
            kind=ActivityKind.FINALIZATION,
            fencing_epoch=identity.fencing_epoch,
            operation_id=operation_id,
            proof_key=identity.proof_key,
            phase="runtime_probe",
            now=now,
            deadline_at=now + timedelta(seconds=45),
        )
        await session.commit()
    # A commit exception cannot reach dispatch. A fresh caller sees started and
    # waits, even when the original process died before the first browser write.
    dispatched = False
    try:
        preview = await coordinator.executor.create_preview_session()
        request = NotesRequest(bound, contract, behavior.PrivatePreviewCapability(preview))
        before = behavior._compiled(await driver.collect_compiled(request), bound)
        if await coordinator._identity() != identity:
            raise RecoveryContractError("notes candidate changed before observation")
        async with coordinator.session_factory() as session:
            current, _ = await _locked_notes_run(session, coordinator, identity)
            need(_continuation_basis(current) == claim_basis, "notes continuation claim changed")
            from yleum_api.services.generation.recovery_readers import require_recovery_readers

            await require_recovery_readers()
            await session.commit()
        dispatched = True
        outcomes = await driver.observe(request, before, operation_id)
        validate_outcomes(outcomes)
        after = behavior._compiled(await driver.collect_compiled(request), bound)
        need(
            after == before and await coordinator._identity() == identity,
            "notes candidate changed during observation",
        )
        async with coordinator.session_factory() as session:
            run, project = await _locked_notes_run(session, coordinator, identity)
            need(_continuation_basis(run) == claim_basis, "notes continuation claim changed")
            need(
                project is not None
                and project.owner_id == run.user_id
                and reviewed_contract(run, project.template) == contract
                and run.agent_state.get(RECEIPT_KEY)
                == {"state": "started", "envelope": envelope, "operation_id": str(operation_id)},
                "notes current attempt changed",
            )
            receipt = {
                "version": 1,
                "profile": PROFILE,
                **envelope,
                "compiled": {
                    "assets": [asdict(item) for item in before.assets],
                    "platform_assets": [asdict(item) for item in before.platform_assets],
                    "compilation_receipt_sha256": before.compilation_receipt_sha256,
                    "digest": before.digest,
                },
                "outcomes": outcomes,
                "runner_sha256": runner_digest(),
            }
            receipt["receipt_digest"] = behavior.digest(receipt)
            validate_receipt(
                receipt,
                run=run,
                bound=bound,
                contract=contract,
                binding=source.model_dump(mode="json"),
            )
            root = dict(run.agent_state)
            root[RECEIPT_KEY] = {"state": "confirmed", "envelope": envelope, "receipt": receipt}
            run.agent_state = root
            await finish_activity(
                session,
                operation_id=operation_id,
                state=ActivityState.COMPLETED,
                finished_at=datetime.now(UTC),
                diagnostic="notes_observation_complete",
            )
            await session.commit()
    except BaseException as exc:
        if not dispatched:
            # The browser was never invoked: there can be no notes HTTP write.
            # Reaped browser PIDs after dispatch are NOT a server-effect receipt.
            # Logical started remains held and never grants browser replay.
            try:
                async with coordinator.session_factory() as session:
                    lease = await session.get(ProjectCellActivityLease, operation_id,
                                              populate_existing=True, with_for_update=True)
                    need(lease is not None and lease.workspace_id == identity.workspace_id
                         and lease.generation_run_id == identity.generation_run_id
                         and lease.fencing_epoch == identity.fencing_epoch,
                         "notes activity changed")
                    if lease.state == ActivityState.ACTIVE.value:
                        await finish_activity(session, operation_id=operation_id,
                            state=ActivityState.FAILED, finished_at=datetime.now(UTC),
                            diagnostic="notes_observation_closed_without_acceptance")
                    await session.commit()
            except Exception:
                pass  # unknown closure commit remains held, never inferred
        if not isinstance(exc, Exception):
            raise
        raise behavior.BehaviorProofError("NOTES_ACCEPTANCE_UNCONFIRMED") from None


def saved_receipt(run: Any, contract: NotesContract) -> dict[str, Any]:
    record = (run.agent_state or {}).get(RECEIPT_KEY)
    need(
        type(record) is dict
        and set(record) == {"state", "envelope", "receipt"}
        and record["state"] == "confirmed",
        "notes receipt unconfirmed",
    )
    assert isinstance(record, dict)
    receipt = record["receipt"]
    need(
        type(receipt) is dict
        and record["envelope"]
        == {key: receipt.get(key) for key in ("binding", "source_binding", "contract", "owner_id")}
        and receipt.get("contract") == contract.to_json(),
        "notes envelope changed",
    )
    assert isinstance(receipt, dict)
    return receipt


async def validate_owned_notes(
    session: Any,
    *,
    run: GenerationRun,
    binding: RecoveryBinding,
    historical: bool = False,
) -> dict[str, Any]:
    """Exact candidate/proof/owner reload; historical mode never grants execution."""
    project = await session.get(
        Project, run.project_id, populate_existing=True, with_for_update=True
    )
    need(project is not None and project.owner_id == run.user_id, "notes owner changed")
    contract = reviewed_contract(run, project.template)
    need(contract is not None, "notes unsupported obligations")
    assert contract is not None
    receipt = saved_receipt(run, contract)
    try:
        workspace_id = UUID(binding.workspace_id)
        need(str(workspace_id) == binding.workspace_id, "notes workspace invalid")
        candidates = list(
            (
                await session.scalars(
                    select(ProjectCellCandidate)
                    .where(
                        ProjectCellCandidate.workspace_id == workspace_id,
                        ProjectCellCandidate.generation_run_id == run.id,
                        ProjectCellCandidate.fencing_epoch == binding.fencing_epoch,
                        ProjectCellCandidate.source_revision == binding.source_sha256,
                        ProjectCellCandidate.cancelled.is_(False),
                        ProjectCellCandidate.status.in_(
                            ("accepted",) if historical else ("prepared", "accepted")
                        ),
                    )
                    .execution_options(populate_existing=True)
                    .with_for_update()
                )
            ).all()
        )
        need(
            len(candidates) == 1 and str(candidates[0].id) == receipt["binding"]["candidate_id"],
            "notes current candidate missing or ambiguous",
        )
        candidate = candidates[0]
        proof = await session.scalar(
            select(ProjectCellProof)
            .where(
                ProjectCellProof.workspace_id == workspace_id,
                ProjectCellProof.generation_run_id == run.id,
                ProjectCellProof.fencing_epoch == binding.fencing_epoch,
                ProjectCellProof.proof_key == receipt["binding"]["proof_key"],
            )
            .execution_options(populate_existing=True)
            .with_for_update()
        )
        need(proof is not None, "notes proof missing")
        identity = proof_identity_from_model(proof)
        need(
            identity.proof_key == proof.proof_key and recovery_binding(identity) == binding,
            "notes proof changed",
        )
        if not historical:
            workspace = await session.get(
                ProjectCellWorkspace, workspace_id, populate_existing=True, with_for_update=True
            )
            need(
                run.status == "running"
                and workspace is not None
                and workspace.project_id == run.project_id
                and workspace.owner_id == run.user_id
                and workspace.generation_run_id == run.id
                and workspace.fencing_epoch == binding.fencing_epoch
                and workspace.state == "ready"
                and workspace.deleted_at is None,
                "notes workspace changed",
            )
            current = await project_cell_agent_identity(
                workspace_id, generation_run_id=run.id, fencing_epoch=binding.fencing_epoch
            )
            need(
                current.workspace_revision == identity.workspace_revision
                and current.schema_data_digest == identity.schema_data_digest
                and current.environment_digest == identity.base_image_digest
                and current.environment_digest == identity.toolchain_digest
                and current.dependency_digest == identity.dependency_digest
                and current.cell_manifest_digest == identity.cell_manifest_digest
                and current.build_config_digest == identity.build_config_digest,
                "notes physical candidate changed",
            )
        bound = behavior.candidate_binding(
            SimpleNamespace(project_id=run.project_id),
            candidate,
            identity,
            candidate.build_ref,
            contract,
        )
        return validate_receipt(
            receipt,
            run=run,
            bound=bound,
            contract=contract,
            binding=binding.model_dump(mode="json"),
        )
    except (KeyError, TypeError, ValueError, AttributeError, behavior.BehaviorProofError) as exc:
        raise RecoveryContractError("notes owning receipt invalid") from exc
