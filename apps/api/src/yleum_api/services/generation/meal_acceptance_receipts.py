"""Persist observations under the same run, candidate and continuation authority."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from uuid import UUID, uuid5

from sqlalchemy import select

from yleum_api.core.config import get_settings
from yleum_api.models.generation_recovery_effect import GenerationRecoveryEffect
from yleum_api.models.generation_run import GenerationRun
from yleum_api.models.project import Project
from yleum_api.models.project_cell import (
    ProjectCellActivityLease,
    ProjectCellCandidate,
    ProjectCellProof,
    ProjectCellWorkspace,
)
from yleum_api.services import max_behavior_proof as behavior
from yleum_api.services.generation.meal_acceptance import (
    STATE_KEY,
    MealContract,
    fixture_signing_seed,
    reviewed_meal_contract,
    runner_digest,
)
from yleum_api.services.generation.notes_acceptance_receipts import (
    _continuation_basis,
)
from yleum_api.services.generation.recovery_actions import recovery_binding
from yleum_api.services.generation.recovery_contract import (
    RecoveryBinding,
    RecoveryContractError,
    RecoveryStateV2,
)
from yleum_api.services.generation.recovery_execution import _require_session_owner, recovery_state
from yleum_api.services.generation_deployment_drain import require_generation_admission
from yleum_api.services.meal_acceptance_guardian import (
    OwnedObservationProcess,
    run_owned_observation,
)
from yleum_api.services.orchestrator_client import (
    OrchestratorBadRequest,
    OrchestratorUnavailable,
    project_cell_agent_identity,
    project_cell_create_meal_acceptance_session,
)
from yleum_api.services.project_cell_activity import (
    ActivityKind,
    ActivityState,
    finish_activity,
    start_activity,
)
from yleum_api.services.project_cell_proofs import proof_identity_from_model


class MealVerificationFailed(behavior.BehaviorProofError):
    def __init__(self, check: str) -> None:
        super().__init__("MEAL_ACCEPTANCE_FAILED", "NEEDS_CHANGES")
        self.code = f"MEAL_ACCEPTANCE_FAILED:{check}"


def _need(condition: object, code: str = "meal acceptance unconfirmed") -> None:
    if not condition:
        raise RecoveryContractError(code)


async def _locked_observation_run(session: Any, coordinator: Any, identity: Any) -> tuple[Any, Any]:
    """Use the continuous session lock for ownership during long proofs."""
    await require_generation_admission(session)
    project = await session.get(
        Project, coordinator.project_id, populate_existing=True, with_for_update=True
    )
    run = await session.get(
        GenerationRun, coordinator.generation_run_id, populate_existing=True, with_for_update=True
    )
    _need(
        project is not None
        and run is not None
        and run.status == "running"
        and run.project_id == project.id
        and run.user_id == project.owner_id,
        "meal observation owner changed",
    )
    workspace = await session.get(
        ProjectCellWorkspace, identity.workspace_id, populate_existing=True, with_for_update=True
    )
    _need(
        workspace is not None
        and workspace.state == "ready"
        and workspace.deleted_at is None
        and workspace.project_id == project.id
        and workspace.owner_id == run.user_id
        and workspace.generation_run_id == run.id
        and workspace.fencing_epoch == identity.fencing_epoch,
        "meal observation workspace changed",
    )
    state = recovery_state(run)
    _need(
        isinstance(state, RecoveryStateV2)
        and state.continuation_claim is not None
        and state.binding == recovery_binding(identity)
        and state.phase in {"working", "repairing", "verifying"},
        "meal continuation changed",
    )
    await _require_session_owner(run.id)
    if (continuation := getattr(coordinator, "continuation", None)) is not None:
        _need(
            state is not None
            and state.identity == continuation.state.identity
            and state.continuation_claim == continuation.state.continuation_claim
            and state.attempt_seq == continuation.state.attempt_seq,
            "meal continuation changed",
        )
    return run, project


@dataclass(frozen=True, slots=True)
class MealRequest:
    binding: behavior.CandidateBehaviorBinding
    contract: MealContract
    prior: dict[str, Any] | None = None

    def fixture_id(self, operation: UUID) -> UUID:
        return UUID(self.prior["id"]) if self.prior is not None else operation


def _fixture_order(project_id: UUID, fixture_id: UUID) -> UUID:
    return uuid5(project_id, f"meal-fixture-order-v1:{fixture_id}")


@dataclass(frozen=True)
class MealDriver:
    collect_compiled: Any
    executable_path: str

    async def prepare(
        self,
        request: MealRequest,
        witness: behavior.CompiledAssetWitness,
        operation: UUID,
    ) -> dict[str, Any]:
        bound = request.binding
        fixture_id = request.fixture_id(operation)
        try:
            seed = fixture_signing_seed(
                bound.project_id,
                get_settings().jwt_secret.get_secret_value(),
            )
            session = await project_cell_create_meal_acceptance_session(
                bound.workspace_id,
                generation_run_id=bound.generation_run_id,
                fencing_epoch=bound.fencing_epoch,
                operation_id=operation,
                fixture_id=fixture_id,
            )
        except (OrchestratorUnavailable, OrchestratorBadRequest, ValueError):
            raise behavior.BehaviorProofError("MEAL_ACCEPTANCE_SESSION_UNAVAILABLE") from None
        return {
            "origin": session.preview_url.rstrip("/"),
            "mode": request.contract.mode,
            "buyer_id": session.buyer_id,
            "buyer_cookie": session.buyer_cookie,
            "other_id": session.other_id,
            "other_cookie": session.other_cookie,
            "project_id": str(bound.project_id),
            "fencing_epoch": bound.fencing_epoch,
            "order_id": str(_fixture_order(bound.project_id, fixture_id)),
            "fixture_seed": seed.hex(),
            "retain_build_fixture": request.contract.mode == "build" and request.prior is None,
            "prior_shopping": request.prior["shopping"] if request.prior else None,
            "prior_checkout": request.prior["checkout"] if request.prior else None,
            "check_checkout_wiring": True,
            "assets": [asdict(item) for item in witness.assets],
            "platform_assets": [asdict(item) for item in witness.platform_assets],
            "runner_sha256": runner_digest(),
            "executable_path": self.executable_path,
            "launch_args": [],
            "timeout_seconds": 85,
        }

    async def observe(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await run_owned_observation(
            payload,
            process_type=OwnedObservationProcess,
            result_seconds=91,
        )


def _envelope(run: GenerationRun, request: MealRequest, binding: RecoveryBinding) -> dict[str, Any]:
    return {
        "owner_id": str(run.user_id),
        "binding": request.binding.to_json(),
        "source_binding": binding.model_dump(mode="json"),
        "contract": request.contract.to_json(),
    }


def _validate_receipt(
    receipt: object,
    envelope: dict[str, Any],
    request: MealRequest,
    *,
    historical: bool = False,
) -> dict[str, Any]:
    _need(type(receipt) is dict, "meal receipt missing")
    assert isinstance(receipt, dict)
    _need(
        set(receipt)
        == {*envelope, "compiled", "checks", "runner_sha256", "receipt_digest", "fixture"}
        and all(receipt[key] == value for key, value in envelope.items())
        and isinstance(receipt["runner_sha256"], str)
        and re.fullmatch(r"[0-9a-f]{64}", receipt["runner_sha256"])
        and (historical or receipt["runner_sha256"] == runner_digest())
        and receipt["checks"] == list(request.contract.checks)
        and receipt["receipt_digest"]
        == behavior.digest(
            {key: value for key, value in receipt.items() if key != "receipt_digest"}
        ),
        "meal receipt binding changed",
    )
    _validate_fixture(receipt["fixture"], request.binding.project_id)
    compiled = receipt["compiled"]
    _need(
        type(compiled) is dict
        and set(compiled)
        == {
            "assets",
            "platform_assets",
            "compilation_receipt_sha256",
            "digest",
        },
        "meal compilation missing",
    )
    try:
        witness = behavior.CompiledAssetWitness(
            request.binding,
            tuple(behavior.ObservedAsset(**value) for value in compiled["assets"]),
            compiled["compilation_receipt_sha256"],
            "served_candidate_compilation_v1",
            tuple(behavior.ObservedAsset(**value) for value in compiled["platform_assets"]),
        )
        behavior._compiled(witness, request.binding)
        _need(witness.digest == compiled["digest"], "meal compilation changed")
    except (KeyError, TypeError, ValueError, behavior.BehaviorProofError) as error:
        raise RecoveryContractError("meal compilation invalid") from error
    return receipt


def _validate_fixture(raw: object, project_id: UUID) -> None:
    item_id = re.compile(r"[A-Za-z0-9_-]{1,96}")
    _need(
        type(raw) is dict and set(raw) == {"id", "shopping", "checkout"},
        "meal fixture proof missing",
    )
    assert isinstance(raw, dict)
    try:
        fixture_id = UUID(raw["id"])
    except (ValueError, TypeError, AttributeError) as error:
        raise RecoveryContractError("meal fixture identity invalid") from error
    _need(str(fixture_id) == raw["id"], "meal fixture identity invalid")
    expected_order = str(_fixture_order(project_id, fixture_id))
    rows, checkout = raw["shopping"], raw["checkout"]
    _need(
        type(rows) is list
        and len(rows) == 2
        and all(
            type(row) is dict
            and set(row) == {"id", "ingredient_id", "name", "bought"}
            and all(
                isinstance(row[key], str) and item_id.fullmatch(row[key])
                for key in ("id", "ingredient_id")
            )
            and isinstance(row["name"], str)
            and 1 <= len(row["name"]) <= 160
            and type(row["bought"]) is bool
            for row in rows
        ),
        "meal shopping fixture invalid",
    )
    _need(
        len({row["id"] for row in rows}) == 2
        and len({row["ingredient_id"] for row in rows}) == 2
        and sum(row["bought"] for row in rows) == 1,
        "meal shopping fixture is not distinct",
    )
    _need(
        type(checkout) is dict
        and set(checkout) == {"order_id", "recipe_ids"}
        and checkout["order_id"] == expected_order
        and type(checkout["recipe_ids"]) is list
        and len(checkout["recipe_ids"]) == 10
        and all(
            isinstance(value, str) and item_id.fullmatch(value) for value in checkout["recipe_ids"]
        )
        and checkout["recipe_ids"] == sorted(set(checkout["recipe_ids"])),
        "meal checkout fixture invalid",
    )


async def _prior_fixture(
    session: Any,
    run: GenerationRun,
    state: RecoveryStateV2,
) -> dict[str, Any]:
    from yleum_api.services.generation.agent_publication import (
        PublicationBindingV2,
        _publication_id,
        decode_publication_binding,
        publication_committed,
    )
    from yleum_api.services.generation.recovery_acceptance import MealAcceptanceSeal

    baseline = state.identity.accepted_snapshot_id
    _need(baseline is not None, "meal edit predecessor missing")
    project = await session.get(Project, run.project_id)
    _need(project is not None and project.current_snapshot_id == baseline, "meal baseline changed")
    previous_runs = (
        await session.scalars(
            select(GenerationRun)
            .where(
                GenerationRun.project_id == run.project_id,
                GenerationRun.user_id == run.user_id,
                GenerationRun.status == "completed",
                GenerationRun.agent_state["snapshot_id"].astext == str(baseline),
            )
            .limit(2)
        )
    ).all()
    _need(len(previous_runs) == 1, "meal baseline generation missing or ambiguous")
    previous = previous_runs[0]
    previous_state = recovery_state(previous)
    _need(isinstance(previous_state, RecoveryStateV2), "meal baseline continuation missing")
    assert isinstance(previous_state, RecoveryStateV2)
    _need(
        await publication_committed(session, run=previous, project=project, state=previous_state),
        "meal baseline publication invalid",
    )
    publication = await session.get(GenerationRecoveryEffect, _publication_id(previous.id))
    _need(publication is not None, "meal baseline publication missing")
    bound = decode_publication_binding(publication.pre_binding)
    _need(
        isinstance(bound, PublicationBindingV2)
        and isinstance(bound.acceptance, MealAcceptanceSeal),
        "meal baseline publication scope changed",
    )
    assert isinstance(bound, PublicationBindingV2) and isinstance(
        bound.acceptance, MealAcceptanceSeal
    )
    fixture = (
        await validate_owned_meal(session, run=previous, binding=bound.source, historical=True)
    )["fixture"]
    assert isinstance(fixture, dict)
    return fixture


def stored_meal_receipt(run: Any, contract: MealContract) -> dict[str, Any]:
    record = run.agent_state.get(STATE_KEY)
    _need(
        type(record) is dict
        and set(record) == {"state", "receipt"}
        and record["state"] == "confirmed"
        and type(record["receipt"]) is dict,
        "meal receipt unconfirmed",
    )
    receipt = record["receipt"]
    _need(receipt.get("contract") == contract.to_json(), "meal scope changed")
    assert isinstance(receipt, dict)
    return receipt


async def _confirmed_fixture_for_resume(
    session: Any,
    run: GenerationRun,
    request: MealRequest,
    source: RecoveryBinding,
    receipt: dict[str, Any],
) -> dict[str, Any]:
    try:
        old_source = RecoveryBinding.model_validate_json(json.dumps(receipt["source_binding"]))
    except (KeyError, ValueError) as error:
        raise RecoveryContractError("meal previous source invalid") from error
    _need(
        old_source.fencing_epoch <= source.fencing_epoch
        and old_source.workspace_id == source.workspace_id,
        "meal previous observation is outside this workspace",
    )
    # The old probe supplies the fixture IDs only. The current source, private
    # session, retained DB rows and compiled assets must all pass again.
    prior = (
        await _validate_owned_meal(
            session, run, old_source, request.contract, receipt, historical=True, resume=True
        )
    )["fixture"]
    _need(request.prior is None or request.prior == prior, "meal predecessor fixture changed")
    assert isinstance(prior, dict)
    return prior


def _red_key(source: RecoveryBinding, contract: MealContract, runner: str) -> str:
    logical = behavior.digest(
        {
            "source": source.model_dump(mode="json", exclude={"fencing_epoch"}),
            "contract": contract.contract_digest,
            "runner": runner,
        }
    )
    return f"meal_observation_red:{logical}:"


async def require_meal_observation(
    coordinator: Any,
    candidate: Any,
    identity: Any,
    build_ref: str,
) -> None:
    from yleum_api.services.generation.recovery_acceptance import acceptance_required
    from yleum_api.services.generation.recovery_readers import require_recovery_readers

    driver = coordinator.behavior_driver
    if type(driver) is not MealDriver:
        return
    async with coordinator.session_factory() as session:
        run = await session.get(GenerationRun, coordinator.generation_run_id)
        project = await session.get(Project, coordinator.project_id)
        if run is None or project is None or not acceptance_required(run):
            return
        if reviewed_meal_contract(run, project.template) is None:
            return
        run, project = await _locked_observation_run(session, coordinator, identity)
        contract = reviewed_meal_contract(run, project.template)
        _need(contract is not None, "meal request changed")
        assert contract is not None
        basis = _continuation_basis(run)
        source = recovery_binding(identity)
        state = recovery_state(run)
        assert isinstance(state, RecoveryStateV2)
        prior_fixture = (
            await _prior_fixture(session, run, state) if contract.mode == "edit" else None
        )
        request = MealRequest(
            behavior.candidate_binding(
                coordinator,
                candidate,
                identity,
                build_ref,
                contract,
            ),
            contract,
            prior_fixture,
        )
        envelope = _envelope(run, request, source)
        runner = runner_digest()
        operation = uuid5(
            run.id, "meal-acceptance:" + behavior.digest({"envelope": envelope, "runner": runner})
        )
        prior = run.agent_state.get(STATE_KEY)
        previous = None
        if prior is not None:
            _need(type(prior) is dict, "meal observation state corrupt")
            if prior.get("state") == "confirmed":
                previous = stored_meal_receipt(run, contract)
            elif prior.get("state") == "red" and set(prior) == {
                "state",
                "envelope",
                "check",
                "previous",
            }:
                previous = prior["previous"]
                _need(
                    previous is None or isinstance(previous, dict), "meal previous receipt invalid"
                )
            else:
                raise behavior.BehaviorProofError("MEAL_ACCEPTANCE_OUTCOME_UNKNOWN")
        if previous is not None:
            if all(previous.get(key) == value for key, value in envelope.items()) and (
                previous.get("runner_sha256") == runner
            ):
                _validate_receipt(previous, envelope, request)
                if prior["state"] != "confirmed":
                    run.agent_state = {
                        **run.agent_state,
                        STATE_KEY: {"state": "confirmed", "receipt": previous},
                    }
                    await session.commit()
                return
            fixture = await _confirmed_fixture_for_resume(session, run, request, source, previous)
            request = replace(request, prior=fixture)
        red_key = _red_key(source, contract, runner)
        diagnostics = {red_key + check: check for check in contract.checks}
        previous_red = await session.scalar(
            select(ProjectCellActivityLease.redacted_diagnostic)
            .where(
                ProjectCellActivityLease.workspace_id == identity.workspace_id,
                ProjectCellActivityLease.generation_run_id == run.id,
                ProjectCellActivityLease.kind == ActivityKind.FINALIZATION.value,
                ProjectCellActivityLease.phase == "runtime_probe",
                ProjectCellActivityLease.state == ActivityState.FAILED.value,
                ProjectCellActivityLease.redacted_diagnostic.in_(diagnostics),
            )
            .limit(1)
        )
        if previous_red is not None:
            raise MealVerificationFailed(diagnostics[previous_red])
        pending = {"state": "started", "envelope": envelope, "operation": str(operation)}
        activity = await session.get(ProjectCellActivityLease, operation, populate_existing=True)
        if activity is not None:
            raise behavior.BehaviorProofError("MEAL_ACCEPTANCE_OUTCOME_UNKNOWN")
        await session.rollback()
    before = behavior._compiled(await driver.collect_compiled(request), request.binding)
    payload = await driver.prepare(request, before, operation)
    _need(await coordinator._identity() == identity, "meal source changed before verification")
    async with coordinator.session_factory() as session:
        current, _ = await _locked_observation_run(session, coordinator, identity)
        _need(
            _continuation_basis(current) == basis and current.agent_state.get(STATE_KEY) == prior,
            "meal continuation or reservation changed",
        )
        await require_recovery_readers()
        current.agent_state = {**current.agent_state, STATE_KEY: pending}
        started = datetime.now(UTC)
        await start_activity(
            session,
            workspace_id=identity.workspace_id,
            generation_run_id=current.id,
            kind=ActivityKind.FINALIZATION,
            fencing_epoch=identity.fencing_epoch,
            operation_id=operation,
            proof_key=identity.proof_key,
            phase="runtime_probe",
            now=started,
            deadline_at=started + timedelta(seconds=125),
        )
        await session.commit()
    observed = await driver.observe(payload)
    red = observed.get("check")
    is_red = set(observed) == {"status", "check", "cleanup_closed"} and (
        observed["status"] == "RED" and red in contract.checks
    )
    passes = observed.get("outcomes")
    is_pass = (
        set(observed) == {"status", "outcomes", "fixture", "cleanup_closed"}
        and observed["status"] == "PASS"
        and type(passes) is dict
        and set(passes) == set(contract.checks)
        and all(v is True for v in passes.values())
    )
    if observed.get("cleanup_closed") is not True:
        raise behavior.BehaviorProofError("MEAL_ACCEPTANCE_OUTCOME_UNKNOWN")
    # Once reaping is confirmed, close physical activity even if subsequent
    # source/receipt reads fail. The started tombstone still prevents replay.
    async with coordinator.session_factory() as session:
        run, _ = await _locked_observation_run(session, coordinator, identity)
        _need(run.agent_state.get(STATE_KEY) == pending, "meal reservation changed")
        await finish_activity(
            session,
            operation_id=operation,
            state=ActivityState.COMPLETED if is_pass else ActivityState.FAILED,
            finished_at=datetime.now(UTC),
            diagnostic="meal_process_closed",
        )
        await session.commit()
    if not (is_pass or is_red):
        raise behavior.BehaviorProofError("MEAL_ACCEPTANCE_OUTCOME_UNKNOWN")
    observed_fixture = None
    if is_pass:
        fixture = observed["fixture"]
        _need(type(fixture) is dict and set(fixture) == {"shopping", "checkout"})
        observed_fixture = {**fixture, "id": str(request.fixture_id(operation))}
        _validate_fixture(observed_fixture, request.binding.project_id)
        _need(
            request.prior is None or observed_fixture == request.prior,
            "meal predecessor fixture changed",
        )
    after = behavior._compiled(await driver.collect_compiled(request), request.binding)
    _need(
        after == before and await coordinator._identity() == identity,
        "meal candidate changed during verification",
    )
    async with coordinator.session_factory() as session:
        run, project = await _locked_observation_run(session, coordinator, identity)
        _need(
            _continuation_basis(run) == basis
            and reviewed_meal_contract(run, project.template) == contract
            and run.agent_state.get(STATE_KEY) == pending,
            "meal observation owner changed",
        )
        if is_red:
            record = {"state": "red", "envelope": envelope, "check": red, "previous": previous}
        else:
            receipt: dict[str, Any] = {
                **envelope,
                "checks": list(contract.checks),
                "runner_sha256": runner,
                "fixture": observed_fixture,
                "compiled": {
                    "assets": [asdict(value) for value in before.assets],
                    "platform_assets": [asdict(value) for value in before.platform_assets],
                    "compilation_receipt_sha256": before.compilation_receipt_sha256,
                    "digest": before.digest,
                },
            }
            receipt["receipt_digest"] = behavior.digest(receipt)
            _validate_receipt(receipt, envelope, request)
            record = {"state": "confirmed", "receipt": receipt}
        run.agent_state = {**run.agent_state, STATE_KEY: record}
        if is_red:
            assert isinstance(red, str)
            activity = await session.get(ProjectCellActivityLease, operation, with_for_update=True)
            _need(
                activity is not None and activity.state == ActivityState.FAILED.value,
                "meal failed activity changed",
            )
            activity.redacted_diagnostic = red_key + red
        await session.commit()
    if is_red:
        assert isinstance(red, str)
        raise MealVerificationFailed(red)


async def validate_owned_meal(
    session: Any,
    *,
    run: GenerationRun,
    binding: RecoveryBinding,
    historical: bool = False,
) -> dict[str, Any]:
    project = await session.get(
        Project, run.project_id, populate_existing=True, with_for_update=True
    )
    _need(project is not None and project.owner_id == run.user_id, "meal owner changed")
    contract = reviewed_meal_contract(run, project.template)
    _need(contract is not None, "meal scope changed")
    assert contract is not None
    receipt = stored_meal_receipt(run, contract)
    return await _validate_owned_meal(
        session, run, binding, contract, receipt, historical=historical
    )


async def _validate_owned_meal(
    session: Any,
    run: GenerationRun,
    binding: RecoveryBinding,
    contract: MealContract,
    receipt: dict[str, Any],
    *,
    historical: bool,
    resume: bool = False,
) -> dict[str, Any]:
    try:
        workspace_id = UUID(binding.workspace_id)
        allowed = (
            ("prepared", "accepted", "rejected")
            if resume
            else (("accepted", "rejected") if historical else ("prepared", "accepted"))
        )
        candidates = (
            await session.scalars(
                select(ProjectCellCandidate)
                .where(
                    ProjectCellCandidate.workspace_id == workspace_id,
                    ProjectCellCandidate.generation_run_id == run.id,
                    ProjectCellCandidate.fencing_epoch == binding.fencing_epoch,
                    ProjectCellCandidate.source_revision == binding.source_sha256,
                    ProjectCellCandidate.cancelled.is_(False),
                    ProjectCellCandidate.status.in_(allowed),
                )
                .execution_options(populate_existing=True)
                .with_for_update()
            )
        ).all()
        _need(
            len(candidates) == 1
            and str(candidates[0].id) == receipt["binding"]["candidate_id"]
            and (not historical or resume or candidates[0].promoted_at is not None),
            "meal current candidate missing or ambiguous",
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
        _need(proof is not None, "meal proof unavailable")
        identity = proof_identity_from_model(proof)
        _need(
            identity.proof_key == proof.proof_key and recovery_binding(identity) == binding,
            "meal proof changed",
        )
        if not historical:
            workspace = await session.get(
                ProjectCellWorkspace, workspace_id, populate_existing=True, with_for_update=True
            )
            _need(
                run.status == "running"
                and workspace is not None
                and workspace.project_id == run.project_id
                and workspace.owner_id == run.user_id
                and workspace.generation_run_id == run.id
                and workspace.fencing_epoch == binding.fencing_epoch
                and workspace.state == "ready"
                and workspace.deleted_at is None,
                "meal workspace changed",
            )
            current = await project_cell_agent_identity(
                workspace_id,
                generation_run_id=run.id,
                fencing_epoch=binding.fencing_epoch,
            )
            _need(
                current.workspace_revision == identity.workspace_revision
                and current.schema_data_digest == identity.schema_data_digest
                and current.environment_digest
                == identity.base_image_digest
                == identity.toolchain_digest
                and current.dependency_digest == identity.dependency_digest
                and current.cell_manifest_digest == identity.cell_manifest_digest
                and current.build_config_digest == identity.build_config_digest,
                "meal physical candidate changed",
            )
        request = MealRequest(
            behavior.candidate_binding(
                SimpleNamespace(project_id=run.project_id),
                candidate,
                identity,
                candidate.build_ref,
                contract,
            ),
            contract,
        )
        return _validate_receipt(
            receipt, _envelope(run, request, binding), request, historical=historical
        )
    except (KeyError, TypeError, ValueError, AttributeError, behavior.BehaviorProofError) as error:
        raise RecoveryContractError("meal owning receipt invalid") from error
