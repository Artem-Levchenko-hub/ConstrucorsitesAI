"""Durable owner-only code restoration, with SQL admission and observed activation."""

import asyncio
import hashlib
import json
import re
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import UUID, uuid4, uuid5

from sqlalchemy import or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from omnia_api.core.errors import ApiError
from omnia_api.models.generation_run import GenerationRun
from omnia_api.models.message import Message
from omnia_api.models.project import Project
from omnia_api.models.project_cell import ProjectCellOperation, ProjectCellWorkspace
from omnia_api.models.project_version import ProjectVersion
from omnia_api.models.restoration import ACTIVE_RESTORATION_STATES, Restoration
from omnia_api.models.snapshot import Snapshot
from omnia_api.schemas.restoration import (
    RestorationAdaptationActivationCommand,
    RestorationAdaptationActivationOffer,
    RestorationAdaptationActivationOfferRequest,
    RestorationAdaptationActivationStatus,
    RestoreApplyRequest,
    RestoreOperation,
    RestoreReport,
    RestoreRequest,
    RestoreState,
    RuntimeRecoveryObserved,
    RuntimeRestoration,
    canonical_activation_digest,
)
from omnia_api.services import repo
from omnia_api.services.generation.publication import consume_free_generation
from omnia_api.services.generation_runs import (
    ACTIVE_GENERATION_STATUSES,
    load_generation_dispatch,
)
from omnia_api.services.orchestrator_client import (
    RestorationAdaptationWorkspace,
    project_cell_apply_restoration_adaptation_activation,
    project_cell_cancel_restoration_adaptation_activation,
    project_cell_offer_restoration_adaptation_activation,
    project_cell_prove_restoration_adaptation,
    project_cell_status_restoration_adaptation_activation,
)
from omnia_api.services.project_versions import record_restored_version, resolve_version
from omnia_api.services.promotion_permit import canonical_files_digest
from omnia_api.services.restoration_runtime import RestorationRuntime


def _digest(payload: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def restoration_request_digest(
    project_id: UUID, owner_id: UUID, request: RestoreRequest
) -> str:
    wire = request.model_dump(mode="json")
    if request.execution_policy == "manual":
        # Pre-policy clients hashed this exact payload. Keep their durable
        # idempotency identity byte-compatible across a rolling deployment.
        wire.pop("execution_policy")
    return _digest(
        {"project_id": str(project_id), "owner_id": str(owner_id), **wire}
    )


# States in which the next transition depends on the controller, not on the owner.
# A verified automatic ``ready`` is handled separately: it waits on the API
# worker to persist the existing apply claim, not on another owner action.
CONTROLLER_WAIT_STATES = frozenset({"preparing", "checking", "applying", "reconciling"})
_RECONCILE_DELAYS_SECONDS = (3, 5, 10, 20, 30)
AUTOMATIC_EXECUTION_POLICY = "automatic_when_safe"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def reconcile_delay_seconds(attempts: int) -> int:
    index = min(max(attempts, 0), len(_RECONCILE_DELAYS_SECONDS) - 1)
    return _RECONCILE_DELAYS_SECONDS[index]


def automatic_apply_ready(operation: Restoration) -> bool:
    """True only for the exact immutable receipt selected by the owner policy."""
    report = operation.report
    runtime = operation.runtime_result or {}
    return bool(
        operation.execution_policy == AUTOMATIC_EXECUTION_POLICY
        and operation.selected_branch == "exact"
        and operation.state == "ready"
        and operation.phase != "cancel"
        and operation.apply_digest is None
        and operation.candidate_id is not None
        and operation.source_binding is not None
        and operation.source_binding_digest is not None
        and isinstance(report, dict)
        and report.get("mode") == "exact"
        and report.get("blockers") == []
        and type(report.get("revision")) is int
        and runtime.get("can_apply") is True
    )


def waits_for_reconciliation(operation: Restoration) -> bool:
    return operation.state in CONTROLLER_WAIT_STATES or automatic_apply_ready(operation)


def _adaptive_generation_owns(operation: Restoration) -> bool:
    return operation.selected_branch == "adaptive" and operation.adaptation_run_id is not None


def schedule_reconcile(operation: Restoration, *, now: datetime | None = None) -> None:
    """Keep the durable projection moving without a client GET.

    Every persisted transition sets when the worker should look at the
    controller again. Owner-facing and terminal states clear the schedule so a
    ready candidate is never polled forever."""
    moment = now or datetime.now(UTC)
    if waits_for_reconciliation(operation):
        # A row built in memory has no server default yet.
        delay = reconcile_delay_seconds(operation.reconcile_attempts or 0)
        operation.next_reconcile_at = moment + timedelta(seconds=delay)
    else:
        operation.next_reconcile_at = None
        operation.reconcile_attempts = 0


async def assert_no_active_restoration(
    session: AsyncSession,
    project_id: UUID,
    *,
    operation_id: UUID | None = None,
) -> None:
    """Caller MUST hold Project FOR UPDATE until its competing claim is durable."""
    query = select(Restoration.id).where(
        Restoration.project_id == project_id,
        Restoration.state.in_(ACTIVE_RESTORATION_STATES),
    )
    if operation_id is not None:
        query = query.where(Restoration.id != operation_id)
    active_id = await session.scalar(query.limit(1))
    if active_id:
        raise ApiError(
            "restoration_active",
            "Идёт восстановление версии: примените или отмените его, затем повторите действие.",
            409,
            details={"restoration_id": str(active_id)},
        )


async def _owned_project(
    session: AsyncSession,
    project_id: UUID,
    owner_id: UUID,
) -> Project:
    # Match existing generation/preview/deletion lock order: advisory, project, workspace.
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:project_id))"),
        {"project_id": str(project_id)},
    )
    project = await session.scalar(
        select(Project)
        .where(Project.id == project_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if project is None or project.owner_id != owner_id:
        raise ApiError("not_found", "project not found", 404)
    return project


async def _read_owned_project(
    session: AsyncSession,
    project_id: UUID,
    owner_id: UUID,
) -> Project:
    """Authorize a read without joining the mutation/admission lock domain."""
    project = await session.get(Project, project_id)
    if project is None or project.owner_id != owner_id:
        raise ApiError("not_found", "project not found", 404)
    return project


async def _owned_project_for_admission(
    session: AsyncSession,
    project_id: UUID,
    owner_id: UUID,
) -> Project:
    """Fail fast when another project mutation owns the admission lock."""
    await _read_owned_project(session, project_id, owner_id)
    acquired = await session.scalar(
        text("SELECT pg_try_advisory_xact_lock(hashtext(:project_id))"),
        {"project_id": str(project_id)},
    )
    if acquired is not True:
        raise ApiError(
            "conflict",
            "Проект сейчас занят. Повторите восстановление через несколько секунд.",
            503,
            details={"retryable": True},
        )
    project = await session.scalar(
        select(Project)
        .where(Project.id == project_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if project is None or project.owner_id != owner_id:
        raise ApiError("not_found", "project not found", 404)
    return project


async def lock_restoration_admission(
    session: AsyncSession,
    project_id: UUID,
    owner_id: UUID,
    *,
    operation_id: UUID | None = None,
) -> Project:
    project = await _owned_project_for_admission(session, project_id, owner_id)
    await assert_no_active_restoration(session, project_id, operation_id=operation_id)
    active_run_id = await session.scalar(
        select(GenerationRun.id)
        .where(
            GenerationRun.project_id == project_id,
            GenerationRun.status.in_(ACTIVE_GENERATION_STATUSES),
        )
        .limit(1)
    )
    if active_run_id:
        raise ApiError(
            "generation_active",
            "Сейчас идёт сборка. Дождитесь её завершения и повторите восстановление версии.",
            409,
            details={"active_run_id": str(active_run_id)},
        )
    return project


async def _owned_operation(
    session: AsyncSession,
    project_id: UUID,
    owner_id: UUID,
    operation_id: UUID,
) -> tuple[Project, Restoration]:
    project = await _owned_project(session, project_id, owner_id)
    operation = await session.scalar(
        select(Restoration)
        .where(Restoration.id == operation_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if operation is None or operation.project_id != project_id or operation.owner_id != owner_id:
        raise ApiError("not_found", "restoration not found", 404)
    return project, operation


async def _read_owned_operation(
    session: AsyncSession,
    project_id: UUID,
    owner_id: UUID,
    operation_id: UUID,
) -> tuple[Project, Restoration]:
    project = await _read_owned_project(session, project_id, owner_id)
    operation = await session.get(Restoration, operation_id)
    if operation is None or operation.project_id != project_id or operation.owner_id != owner_id:
        raise ApiError("not_found", "restoration not found", 404)
    return project, operation


def _touch(operation: Restoration) -> None:
    operation.revision += 1
    operation.updated_at = datetime.now(UTC)
    schedule_reconcile(operation, now=operation.updated_at)


def public_operation(operation: Restoration) -> RestoreOperation:
    report = RestoreReport.model_validate(operation.report) if operation.report else None
    runtime = operation.runtime_result or {}
    adaptive_can_cancel = bool(
        operation.selected_branch == "adaptive"
        and operation.state in {"adapting", "applying", "reconciling"}
        and operation.phase not in {"activation_cancel", "activation_cancelled"}
        and getattr(operation, "activation_cancel_requested_at", None) is None
        and operation.activation_effects_admitted is not True
    )
    return RestoreOperation(
        id=operation.id,
        project_id=operation.project_id,
        source_version_id=operation.source_version_id,
        source_snapshot_id=operation.source_snapshot_id,
        base_draft_snapshot_id=operation.base_draft_snapshot_id,
        state=cast(RestoreState, operation.state),
        phase=operation.phase,
        updated_at=operation.updated_at,
        revision=operation.revision,
        candidate_id=operation.candidate_id,
        execution_policy=cast(Any, operation.execution_policy),
        selected_branch=cast(Any, operation.selected_branch),
        adaptation_run_id=operation.adaptation_run_id,
        report=report,
        can_apply=(
            operation.state == "ready"
            and report is not None
            and not report.blockers
            and operation.source_binding is not None
            and operation.source_binding_digest is not None
            and runtime.get("can_apply") is True
        ),
        can_cancel=(
            adaptive_can_cancel
            or (
                operation.state
                in {"preparing", "checking", "ready", "needs_changes", "applying", "reconciling"}
                and operation.apply_digest is None
                and operation.phase != "cancel"
                and operation.activation_effects_admitted is not True
                and (not runtime or runtime.get("can_cancel") is True)
            )
        ),
        applied_version=operation.applied_version_id,
        applied_snapshot_id=operation.applied_snapshot_id,
        error=operation.error,
    )


def _activation_candidate_files_digest(files: Mapping[str, str]) -> str:
    return canonical_activation_digest(
        [
            {"path": path, "sha256": hashlib.sha256(content.encode()).hexdigest()}
            for path, content in sorted(files.items())
        ]
    )


def _adaptation_state(run: GenerationRun) -> tuple[dict[str, Any], dict[str, Any]]:
    root = run.agent_state if isinstance(run.agent_state, dict) else {}
    bundle = root.get("restoration_adaptation")
    finalization = root.get("max_finalization")
    proof = (
        finalization.get("restoration_adaptation_proof")
        if isinstance(finalization, dict)
        else None
    )
    if not isinstance(bundle, dict) or not isinstance(proof, dict):
        raise ApiError("conflict", "Restoration adaptation proof is unavailable", 409)
    if proof.get("state") != "proof_ready":
        raise ApiError("conflict", "Restoration adaptation proof is not ready", 409)
    return bundle, proof


def _adaptation_proof_intent(
    raw_command: object,
) -> tuple[RestorationAdaptationWorkspace, dict[str, str], str, str, str, int]:
    if not isinstance(raw_command, dict) or set(raw_command) != {
        "proof_request",
        "candidate_files",
        "candidate_artifact_digest",
    }:
        raise ApiError("conflict", "Restoration adaptation proof outbox is invalid", 409)
    raw_request = raw_command.get("proof_request")
    candidate_files = raw_command.get("candidate_files")
    artifact_digest = raw_command.get("candidate_artifact_digest")
    if not isinstance(raw_request, dict) or set(raw_request) != {
        "workspace",
        "candidate_workspace_id",
        "candidate_fencing_epoch",
        "candidate_workspace_revision",
        "candidate_proof_key",
        "candidate_artifact_digest",
        "proof_attempt",
    }:
        raise ApiError("conflict", "Restoration adaptation proof request is invalid", 409)
    raw_workspace = raw_request.get("workspace")
    candidate_fencing_epoch = raw_request.get("candidate_fencing_epoch")
    candidate_revision = raw_request.get("candidate_workspace_revision")
    candidate_proof_key = raw_request.get("candidate_proof_key")
    proof_attempt = raw_request.get("proof_attempt")
    if not isinstance(raw_workspace, dict):
        raise ApiError("conflict", "Restoration adaptation proof workspace is invalid", 409)
    try:
        workspace = RestorationAdaptationWorkspace.from_json(
            {"state": "ready", **raw_workspace}
        )
    except Exception as exc:
        raise ApiError(
            "conflict", "Restoration adaptation proof workspace is invalid", 409
        ) from exc
    if (
        not isinstance(candidate_files, dict)
        or any(
            not isinstance(path, str) or not isinstance(content, str)
            for path, content in candidate_files.items()
        )
        or not isinstance(artifact_digest, str)
        or _SHA256_RE.fullmatch(artifact_digest) is None
        or artifact_digest != canonical_files_digest(candidate_files)
        or raw_request.get("candidate_artifact_digest") != artifact_digest
        or raw_request.get("candidate_workspace_id") != str(workspace.candidate_workspace_id)
        or type(candidate_fencing_epoch) is not int
        or candidate_fencing_epoch < 1
        or candidate_fencing_epoch != workspace.candidate_fencing_epoch
        or type(candidate_revision) is not str
        or _SHA256_RE.fullmatch(candidate_revision) is None
        or type(candidate_proof_key) is not str
        or _SHA256_RE.fullmatch(candidate_proof_key) is None
        or type(proof_attempt) is not int
        or proof_attempt < 1
    ):
        raise ApiError("conflict", "Restoration adaptation proof binding changed", 409)
    return (
        workspace,
        cast(dict[str, str], candidate_files),
        artifact_digest,
        candidate_revision,
        candidate_proof_key,
        proof_attempt,
    )


def _adaptation_cancel_requested(run: GenerationRun) -> bool:
    state = run.agent_state if isinstance(run.agent_state, dict) else {}
    return run.status in {"cancel_requested", "cancelled"} or bool(
        state.get("restoration_adaptation_cancel_requested")
    )


def _mark_activation_cancel_requested(operation: Restoration) -> None:
    if operation.activation_cancel_requested_at is None:
        operation.activation_cancel_requested_at = datetime.now(UTC)


def _activation_cancel_requested(
    operation: Restoration,
    run: GenerationRun | None = None,
) -> bool:
    if operation.activation_effects_admitted:
        return False
    return bool(
        operation.activation_cancel_requested_at is not None
        or operation.phase in {"activation_cancel", "activation_cancelled"}
        or (
            run is not None
            and _adaptation_cancel_requested(run)
        )
    )


async def _settle_cancelled_adaptation_locked(
    session: AsyncSession,
    operation: Restoration,
    *,
    receipt: RestorationAdaptationActivationStatus | None = None,
) -> GenerationRun:
    """Atomically terminalize the operation and its generation without quota."""
    if operation.activation_effects_admitted:
        raise ApiError(
            "conflict",
            "Restoration activation passed the controller point of no return",
            409,
        )
    if operation.adaptation_run_id is None:
        raise ApiError("conflict", "Restoration adaptation generation is missing", 409)
    run = await session.get(
        GenerationRun, operation.adaptation_run_id, with_for_update=True
    )
    if (
        run is None
        or run.project_id != operation.project_id
        or run.user_id != operation.owner_id
    ):
        raise ApiError("conflict", "Restoration adaptation owner disappeared", 409)
    if receipt is not None:
        if receipt.effects_admitted or receipt.state != "cancelled":
            raise ApiError("conflict", "Restoration cancellation receipt is not terminal", 409)
        _validate_activation_receipt_progression(operation, receipt)
        operation.activation_receipt = receipt.model_dump(mode="json")
        operation.activation_receipt_digest = receipt.receipt_digest
        operation.activation_effects_admitted = False
    _mark_activation_cancel_requested(operation)
    operation.state = "cancelled"
    operation.phase = "activation_cancelled"
    operation.error = None
    operation.activation_request = None
    operation.activation_request_digest = None
    if operation.activation_notification_state != "delivered":
        operation.activation_notification_state = "pending"
    state = dict(run.agent_state or {})
    state["restoration_adaptation_cancel_requested"] = True
    state["restoration_adaptation_activation"] = {
        "state": "cancelled",
        "operation_id": str(operation.id),
        "activation_id": (
            str(operation.activation_id) if operation.activation_id is not None else None
        ),
        "receipt_digest": (
            receipt.receipt_digest
            if receipt is not None
            else operation.activation_receipt_digest
        ),
        "publication_consumed": False,
    }
    if state.get("restoration_adaptation_owner_status") != "terminal_notified":
        state["restoration_adaptation_owner_status"] = "terminal_pending"
    run.agent_state = state
    run.status = "cancelled"
    run.error = None
    run.finished_at = run.finished_at or datetime.now(UTC)
    _touch(operation)
    return run


def _activation_offer_request(
    *,
    operation: Restoration,
    run: GenerationRun,
    proof: Mapping[str, Any],
) -> RestorationAdaptationActivationOfferRequest:
    try:
        return RestorationAdaptationActivationOfferRequest(
            workspace_id=operation.workspace_id,
            operation_id=operation.id,
            project_id=operation.project_id,
            owner_id=operation.owner_id,
            generation_run_id=run.id,
            candidate_workspace_id=UUID(str(proof["candidate_workspace_id"])),
            candidate_fencing_epoch=proof["candidate_fencing_epoch"],
            candidate_workspace_revision=proof["candidate_workspace_revision"],
            proof_attempt=proof["proof_attempt"],
            proof_digest=proof["proof_digest"],
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ApiError("conflict", "Restoration adaptation proof is invalid", 409) from exc


def _validate_activation_offer(
    *,
    request: RestorationAdaptationActivationOfferRequest,
    offer: RestorationAdaptationActivationOffer,
    operation: Restoration,
    proof: Mapping[str, Any],
    files: Mapping[str, str],
    workspace: ProjectCellWorkspace,
) -> None:
    expected = request.model_dump(mode="json")
    for key in expected:
        if str(getattr(offer, key)) != str(expected[key]):
            raise ApiError("conflict", "Restoration activation offer changed", 409)
    if (
        offer.expected_source_fencing_epoch != workspace.fencing_epoch
        or offer.source_workspace_revision != proof.get("source_workspace_revision")
        or offer.candidate_artifact_digest != proof.get("candidate_artifact_digest")
        or offer.candidate_source_manifest_digest
        != proof.get("candidate_source_manifest_digest")
        or offer.probe_contract_digest != proof.get("probe_contract_digest")
        or offer.probe_rehearsal_digest != proof.get("probe_rehearsal_digest")
        or offer.probe_rehearsal_database_digest
        != proof.get("probe_rehearsal_database_digest")
        or offer.candidate_artifact_digest != canonical_files_digest(files)
        or offer.candidate_files_digest != _activation_candidate_files_digest(files)
    ):
        raise ApiError("conflict", "Restoration activation source or candidate changed", 409)


_ACTIVATION_PHASE_ORDER = {
    "intent": 0,
    "target_prepared": 1,
    "writers_stopping": 2,
    "target_writers_admitted": 3,
    "activated": 4,
}


def _validate_activation_receipt_progression(
    operation: Restoration,
    receipt: RestorationAdaptationActivationStatus,
) -> None:
    raw_prior = operation.activation_receipt
    if raw_prior is None:
        return
    try:
        prior = RestorationAdaptationActivationStatus.model_validate_json(
            json.dumps(raw_prior), strict=True
        )
    except ValueError as exc:
        raise ApiError(
            "conflict", "Restoration activation receipt journal is invalid", 409
        ) from exc
    if (
        prior.offer != receipt.offer
        or prior.planned_commit_sha != receipt.planned_commit_sha
        or prior.activation_digest != receipt.activation_digest
    ):
        raise ApiError("conflict", "Restoration activation receipt binding changed", 409)
    if prior.state == receipt.state:
        if prior.receipt_digest != receipt.receipt_digest:
            raise ApiError("conflict", "Restoration activation receipt changed", 409)
        return
    if prior.state in {"activated", "cancelled"}:
        raise ApiError("conflict", "Restoration activation receipt regressed", 409)
    if prior.effects_admitted and not receipt.effects_admitted:
        raise ApiError("conflict", "Restoration activation effects regressed", 409)
    if receipt.state == "cancelled":
        if prior.effects_admitted:
            raise ApiError("conflict", "Restoration activation cancelled after admission", 409)
        return
    if receipt.state == "cancelling":
        if prior.effects_admitted:
            raise ApiError("conflict", "Restoration activation cancel regressed", 409)
        return
    prior_order = _ACTIVATION_PHASE_ORDER.get(prior.state)
    next_order = _ACTIVATION_PHASE_ORDER.get(receipt.state)
    if prior.state == "cancelling":
        if receipt.effects_admitted and receipt.state in {
            "target_writers_admitted",
            "activated",
        }:
            return
        raise ApiError("conflict", "Restoration activation receipt transition is invalid", 409)
    if prior_order is None or next_order is None:
        raise ApiError("conflict", "Restoration activation receipt transition is invalid", 409)
    if next_order <= prior_order:
        raise ApiError("conflict", "Restoration activation receipt regressed", 409)


async def _complete_restoration_adaptation_activation(
    session: AsyncSession,
    *,
    run_id: UUID,
    command: RestorationAdaptationActivationCommand,
    receipt: RestorationAdaptationActivationStatus,
    files: Mapping[str, str],
) -> bool:
    """Commit the user-visible result and quota exactly once after a terminal receipt."""
    offer = command.offer
    project = await _owned_project(session, offer.project_id, offer.owner_id)
    operation = await session.scalar(
        select(Restoration)
        .where(Restoration.id == offer.operation_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    run = await session.get(GenerationRun, run_id, with_for_update=True)
    workspace = await session.get(ProjectCellWorkspace, offer.workspace_id, with_for_update=True)
    if (
        operation is None
        or run is None
        or workspace is None
        or operation.project_id != project.id
        or operation.owner_id != offer.owner_id
        or operation.adaptation_run_id != run.id
        or run.project_id != project.id
        or run.user_id != offer.owner_id
        or operation.activation_id != offer.activation_id
        or operation.activation_offer != offer.model_dump(mode="json")
        or operation.activation_request != command.model_dump(mode="json")
        or operation.activation_request_digest != command.digest()
        or workspace.project_id != project.id
        or workspace.owner_id != offer.owner_id
    ):
        raise ApiError("conflict", "Restoration activation ownership changed", 409)
    if operation.state == "completed":
        if (
            operation.adaptation_planned_commit_sha != command.planned_commit_sha
            or operation.activation_receipt_digest != receipt.receipt_digest
            or operation.applied_snapshot_id is None
        ):
            raise ApiError("conflict", "Completed restoration activation changed", 409)
        return True
    if receipt.state != "activated" or not receipt.effects_admitted:
        return False
    if workspace.fencing_epoch not in {
        offer.expected_source_fencing_epoch,
        offer.target_fencing_epoch,
    }:
        raise ApiError("conflict", "Project workspace fence changed", 409)
    if project.current_snapshot_id != operation.base_draft_snapshot_id:
        raise ApiError("conflict", "Project draft changed before activation completion", 409)
    base = await session.get(Snapshot, operation.base_draft_snapshot_id)
    if (
        base is None
        or base.project_id != project.id
        or base.commit_sha != operation.base_commit_sha
    ):
        raise ApiError("conflict", "Restoration activation base snapshot changed", 409)

    dispatch = load_generation_dispatch(run)
    snapshot_id = uuid5(operation.id, "restoration-adaptation-snapshot")
    snapshot = await session.get(Snapshot, snapshot_id)
    if snapshot is None:
        snapshot = Snapshot(
            id=snapshot_id,
            project_id=project.id,
            commit_sha=command.planned_commit_sha,
            prompt_text=dispatch.prompt_text,
            model_id=dispatch.model_id,
            parent_id=base.id,
        )
        session.add(snapshot)
        await session.flush()
    elif (
        snapshot.project_id != project.id
        or snapshot.commit_sha != command.planned_commit_sha
        or snapshot.parent_id != base.id
    ):
        raise ApiError("conflict", "Restoration activation snapshot identity changed", 409)

    version = await session.scalar(
        select(ProjectVersion)
        .where(ProjectVersion.generation_run_id == run.id)
        .with_for_update()
    )
    if version is None or version.project_id != project.id:
        raise ApiError("conflict", "Restoration adaptation version is unavailable", 409)
    message = (
        await session.get(Message, run.assistant_message_id, with_for_update=True)
        if run.assistant_message_id is not None
        else None
    )
    if message is None or message.project_id != project.id or message.role != "assistant":
        raise ApiError("conflict", "Restoration adaptation message is unavailable", 409)

    project.current_snapshot_id = snapshot.id
    if workspace.fencing_epoch == offer.expected_source_fencing_epoch:
        workspace.version += 1
    workspace.fencing_epoch = offer.target_fencing_epoch
    version.snapshot_id = snapshot.id
    version.restored_from_snapshot_id = operation.source_snapshot_id
    version.commit_sha = command.planned_commit_sha
    version.status = "ready"
    message.snapshot_id = snapshot.id
    message.content = "Готово — версия адаптирована к текущим данным и восстановлена."
    message.tokens_out = message.tokens_out or 0
    state = dict(run.agent_state or {})
    state.update(
        {
            "snapshot_id": str(snapshot.id),
            "commit_sha": command.planned_commit_sha,
            "changed_files": sorted(files),
            "restoration_adaptation_activation": {
                "state": "completed",
                "operation_id": str(operation.id),
                "activation_id": str(offer.activation_id),
                "receipt_digest": receipt.receipt_digest,
                "publication_consumed": True,
            },
            "restoration_adaptation_owner_status": "terminal_pending",
        }
    )
    run.agent_state = state
    run.status = "completed"
    run.error = None
    run.finished_at = datetime.now(UTC)
    if operation.activation_settled_at is None:
        await consume_free_generation(
            session,
            is_free=dispatch.is_free,
            user_id=run.user_id,
        )
        operation.activation_settled_at = datetime.now(UTC)
    operation.activation_receipt = receipt.model_dump(mode="json")
    operation.activation_receipt_digest = receipt.receipt_digest
    operation.activation_effects_admitted = True
    operation.prior_fencing_epoch = offer.expected_source_fencing_epoch
    operation.fencing_epoch = offer.target_fencing_epoch
    operation.state = "completed"
    operation.phase = "activation_complete"
    operation.error = None
    operation.applied_snapshot_id = snapshot.id
    operation.applied_version_id = version.id
    operation.activation_notification_state = "pending"
    _touch(operation)
    await session.commit()
    return True


async def activate_restoration_adaptation(
    factory: async_sessionmaker[AsyncSession],
    *,
    generation_run_id: UUID,
    files: Mapping[str, str],
) -> bool:
    """Create or resume a durable activation without holding SQL locks during HTTP."""
    exact_files = dict(files)
    async with factory() as session:
        run_hint = await session.get(GenerationRun, generation_run_id)
        if run_hint is None:
            raise ApiError("not_found", "generation not found", 404)
        bundle, _ = _adaptation_state(run_hint)
        try:
            operation_id = UUID(str(bundle["operation_id"]))
        except (KeyError, TypeError, ValueError) as exc:
            raise ApiError("conflict", "Restoration adaptation binding is invalid", 409) from exc
        project, operation = await _owned_operation(
            session, run_hint.project_id, run_hint.user_id, operation_id
        )
        run = await session.get(GenerationRun, generation_run_id, with_for_update=True)
        if run is None:
            raise ApiError("not_found", "generation not found", 404)
        bundle, proof = _adaptation_state(run)
        workspace = await session.get(
            ProjectCellWorkspace, operation.workspace_id, with_for_update=True
        )
        if operation.state == "completed":
            if (
                operation.activation_settled_at is None
                or operation.applied_snapshot_id is None
                or operation.applied_version_id is None
            ):
                raise ApiError("conflict", "Completed restoration activation is incomplete", 409)
            return True
        if (
            workspace is None
            or operation.selected_branch != "adaptive"
            or operation.adaptation_run_id != run.id
            or operation.state not in {"adapting", "applying", "reconciling", "completed"}
            or project.current_snapshot_id != operation.base_draft_snapshot_id
            or bundle.get("project_id") != str(project.id)
            or bundle.get("owner_id") != str(run.user_id)
            or bundle.get("adaptation_run_id") != str(run.id)
            or bundle.get("base_draft_snapshot_id") != str(project.current_snapshot_id)
            or proof.get("operation_id") != str(operation.id)
            or proof.get("source_workspace_id") != str(operation.workspace_id)
        ):
            raise ApiError("conflict", "Restoration adaptation binding changed", 409)
        if _activation_cancel_requested(operation, run):
            _mark_activation_cancel_requested(operation)
            if operation.activation_request is None:
                await _settle_cancelled_adaptation_locked(session, operation)
            else:
                operation.state = "reconciling"
                operation.phase = "activation_cancel"
                operation.error = None
                _touch(operation)
            await session.commit()
            return False
        base = await session.get(Snapshot, operation.base_draft_snapshot_id)
        if (
            base is None
            or base.project_id != project.id
            or base.commit_sha != operation.base_commit_sha
        ):
            raise ApiError("conflict", "Restoration activation base changed", 409)
        base_commit_sha = base.commit_sha
        planned_sha = operation.adaptation_planned_commit_sha
        request = _activation_offer_request(operation=operation, run=run, proof=proof)
        saved_request = request.model_dump(mode="json")
        saved_intent = {
            "offer_request": saved_request,
            "candidate_files": exact_files,
            "candidate_artifact_digest": canonical_files_digest(exact_files),
        }
        if operation.activation_offer is None:
            if (
                operation.activation_request is not None
                and operation.activation_request != saved_intent
            ):
                raise ApiError("conflict", "Restoration activation intent changed", 409)
            operation.activation_request = saved_intent
            operation.activation_request_digest = canonical_activation_digest(saved_intent)
            operation.state = "applying"
            operation.phase = "activation_offer_intent"
            operation.error = None
            _touch(operation)
            await session.commit()
            offer = None
        else:
            offer = RestorationAdaptationActivationOffer.model_validate_json(
                json.dumps(operation.activation_offer), strict=True
            )
            await session.commit()

    if planned_sha is None:
        planned_sha = await asyncio.to_thread(
            repo.prepare_restoration_adaptation_commit,
            request.project_id,
            exact_files,
            base_commit_sha,
            request.operation_id,
        )
        async with factory() as session:
            _, operation = await _owned_operation(
                session, request.project_id, request.owner_id, request.operation_id
            )
            if operation.adaptation_planned_commit_sha not in {None, planned_sha}:
                raise ApiError("conflict", "Restoration activation Git identity changed", 409)
            operation.adaptation_planned_commit_sha = planned_sha
            await session.commit()
    else:
        planned_files = await asyncio.to_thread(
            repo.read_files, request.project_id, planned_sha
        )
        if planned_files != exact_files:
            raise ApiError("conflict", "Restoration activation Git tree changed", 409)
    planned_manifest_digest = await asyncio.to_thread(
        repo.restoration_adaptation_source_manifest_digest,
        request.project_id,
        planned_sha,
    )
    if planned_manifest_digest != proof.get("candidate_source_manifest_digest"):
        raise ApiError("conflict", "Restoration activation source manifest changed", 409)

    if offer is None:
        try:
            offer = await project_cell_offer_restoration_adaptation_activation(request)
        except Exception as exc:
            async with factory() as session:
                _, operation = await _owned_operation(
                    session, request.project_id, request.owner_id, request.operation_id
                )
                if operation.state in {"applying", "reconciling"}:
                    operation.state = "reconciling"
                    operation.phase = (
                        "activation_cancel"
                        if _activation_cancel_requested(operation)
                        else "activation_offer"
                    )
                    operation.error = str(exc)[:2000]
                    _touch(operation)
                    await session.commit()
            return False

    async with factory() as session:
        project, operation = await _owned_operation(
            session, request.project_id, request.owner_id, request.operation_id
        )
        run = await session.get(GenerationRun, generation_run_id, with_for_update=True)
        workspace = await session.get(
            ProjectCellWorkspace, request.workspace_id, with_for_update=True
        )
        if workspace is None or run is None:
            raise ApiError("conflict", "Restoration activation owner disappeared", 409)
        _, proof = _adaptation_state(run)
        _validate_activation_offer(
            request=request,
            offer=offer,
            operation=operation,
            proof=proof,
            files=exact_files,
            workspace=workspace,
        )
        if (
            project.current_snapshot_id != operation.base_draft_snapshot_id
            or operation.adaptation_planned_commit_sha != planned_sha
        ):
            raise ApiError("conflict", "Restoration activation base changed", 409)
        await session.commit()

    command = RestorationAdaptationActivationCommand(
        offer=offer, planned_commit_sha=planned_sha
    )
    async with factory() as session:
        project, operation = await _owned_operation(
            session, request.project_id, request.owner_id, request.operation_id
        )
        run = await session.get(GenerationRun, generation_run_id, with_for_update=True)
        workspace = await session.get(
            ProjectCellWorkspace, request.workspace_id, with_for_update=True
        )
        if run is None:
            raise ApiError("conflict", "Restoration activation owner disappeared", 409)
        cancel_requested = _activation_cancel_requested(operation, run)
        if cancel_requested:
            _mark_activation_cancel_requested(operation)
        if (
            operation.state not in {"applying", "reconciling"}
            or operation.phase not in {
                "activation_offer_intent",
                "activation_offer",
                "activation_cancel",
            }
            or operation.selected_branch != "adaptive"
            or operation.adaptation_run_id != request.generation_run_id
            or operation.activation_effects_admitted
            or project.current_snapshot_id != operation.base_draft_snapshot_id
            or workspace is None
            or workspace.fencing_epoch != offer.expected_source_fencing_epoch
        ):
            raise ApiError("conflict", "Restoration activation intent is no longer current", 409)
        if (
            operation.activation_offer is not None
            and operation.activation_offer != offer.model_dump(mode="json")
        ):
            raise ApiError("conflict", "Restoration activation offer changed", 409)
        if operation.activation_request not in (
            saved_intent,
            command.model_dump(mode="json"),
        ):
            raise ApiError("conflict", "Restoration activation request changed", 409)
        operation.activation_id = offer.activation_id
        operation.activation_offer = offer.model_dump(mode="json")
        operation.activation_offer_digest = offer.offer_digest
        operation.activation_request = command.model_dump(mode="json")
        operation.activation_request_digest = command.digest()
        operation.adaptation_planned_commit_sha = planned_sha
        operation.prior_fencing_epoch = offer.expected_source_fencing_epoch
        operation.fencing_epoch = offer.target_fencing_epoch
        operation.state = "reconciling" if cancel_requested else "applying"
        operation.phase = "activation_cancel" if cancel_requested else "activation_intent"
        operation.error = None
        _touch(operation)
        await session.commit()

    try:
        receipt = await (
            project_cell_cancel_restoration_adaptation_activation(command)
            if cancel_requested
            else project_cell_apply_restoration_adaptation_activation(command)
        )
    except Exception as apply_exc:
        try:
            receipt = await project_cell_status_restoration_adaptation_activation(command)
        except Exception as status_exc:
            async with factory() as session:
                _, operation = await _owned_operation(
                    session, request.project_id, request.owner_id, request.operation_id
                )
                if operation.state in {"applying", "reconciling"}:
                    sticky_cancel = _activation_cancel_requested(operation)
                    operation.state = "reconciling"
                    operation.phase = (
                        "activation_cancel" if sticky_cancel else "activation_status"
                    )
                    operation.error = f"{apply_exc}; {status_exc}"[:2000]
                    _touch(operation)
                    await session.commit()
            return False

    async with factory() as session:
        _, operation = await _owned_operation(
            session, request.project_id, request.owner_id, request.operation_id
        )
        run = await session.get(GenerationRun, generation_run_id, with_for_update=True)
        sticky_cancel = _activation_cancel_requested(operation, run)
        if sticky_cancel:
            _mark_activation_cancel_requested(operation)
        _validate_activation_receipt_progression(operation, receipt)
        operation.activation_receipt = receipt.model_dump(mode="json")
        operation.activation_receipt_digest = receipt.receipt_digest
        operation.activation_effects_admitted = receipt.effects_admitted
        if receipt.state == "cancelled":
            await _settle_cancelled_adaptation_locked(
                session, operation, receipt=receipt
            )
        elif receipt.state != "activated":
            operation.state = "reconciling"
            operation.phase = (
                "activation_status"
                if receipt.effects_admitted or not sticky_cancel
                else "activation_cancel"
            )
            operation.error = None
            _touch(operation)
        else:
            operation.error = None
            _touch(operation)
        await session.commit()
        if receipt.state != "activated":
            return False
    async with factory() as session:
        return await _complete_restoration_adaptation_activation(
            session,
            run_id=generation_run_id,
            command=command,
            receipt=receipt,
            files=exact_files,
        )


async def adaptation_activation_consumed(
    factory: async_sessionmaker[AsyncSession], generation_run_id: UUID
) -> bool:
    async with factory() as session:
        run = await session.get(GenerationRun, generation_run_id)
        state = run.agent_state if run is not None and isinstance(run.agent_state, dict) else {}
        activation = state.get("restoration_adaptation_activation")
        return isinstance(activation, dict) and activation.get("publication_consumed") is True


async def adaptation_activation_holds_generation_lease(
    factory: async_sessionmaker[AsyncSession], generation_run_id: UUID
) -> bool:
    """Keep source/candidate ownership once a proof request may have reached the controller."""
    async with factory() as session:
        operation = await session.scalar(
            select(Restoration).where(
                Restoration.adaptation_run_id == generation_run_id,
                Restoration.selected_branch == "adaptive",
                Restoration.state.in_(("adapting", "applying", "reconciling")),
            )
        )
        if operation is None:
            return False
        run = await session.get(GenerationRun, generation_run_id)
        if run is None:
            return False
        root = run.agent_state if isinstance(run.agent_state, dict) else {}
        finalization = root.get("max_finalization")
        proof = (
            finalization.get("restoration_adaptation_proof")
            if isinstance(finalization, dict)
            else None
        )
        attempt = (
            finalization.get("restoration_adaptation_proof_attempt")
            if isinstance(finalization, dict)
            else None
        )
        return bool(
            (isinstance(proof, dict) and proof.get("state") == "proof_ready")
            or (isinstance(attempt, dict) and attempt.get("status") == "issued")
        )


async def adaptation_activation_handoff_pending(
    factory: async_sessionmaker[AsyncSession], generation_run_id: UUID
) -> bool:
    """Recognize only a complete controller-owned adaptation outbox.

    The generation executor must never replay an already-started model turn. Once
    finalization has durably handed a proof or activation command to the
    restoration reconciler, it also must not misclassify that intentional handoff
    as a crashed executor. Invalid or incomplete journals remain ordinary orphans.
    """

    async with factory() as session:
        run_hint = await session.get(GenerationRun, generation_run_id)
        if run_hint is None or run_hint.status != "running":
            return False
        root_hint = run_hint.agent_state if isinstance(run_hint.agent_state, dict) else {}
        raw_binding = root_hint.get("restoration_adaptation")
        if not isinstance(raw_binding, dict):
            return False
        try:
            operation_id = UUID(str(raw_binding["operation_id"]))
            _, operation = await _owned_operation(
                session,
                run_hint.project_id,
                run_hint.user_id,
                operation_id,
            )
        except (ApiError, KeyError, TypeError, ValueError):
            await session.rollback()
            return False
        run = await session.get(GenerationRun, generation_run_id, with_for_update=True)
        if run is None or run.status != "running":
            await session.commit()
            return False
        root = run.agent_state if isinstance(run.agent_state, dict) else {}
        binding = root.get("restoration_adaptation")
        raw_finalization = root.get("max_finalization")
        finalization = raw_finalization if isinstance(raw_finalization, dict) else {}
        raw_command = operation.activation_request
        try:
            raw_command_digest = _digest(raw_command) if isinstance(raw_command, dict) else None
        except (TypeError, ValueError):
            raw_command_digest = None
        if (
            not isinstance(binding, dict)
            or binding.get("operation_id") != str(operation.id)
            or binding.get("adaptation_run_id") != str(run.id)
            or operation.selected_branch != "adaptive"
            or operation.adaptation_run_id != run.id
            or operation.project_id != run.project_id
            or operation.owner_id != run.user_id
            or operation.state not in {"applying", "reconciling"}
            or not isinstance(operation.phase, str)
            or not operation.phase.startswith("activation_")
            or not isinstance(raw_command, dict)
            or operation.activation_request_digest != raw_command_digest
        ):
            await session.commit()
            return False
        if "proof_request" in raw_command:
            try:
                (
                    workspace,
                    _files,
                    artifact_digest,
                    candidate_revision,
                    candidate_proof_key,
                    proof_attempt,
                ) = _adaptation_proof_intent(raw_command)
            except ApiError:
                await session.commit()
                return False
            raw_attempt = finalization.get("restoration_adaptation_proof_attempt")
            attempt = raw_attempt if isinstance(raw_attempt, dict) else {}
            attempt_number = attempt.get("number")
            attempt_fencing_epoch = attempt.get("candidate_fencing_epoch")
            valid = bool(
                operation.phase == "activation_proof_intent"
                and not operation.activation_effects_admitted
                and workspace.source_workspace_id == operation.workspace_id
                and workspace.operation_id == operation.id
                and workspace.project_id == run.project_id
                and workspace.owner_id == run.user_id
                and workspace.generation_run_id == run.id
                and attempt.get("status") == "issued"
                and type(attempt_number) is int
                and attempt_number > 0
                and attempt_number == proof_attempt
                and attempt.get("candidate_workspace_id")
                == str(workspace.candidate_workspace_id)
                and type(attempt_fencing_epoch) is int
                and attempt_fencing_epoch > 0
                and attempt_fencing_epoch == workspace.candidate_fencing_epoch
                and attempt.get("candidate_workspace_revision") == candidate_revision
                and attempt.get("candidate_proof_key") == candidate_proof_key
                and attempt.get("candidate_artifact_digest") == artifact_digest
            )
            await session.commit()
            return valid

        raw_proof = finalization.get("restoration_adaptation_proof")
        proof = raw_proof if isinstance(raw_proof, dict) else {}
        if proof.get("state") != "proof_ready":
            await session.commit()
            return False
        if "offer_request" in raw_command:
            raw_offer_request = raw_command.get("offer_request")
            candidate_files = raw_command.get("candidate_files")
            offer_artifact_digest = raw_command.get("candidate_artifact_digest")
            try:
                offer_request = RestorationAdaptationActivationOfferRequest.model_validate_json(
                    json.dumps(raw_offer_request), strict=True
                )
                expected_offer_request = _activation_offer_request(
                    operation=operation, run=run, proof=proof
                )
            except (ApiError, TypeError, ValueError):
                await session.commit()
                return False
            valid = bool(
                operation.phase
                in {"activation_offer_intent", "activation_offer", "activation_cancel"}
                and isinstance(candidate_files, dict)
                and all(
                    isinstance(path, str) and isinstance(content, str)
                    for path, content in candidate_files.items()
                )
                and isinstance(offer_artifact_digest, str)
                and offer_artifact_digest
                == canonical_files_digest(cast(dict[str, str], candidate_files))
                and offer_request == expected_offer_request
            )
            await session.commit()
            return valid
        try:
            command = RestorationAdaptationActivationCommand.model_validate_json(
                json.dumps(raw_command), strict=True
            )
        except (TypeError, ValueError):
            await session.commit()
            return False
        valid = bool(
            operation.phase
            in {
                "activation_intent",
                "activation_status",
                "activation_cancel",
            }
            and operation.activation_id == command.offer.activation_id
            and operation.adaptation_planned_commit_sha == command.planned_commit_sha
            and operation.activation_request_digest == command.digest()
            and command.offer.operation_id == operation.id
            and command.offer.project_id == run.project_id
            and command.offer.owner_id == run.user_id
            and command.offer.generation_run_id == run.id
            and command.offer.proof_digest == proof.get("proof_digest")
        )
        await session.commit()
        return valid


async def _resume_restoration_adaptation_activation(
    session: AsyncSession,
    *,
    project_id: UUID,
    owner_id: UUID,
    operation_id: UUID,
) -> RestoreOperation:
    """Reconcile a durable command after a lost API process or uncertain HTTP response."""
    _, operation = await _owned_operation(session, project_id, owner_id, operation_id)
    raw_command = operation.activation_request
    run = (
        await session.get(GenerationRun, operation.adaptation_run_id, with_for_update=True)
        if operation.adaptation_run_id is not None
        else None
    )
    cancel_requested = _activation_cancel_requested(operation, run)
    if cancel_requested:
        _mark_activation_cancel_requested(operation)
    if cancel_requested and (
        not isinstance(raw_command, dict) or "proof_request" in raw_command
    ):
        await _settle_cancelled_adaptation_locked(session, operation)
        await session.commit()
        return public_operation(operation)
    if cancel_requested and operation.phase != "activation_cancel":
        operation.state = "reconciling"
        operation.phase = "activation_cancel"
        operation.error = None
        _touch(operation)
    if operation.state == "completed" or not isinstance(raw_command, dict):
        return public_operation(operation)
    if "proof_request" in raw_command:
        (
            proof_workspace,
            proof_files,
            proof_artifact_digest,
            candidate_workspace_revision,
            candidate_proof_key,
            proof_attempt,
        ) = _adaptation_proof_intent(raw_command)
        if (
            proof_workspace.source_workspace_id != operation.workspace_id
            or proof_workspace.operation_id != operation.id
            or proof_workspace.project_id != operation.project_id
            or proof_workspace.owner_id != operation.owner_id
            or proof_workspace.generation_run_id != operation.adaptation_run_id
            or operation.state not in {"applying", "reconciling"}
            or operation.phase != "activation_proof_intent"
        ):
            raise ApiError("conflict", "Restoration adaptation proof intent changed", 409)
        await session.commit()
        proof_receipt = await project_cell_prove_restoration_adaptation(
            proof_workspace,
            candidate_workspace_revision=candidate_workspace_revision,
            candidate_proof_key=candidate_proof_key,
            candidate_artifact_digest=proof_artifact_digest,
            proof_attempt=proof_attempt,
        )
        from omnia_api.services.max_finalization import MaxFinalizationCoordinator

        await MaxFinalizationCoordinator.persist_adaptation_proof_receipt(
            session,
            generation_run_id=proof_workspace.generation_run_id,
            project_id=proof_workspace.project_id,
            receipt=proof_receipt,
            files=proof_files,
        )
        _, operation = await _read_owned_operation(
            session, project_id, owner_id, operation_id
        )
        return public_operation(operation)
    if "offer" not in raw_command:
        raw_offer_request = raw_command.get("offer_request")
        candidate_files = raw_command.get("candidate_files")
        candidate_digest = raw_command.get("candidate_artifact_digest")
        if not isinstance(raw_offer_request, dict) or not isinstance(candidate_files, dict):
            raise ApiError("conflict", "Restoration activation outbox is invalid", 409)
        if any(
            not isinstance(path, str) or not isinstance(content, str)
            for path, content in candidate_files.items()
        ) or candidate_digest != canonical_files_digest(candidate_files):
            raise ApiError("conflict", "Restoration activation candidate changed", 409)
        try:
            offer_request = RestorationAdaptationActivationOfferRequest.model_validate_json(
                json.dumps(raw_offer_request), strict=True
            )
        except ValueError as exc:
            raise ApiError("conflict", "Restoration activation journal is invalid", 409) from exc
        planned_sha = operation.adaptation_planned_commit_sha
        if planned_sha is None:
            base = await session.get(Snapshot, operation.base_draft_snapshot_id)
            if base is None or base.commit_sha != operation.base_commit_sha:
                raise ApiError("conflict", "Restoration activation base changed", 409)
            base_commit_sha = base.commit_sha
            await session.commit()
            planned_sha = await asyncio.to_thread(
                repo.prepare_restoration_adaptation_commit,
                project_id,
                candidate_files,
                base_commit_sha,
                operation_id,
            )
            _, operation = await _owned_operation(
                session, project_id, owner_id, operation_id
            )
            if operation.adaptation_planned_commit_sha not in {None, planned_sha}:
                raise ApiError("conflict", "Restoration activation Git identity changed", 409)
            operation.adaptation_planned_commit_sha = planned_sha
            await session.commit()
        await session.commit()
        offer = await project_cell_offer_restoration_adaptation_activation(offer_request)
        files = await asyncio.to_thread(repo.read_files, project_id, planned_sha)
        if files != candidate_files:
            raise ApiError("conflict", "Restoration activation Git tree changed", 409)
        planned_manifest_digest = await asyncio.to_thread(
            repo.restoration_adaptation_source_manifest_digest,
            project_id,
            planned_sha,
        )
        project, operation = await _owned_operation(session, project_id, owner_id, operation_id)
        run = await session.get(
            GenerationRun, offer_request.generation_run_id, with_for_update=True
        )
        workspace = await session.get(
            ProjectCellWorkspace, offer_request.workspace_id, with_for_update=True
        )
        if run is None or workspace is None:
            raise ApiError("conflict", "Restoration activation owner disappeared", 409)
        cancel_requested = _activation_cancel_requested(operation, run)
        if cancel_requested:
            _mark_activation_cancel_requested(operation)
        if (
            operation.state not in {"applying", "reconciling"}
            or operation.phase
            not in {"activation_offer_intent", "activation_offer", "activation_cancel"}
            or operation.selected_branch != "adaptive"
            or operation.adaptation_run_id != offer_request.generation_run_id
            or operation.activation_effects_admitted
            or operation.activation_request != raw_command
            or operation.activation_request_digest != _digest(raw_command)
            or operation.adaptation_planned_commit_sha != planned_sha
            or project.current_snapshot_id != operation.base_draft_snapshot_id
            or workspace.fencing_epoch != offer.expected_source_fencing_epoch
        ):
            raise ApiError("conflict", "Restoration activation intent is no longer current", 409)
        _, proof = _adaptation_state(run)
        if planned_manifest_digest != proof.get("candidate_source_manifest_digest"):
            raise ApiError("conflict", "Restoration activation source manifest changed", 409)
        _validate_activation_offer(
            request=offer_request,
            offer=offer,
            operation=operation,
            proof=proof,
            files=files,
            workspace=workspace,
        )
        command = RestorationAdaptationActivationCommand(
            offer=offer, planned_commit_sha=planned_sha
        )
        operation.activation_id = offer.activation_id
        operation.activation_offer = offer.model_dump(mode="json")
        operation.activation_offer_digest = offer.offer_digest
        operation.activation_request = command.model_dump(mode="json")
        operation.activation_request_digest = command.digest()
        operation.prior_fencing_epoch = offer.expected_source_fencing_epoch
        operation.fencing_epoch = offer.target_fencing_epoch
        operation.state = "reconciling" if cancel_requested else "applying"
        operation.phase = "activation_cancel" if cancel_requested else "activation_intent"
        operation.error = None
        _touch(operation)
        await session.commit()
    else:
        try:
            command = RestorationAdaptationActivationCommand.model_validate_json(
                json.dumps(raw_command), strict=True
            )
        except ValueError as exc:
            raise ApiError("conflict", "Restoration activation journal is invalid", 409) from exc
    if (
        operation.activation_id != command.offer.activation_id
        or operation.activation_request_digest != command.digest()
        or operation.adaptation_planned_commit_sha != command.planned_commit_sha
    ):
        raise ApiError("conflict", "Restoration activation journal changed", 409)
    await session.commit()
    if cancel_requested:
        try:
            receipt = await project_cell_cancel_restoration_adaptation_activation(command)
        except Exception:
            receipt = await project_cell_status_restoration_adaptation_activation(command)
    else:
        try:
            receipt = await project_cell_status_restoration_adaptation_activation(command)
        except Exception:
            receipt = await project_cell_apply_restoration_adaptation_activation(command)
    files = await asyncio.to_thread(repo.read_files, project_id, command.planned_commit_sha)
    _, operation = await _owned_operation(session, project_id, owner_id, operation_id)
    run = await session.get(
        GenerationRun, command.offer.generation_run_id, with_for_update=True
    )
    cancel_requested = _activation_cancel_requested(operation, run)
    if cancel_requested:
        _mark_activation_cancel_requested(operation)
    _validate_activation_receipt_progression(operation, receipt)
    operation.activation_receipt = receipt.model_dump(mode="json")
    operation.activation_receipt_digest = receipt.receipt_digest
    operation.activation_effects_admitted = receipt.effects_admitted
    operation.error = None
    if receipt.state == "cancelled":
        await _settle_cancelled_adaptation_locked(session, operation, receipt=receipt)
        await session.commit()
        return public_operation(operation)
    if receipt.state != "activated":
        operation.state = "reconciling"
        operation.phase = (
            "activation_status"
            if receipt.effects_admitted or not cancel_requested
            else "activation_cancel"
        )
        _touch(operation)
        await session.commit()
        return public_operation(operation)
    _touch(operation)
    await session.commit()
    await _complete_restoration_adaptation_activation(
        session,
        run_id=command.offer.generation_run_id,
        command=command,
        receipt=receipt,
        files=files,
    )
    _, completed = await _read_owned_operation(session, project_id, owner_id, operation_id)
    return public_operation(completed)


async def list_operations(
    session: AsyncSession,
    project_id: UUID,
    owner_id: UUID,
) -> list[RestoreOperation]:
    await _read_owned_project(session, project_id, owner_id)
    recent = (
        select(Restoration.id)
        .where(Restoration.project_id == project_id)
        .order_by(
            Restoration.created_at.desc(),
            Restoration.id.desc(),
        )
        .limit(50)
    )
    rows = (
        await session.scalars(
            select(Restoration)
            .where(
                Restoration.project_id == project_id,
                or_(Restoration.id.in_(recent), Restoration.state.in_(ACTIVE_RESTORATION_STATES)),
            )
            .order_by(Restoration.created_at.desc(), Restoration.id.desc())
        )
    ).all()
    return [public_operation(row) for row in rows]


def validate_runtime_response(request: dict[str, Any], result: RuntimeRestoration) -> None:
    for key in ("operation_id", "workspace_id", "project_id", "owner_id"):
        if str(getattr(result, key)) != request[key]:
            raise ValueError("restoration response identity mismatch")
    binding_contract_version = request.get("binding_contract_version")
    bound_contract = binding_contract_version in {2, 3}
    if (
        bound_contract
        and result.state == "ready"
        and (
            result.binding is None or result.binding_digest is None or result.can_apply is not True
        )
    ):
        raise ValueError("restoration source binding is required")
    if (
        bound_contract
        and result.binding is not None
        and result.binding.version != binding_contract_version
    ):
        raise ValueError("restoration source binding contract mismatch")
    expected_binding = request.get("binding_digest")
    if expected_binding is not None and result.binding_digest != expected_binding:
        raise ValueError("restoration source binding mismatch")
    if (
        result.observed is not None
        and expected_binding is not None
        and result.observed.binding_digest != expected_binding
    ):
        raise ValueError("restoration activation binding mismatch")
    if result.observed is not None and (
        result.observed.source_commit_sha != request["planned_commit_sha"]
        or result.observed.fencing_epoch != request["fencing_epoch"]
        or result.observed.candidate_id != result.candidate_id
        or (
            request.get("candidate_id") is not None
            and str(result.candidate_id) != request["candidate_id"]
        )
    ):
        raise ValueError("restoration activation evidence mismatch")
    if (
        isinstance(result.observed, RuntimeRecoveryObserved)
        and result.observed.rejected_before_effect is True
    ):
        retained_epoch = result.observed.retained_source_fencing_epoch
        expected_epoch = request.get("expected_fencing_epoch")
        if (
            type(retained_epoch) is not int
            or type(expected_epoch) is not int
            or retained_epoch > expected_epoch
        ):
            raise ValueError("restoration rejection fence mismatch")


def _should_redispatch_lost_apply(
    operation: Restoration,
    payload: dict[str, Any],
    result: RuntimeRestoration,
    *,
    same_receipt: bool,
) -> bool:
    """Accept only the exact prepared receipt owned by the durable apply claim."""
    return (
        same_receipt
        and operation.phase == "apply"
        and operation.apply_digest is not None
        and result.state == "ready"
        and result.observed is None
        and result.error is None
        and result.can_apply is True
        and result.candidate_id is not None
        and operation.candidate_id == result.candidate_id
        and payload.get("candidate_id") == str(result.candidate_id)
        and operation.source_binding_digest is not None
        and payload.get("binding_digest") == operation.source_binding_digest
        and result.binding_digest == operation.source_binding_digest
        and result.report is not None
        and type(payload.get("report_revision")) is int
        and result.report.revision == payload["report_revision"]
        and result.revision == operation.runtime_revision
        and type(payload.get("fencing_epoch")) is int
        and operation.fencing_epoch == payload["fencing_epoch"]
        and type(payload.get("expected_fencing_epoch")) is int
        and operation.prior_fencing_epoch == payload["expected_fencing_epoch"]
    )


async def create_restoration(
    session: AsyncSession,
    project_id: UUID,
    owner_id: UUID,
    request: RestoreRequest,
    runtime: RestorationRuntime,
    *,
    _source_attempt: int = 0,
) -> RestoreOperation:
    project = await _owned_project_for_admission(session, project_id, owner_id)
    digest = restoration_request_digest(project_id, owner_id, request)
    existing = await session.scalar(
        select(Restoration).where(
            Restoration.project_id == project_id,
            Restoration.idempotency_key == request.idempotency_key,
        )
    )
    if existing:
        if existing.request_digest != digest:
            raise ApiError("conflict", "Restoration idempotency key was reused", 409)
        identifier = existing.id
        await session.commit()
        return await get_restoration(session, project_id, owner_id, identifier)
    project = await lock_restoration_admission(session, project_id, owner_id)
    if project.template != "max_miniapp":
        raise ApiError("conflict", "This restoration flow requires a MAX project", 409)
    if project.current_snapshot_id != request.expected_draft_snapshot_id:
        raise ApiError("conflict", "The draft changed; refresh version history", 409)
    version = await session.get(ProjectVersion, request.target_version_id)
    if version is None or version.project_id != project_id:
        raise ApiError("not_found", "version not found", 404)
    status, source = await resolve_version(session, version)
    base = await session.get(Snapshot, request.expected_draft_snapshot_id)
    if (
        status not in {"ready", "unchanged"}
        or source is None
        or base is None
        or base.project_id != project_id
    ):
        raise ApiError("conflict", "Version source is not available for restoration", 409)
    workspace = await session.scalar(
        select(ProjectCellWorkspace)
        .where(
            ProjectCellWorkspace.project_id == project_id,
            ProjectCellWorkspace.owner_id == owner_id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if workspace is None or workspace.state in {"deleting", "deleted"}:
        raise ApiError("conflict", "Project environment is unavailable", 409)
    if _source_attempt >= 3:
        raise ApiError(
            "orchestrator_unavailable", "Среда ещё восстанавливается. Повторите подготовку.", 503
        )
    if await _prepare_source_environment(session, workspace):
        # Lifecycle I/O committed its own durable intent. Recheck every admission
        # condition before binding the restoration to the resulting source epoch.
        return await create_restoration(
            session,
            project_id,
            owner_id,
            request,
            runtime,
            _source_attempt=_source_attempt + 1,
        )
    await _require_idle_cell(session, workspace)
    operation = Restoration(
        id=uuid4(),
        project_id=project_id,
        owner_id=owner_id,
        workspace_id=workspace.id,
        source_version_id=version.id,
        source_snapshot_id=source.id,
        base_draft_snapshot_id=base.id,
        target_commit_sha=source.commit_sha,
        base_commit_sha=base.commit_sha,
        idempotency_key=request.idempotency_key,
        request_digest=digest,
        execution_policy=request.execution_policy,
        selected_branch=None,
        adaptation_run_id=None,
        fencing_epoch=workspace.fencing_epoch,
        state="preparing",
        phase="prepare",
        revision=1,
        runtime_revision=0,
        request_payload={},
        reconcile_attempts=0,
    )
    schedule_reconcile(operation)  # A crash before dispatch is resumed by the worker.
    session.add(operation)
    await session.commit()  # Claim survives a crash before Git export or any HTTP dispatch.
    return await _prepare(session, project_id, owner_id, operation.id, runtime)


async def _prepare_source_environment(
    session: AsyncSession,
    workspace: ProjectCellWorkspace,
) -> bool:
    from omnia_api.services import orchestrator_client, project_cell_runtime
    from omnia_api.services.project_cell_capacity import release_one_stale_generation_lease

    if workspace.generation_run_id is not None:
        run = await session.get(GenerationRun, workspace.generation_run_id)
        if (
            run is None
            or run.project_id != workspace.project_id
            or run.user_id != workspace.owner_id
            or run.status in ACTIVE_GENERATION_STATUSES
        ):
            raise ApiError("conflict", "Wait for the current generation to finish", 409)
        run_id, workspace_id = run.id, workspace.id
        factory = async_sessionmaker(session.bind, expire_on_commit=False)
        await session.commit()  # The lifecycle helper needs its own workspace lock.
        released = await release_one_stale_generation_lease(
            factory,
            requesting_run_id=run_id,
            workspace_id=workspace_id,
            client=orchestrator_client.HttpProjectCellOrchestratorClient(),
            reclaim_for_repair=False,
        )
        if not released:
            raise ApiError(
                "orchestrator_unavailable",
                "Завершаем предыдущую операцию среды. Повторите подготовку.",
                503,
            )
        return True
    wake = await project_cell_runtime._unfinished_owner_wake(session, workspace.id)
    if wake is None:
        await _require_idle_cell(session, workspace)
    resources = await project_cell_runtime._get_cell_resources(workspace.id)
    if resources.state in {"resources_paused", "retained"} or wake is not None:
        await project_cell_runtime._wake_owner_workspace(session, workspace, operation=wake)
        return True
    if resources.state != "resources_ready":
        raise ApiError(
            "orchestrator_unavailable", "Среда проекта ещё не готова. Повторите подготовку.", 503
        )
    return False


async def _require_idle_cell(session: AsyncSession, workspace: ProjectCellWorkspace) -> None:
    if workspace.generation_run_id is not None or await session.scalar(
        select(ProjectCellOperation.id)
        .where(
            ProjectCellOperation.workspace_id == workspace.id,
            ProjectCellOperation.status.in_(
                ("pending", "waiting_capacity", "running", "indeterminate"),
            ),
        )
        .limit(1)
    ):
        raise ApiError("conflict", "Project environment has an active operation", 409)


async def _prepare(
    session: AsyncSession,
    project_id: UUID,
    owner_id: UUID,
    operation_id: UUID,
    runtime: RestorationRuntime,
) -> RestoreOperation:
    project, operation = await _owned_operation(session, project_id, owner_id, operation_id)
    if operation.state not in {"preparing", "reconciling"} or operation.phase != "prepare":
        return public_operation(operation)
    if project.current_snapshot_id != operation.base_draft_snapshot_id:
        raise ApiError("conflict", "The draft changed before restoration preparation", 409)
    if not operation.request_payload:
        try:
            from omnia_api.models.max_project_config import MaxProjectConfig
            from omnia_api.schemas.max_studio import MaxProjectConfigPayload
            from omnia_api.services.max_project_kit import (
                MAX_RETIRED_MANAGED_FILES,
                render_portable_max_managed_files,
                render_portable_max_session,
            )

            config = await session.get(MaxProjectConfig, project_id)
            platform_files = (
                render_portable_max_managed_files(
                    MaxProjectConfigPayload.model_validate(config.config), project_id
                )
                if config is not None
                else {"src/lib/max/session.ts": render_portable_max_session(project_id)}
            )
            prepared = repo.prepare_restore_commit(
                project_id,
                operation.target_commit_sha,
                operation.base_commit_sha,
                operation.id,
                overrides=platform_files,
                deletes=tuple(sorted(MAX_RETIRED_MANAGED_FILES)),
            )
        except (ValueError, RuntimeError, OSError):
            operation.state = "failed"
            operation.error = "Version source could not be prepared"
            _touch(operation)
            await session.commit()
            return public_operation(operation)
        operation.planned_commit_sha = prepared["commit_sha"]
        operation.request_payload = {
            "operation_id": str(operation.id),
            "workspace_id": str(operation.workspace_id),
            "project_id": str(project_id),
            "owner_id": str(owner_id),
            "expected_source_head": operation.base_commit_sha,
            "target_commit_sha": operation.target_commit_sha,
            "planned_commit_sha": operation.planned_commit_sha,
            "fencing_epoch": operation.fencing_epoch,
            "binding_contract_version": 3,
            "files": prepared["files"],
            "current_files": prepared["current_files"],
        }
        await session.commit()
    else:
        await session.commit()
    return await _dispatch(session, project_id, owner_id, operation_id, runtime, "prepare")


async def get_restoration(
    session: AsyncSession,
    project_id: UUID,
    owner_id: UUID,
    operation_id: UUID,
) -> RestoreOperation:
    """Return the durable owner projection; reads never drive recovery or effects."""
    _, operation = await _read_owned_operation(session, project_id, owner_id, operation_id)
    return public_operation(operation)


async def advance_restoration(
    session: AsyncSession,
    project_id: UUID,
    owner_id: UUID,
    operation_id: UUID,
    runtime: RestorationRuntime,
) -> RestoreOperation:
    """Worker-only progression for a previously durable restoration intent."""
    _, operation = await _owned_operation(session, project_id, owner_id, operation_id)
    auto_apply = automatic_apply_ready(operation)
    if operation.state not in CONTROLLER_WAIT_STATES and not auto_apply:
        return public_operation(operation)
    activation_pending = (
        operation.selected_branch == "adaptive"
        and isinstance(operation.activation_request, dict)
        and operation.phase.startswith("activation_")
    )
    pending_export = not operation.request_payload and operation.state == "preparing"
    if auto_apply:
        report = public_operation(operation).report
        if report is None:
            return public_operation(operation)
        request = RestoreApplyRequest(
            report_revision=report.revision,
            expected_draft_snapshot_id=operation.base_draft_snapshot_id,
            idempotency_key=f"restoration-auto-apply:{operation.id}",
        )
    await session.commit()
    if activation_pending:
        return await _resume_restoration_adaptation_activation(
            session,
            project_id=project_id,
            owner_id=owner_id,
            operation_id=operation_id,
        )
    if pending_export:
        return await _prepare(session, project_id, owner_id, operation_id, runtime)
    if auto_apply:
        try:
            return await apply_restoration(
                session, project_id, owner_id, operation_id, request, runtime
            )
        except ApiError as exc:
            if exc.status_code != 409:
                raise
            return await _stop_automatic_pre_admission(
                session, project_id, owner_id, operation_id
            )
    return await _dispatch(session, project_id, owner_id, operation_id, runtime, "status")


async def _stop_automatic_pre_admission(
    session: AsyncSession,
    project_id: UUID,
    owner_id: UUID,
    operation_id: UUID,
) -> RestoreOperation:
    """Make a definitive automatic-apply rejection owner-visible.

    Re-read under the canonical project/operation lock. A concurrent cancel or
    admitted apply owns the operation and must never be overwritten here.
    """
    _, operation = await _owned_operation(session, project_id, owner_id, operation_id)
    if (
        operation.execution_policy == AUTOMATIC_EXECUTION_POLICY
        and operation.state == "ready"
        and operation.phase != "cancel"
        and operation.apply_digest is None
    ):
        operation.state = "needs_changes"
        operation.phase = "retry_prepare"
        operation.error = (
            "Черновик, отчёт или среда проекта изменились до применения. "
            "Запустите восстановление выбранной версии заново."
        )
        _touch(operation)
        await session.commit()
    return public_operation(operation)


async def apply_restoration(
    session: AsyncSession,
    project_id: UUID,
    owner_id: UUID,
    operation_id: UUID,
    request: RestoreApplyRequest,
    runtime: RestorationRuntime,
) -> RestoreOperation:
    project, operation = await _owned_operation(session, project_id, owner_id, operation_id)
    digest = _digest(request.model_dump(mode="json"))
    if operation.apply_digest:
        if operation.apply_digest != digest:
            raise ApiError("conflict", "The restoration apply request changed", 409)
        completed = operation.state == "completed"
        response = public_operation(operation)
        await session.commit()
        if completed:
            return response
        return await _dispatch(session, project_id, owner_id, operation_id, runtime, "apply")
    await lock_restoration_admission(session, project_id, owner_id, operation_id=operation_id)
    if (
        request.expected_draft_snapshot_id != operation.base_draft_snapshot_id
        or project.current_snapshot_id != operation.base_draft_snapshot_id
    ):
        raise ApiError("conflict", "The draft changed; prepare restoration again", 409)
    report = public_operation(operation)
    if operation.source_binding is None or operation.source_binding_digest is None:
        raise ApiError(
            "conflict", "Restoration source binding is missing; prepare restoration again", 409
        )
    if (
        not report.can_apply
        or report.report is None
        or report.report.revision != request.report_revision
    ):
        raise ApiError("conflict", "The restoration report is not ready or has changed", 409)
    workspace = await session.scalar(
        select(ProjectCellWorkspace)
        .where(
            ProjectCellWorkspace.id == operation.workspace_id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if (
        workspace is None
        or workspace.owner_id != owner_id
        or workspace.project_id != project_id
        or workspace.fencing_epoch != operation.fencing_epoch
    ):
        raise ApiError("conflict", "Project environment changed; prepare restoration again", 409)
    await _require_idle_cell(session, workspace)
    operation.prior_fencing_epoch = workspace.fencing_epoch
    workspace.fencing_epoch += 1
    workspace.version += 1
    operation.fencing_epoch = workspace.fencing_epoch
    operation.apply_digest = digest
    operation.apply_idempotency_key = request.idempotency_key
    operation.state, operation.phase = "applying", "apply"
    operation.request_payload = {
        **operation.request_payload,
        "fencing_epoch": operation.fencing_epoch,
        "expected_fencing_epoch": operation.prior_fencing_epoch,
        "report_revision": request.report_revision,
        "candidate_id": str(operation.candidate_id),
        "binding_digest": operation.source_binding_digest,
    }
    _touch(operation)
    await session.commit()  # Once-only fence and planned identity precede external activation.
    return await _dispatch(session, project_id, owner_id, operation_id, runtime, "apply")


async def cancel_restoration(
    session: AsyncSession,
    project_id: UUID,
    owner_id: UUID,
    operation_id: UUID,
    runtime: RestorationRuntime,
) -> RestoreOperation:
    _, operation = await _owned_operation(session, project_id, owner_id, operation_id)
    if operation.state == "cancelled":
        return public_operation(operation)
    if operation.activation_effects_admitted:
        raise ApiError(
            "conflict",
            "Restoration activation passed the controller point of no return",
            409,
        )
    if operation.selected_branch == "adaptive":
        _mark_activation_cancel_requested(operation)
        if not isinstance(operation.activation_request, dict):
            await _settle_cancelled_adaptation_locked(session, operation)
            await session.commit()
            return public_operation(operation)
        if "proof_request" in operation.activation_request:
            await _settle_cancelled_adaptation_locked(session, operation)
            operation.activation_request = None
            operation.activation_request_digest = None
            await session.commit()
            return public_operation(operation)
        if "offer" not in operation.activation_request:
            operation.state = "reconciling"
            operation.phase = "activation_cancel"
            operation.error = None
            _touch(operation)
            await session.commit()
            return public_operation(operation)
        try:
            command = RestorationAdaptationActivationCommand.model_validate_json(
                json.dumps(operation.activation_request), strict=True
            )
        except ValueError as exc:
            raise ApiError("conflict", "Restoration activation journal is invalid", 409) from exc
        operation.state = "reconciling"
        operation.phase = "activation_cancel"
        operation.error = None
        _touch(operation)
        await session.commit()
        receipt = await project_cell_cancel_restoration_adaptation_activation(command)
        _, operation = await _owned_operation(session, project_id, owner_id, operation_id)
        _validate_activation_receipt_progression(operation, receipt)
        if receipt.effects_admitted or receipt.state != "cancelled":
            operation.activation_receipt = receipt.model_dump(mode="json")
            operation.activation_receipt_digest = receipt.receipt_digest
            operation.activation_effects_admitted = receipt.effects_admitted
            operation.state = "reconciling"
            operation.phase = (
                "activation_status" if receipt.effects_admitted else "activation_cancel"
            )
            _touch(operation)
            await session.commit()
            raise ApiError(
                "conflict",
                "Restoration activation can no longer be cancelled",
                409,
            )
        await _settle_cancelled_adaptation_locked(session, operation, receipt=receipt)
        await session.commit()
        return public_operation(operation)
    if operation.phase == "cancel" and operation.state == "reconciling":
        await session.commit()
        return await get_restoration(session, project_id, owner_id, operation_id)
    if not public_operation(operation).can_cancel:
        raise ApiError("conflict", "Restoration can no longer be cancelled", 409)
    if not operation.request_payload:
        operation.state, operation.phase = "cancelled", "cancel"
        _touch(operation)
        await session.commit()
        return public_operation(operation)
    operation.state, operation.phase = "reconciling", "cancel"
    _touch(operation)
    await session.commit()
    return await _dispatch(session, project_id, owner_id, operation_id, runtime, "cancel")


async def _dispatch(
    session: AsyncSession,
    project_id: UUID,
    owner_id: UUID,
    operation_id: UUID,
    runtime: RestorationRuntime,
    action: str,
) -> RestoreOperation:
    _, operation = await _owned_operation(session, project_id, owner_id, operation_id)
    if operation.state not in ACTIVE_RESTORATION_STATES:
        return public_operation(operation)
    if action == "prepare" and (operation.apply_digest is not None or operation.phase == "cancel"):
        return public_operation(operation)
    payload = dict(operation.request_payload)
    await session.commit()  # Never hold SQL locks across runtime preparation/build checks.
    try:
        method = {
            "prepare": runtime.prepare,
            "status": runtime.status,
            "apply": runtime.apply,
            "cancel": runtime.cancel,
        }[action]
        result = await method(payload)
        validate_runtime_response(payload, result)
    except ApiError as exc:
        if action == "prepare" and exc.status_code == 422:
            # Request-model validation runs before the controller creates an operation.
            _, operation = await _owned_operation(session, project_id, owner_id, operation_id)
            if operation.phase == "prepare" and operation.apply_digest is None:
                operation.state = "failed"
                operation.error = "Historical source does not meet restoration requirements"
                _touch(operation)
                await session.commit()
                return public_operation(operation)
        if action == "status" and exc.status_code == 404:
            _, operation = await _owned_operation(session, project_id, owner_id, operation_id)
            intent = operation.phase
            await session.commit()
            if intent in {"prepare", "apply", "cancel"}:
                # A durable claim may precede a lost dispatch. Reuse the exact operation.
                return await _dispatch(session, project_id, owner_id, operation_id, runtime, intent)
        if _no_evidence(action, exc):
            return await _unobserved(session, project_id, owner_id, operation_id)
        return await _unconfirmed(session, project_id, owner_id, operation_id)
    except (ValueError, OSError, TimeoutError) as exc:
        if _no_evidence(action, exc):
            return await _unobserved(session, project_id, owner_id, operation_id)
        return await _unconfirmed(session, project_id, owner_id, operation_id)
    project, operation = await _owned_operation(session, project_id, owner_id, operation_id)
    # A delayed response to the old prepare envelope cannot overwrite an apply claim.
    if payload != operation.request_payload or result.revision < operation.runtime_revision:
        return public_operation(operation)
    if _adaptive_generation_owns(operation):
        # The generation admission owns this operation now. A delayed response
        # from the legacy prepare/cancel controller cannot project over it.
        return public_operation(operation)
    if (
        action == "status"
        and operation.phase == "cancel"
        and (result.can_cancel or result.state == "failed")
    ):
        # The controller has not accepted the durable cancel intent. This also
        # covers preparation failure winning the race immediately before cancel.
        await session.commit()
        return await _dispatch(session, project_id, owner_id, operation_id, runtime, "cancel")
    same_receipt = (
        result.revision == operation.runtime_revision and operation.runtime_result is not None
    )
    projection_before: tuple[Any, ...] | None = None
    if same_receipt:
        try:
            # JSON validation retains strict flags/identity checks and supplies
            # defaults introduced after the prior durable receipt was written.
            previous = RuntimeRestoration.model_validate_json(
                json.dumps(operation.runtime_result), strict=True
            )
        except (ValueError, TypeError):
            return await _unconfirmed(session, project_id, owner_id, operation_id)
        if result != previous:
            return await _unconfirmed(session, project_id, owner_id, operation_id)
        # A receipt can already be durable while a later lost action response has
        # moved the API projection back to reconciling. Re-evaluate projection
        # rules for the same evidence, then persist only a semantic correction.
        projection_before = (
            operation.state,
            operation.phase,
            operation.error,
            operation.applied_snapshot_id,
            operation.applied_version_id,
            operation.source_binding,
            operation.source_binding_digest,
            operation.selected_branch,
            project.current_snapshot_id,
        )
    if (
        action == "status"
        and operation.phase == "apply"
        and operation.apply_digest is not None
        and result.state == "ready"
    ):
        if _should_redispatch_lost_apply(
            operation,
            payload,
            result,
            same_receipt=same_receipt,
        ):
            # The controller still owns the exact prepared receipt: activation
            # was never admitted. Reuse the durable operation, candidate,
            # binding and fencing envelope; no SQL fence or owner intent is
            # created here.
            await session.commit()
            return await _dispatch(session, project_id, owner_id, operation_id, runtime, "apply")
        # A different ready receipt is not proof that this apply was never
        # admitted. Keep the original preparation evidence as the only replay
        # baseline and wait for an unambiguous controller outcome.
        await session.commit()
        return await _unconfirmed(session, project_id, owner_id, operation_id)
    if operation.state in {"completed", "cancelled", "failed"}:
        return public_operation(operation)
    result_binding = result.binding.model_dump(mode="json") if result.binding is not None else None
    if operation.source_binding is not None and (
        result_binding != operation.source_binding
        or result.binding_digest != operation.source_binding_digest
    ):
        operation.state = "reconciling"
        operation.error = "Runtime source binding changed; checking the same operation"
        _touch(operation)
        await session.commit()
        return public_operation(operation)
    if not same_receipt and operation.source_binding is None and result_binding is not None:
        operation.source_binding = result_binding
        operation.source_binding_digest = result.binding_digest
    if not same_receipt:
        operation.reconcile_attempts = 0  # controller progress restarts the backoff
        operation.runtime_revision = result.revision
        operation.runtime_result = result.model_dump(mode="json")
        operation.candidate_id = result.candidate_id
        operation.report = result.report.model_dump(mode="json") if result.report else None
    if (
        result.state == "ready"
        and result.report is not None
        and result.report.mode == "exact"
        and not result.report.blockers
        and result.can_apply is True
        and result.binding is not None
        and result.binding_digest is not None
    ):
        operation.selected_branch = "exact"
    operation.error = result.error
    if result.state == "completed":
        if operation.apply_digest is None:
            operation.state = "reconciling"
            operation.error = "Runtime activation has no owner apply claim"
        else:
            await _complete(session, project, operation)
    elif operation.apply_digest and result.state == "failed" and result.observed is None:
        # Failure alone cannot prove whether an accepted activation changed the runtime.
        operation.state = "reconciling"
    elif operation.apply_digest and result.state not in {"applying", "reconciling", "failed"}:
        # A stale pre-apply result is not evidence that an accepted apply did not execute.
        operation.state = "reconciling"
    elif operation.phase == "cancel" and result.state != "cancelled":
        operation.state = "reconciling"
    else:
        operation.state = result.state
    if projection_before == (
        operation.state,
        operation.phase,
        operation.error,
        operation.applied_snapshot_id,
        operation.applied_version_id,
        operation.source_binding,
        operation.source_binding_digest,
        operation.selected_branch,
        project.current_snapshot_id,
    ):
        await session.commit()
        return public_operation(operation)
    _touch(operation)
    await session.commit()
    return public_operation(operation)


async def _unconfirmed(
    session: AsyncSession,
    project_id: UUID,
    owner_id: UUID,
    operation_id: UUID,
) -> RestoreOperation:
    _, operation = await _owned_operation(session, project_id, owner_id, operation_id)
    if _adaptive_generation_owns(operation):
        return public_operation(operation)
    if operation.state in ACTIVE_RESTORATION_STATES:
        operation.state = "reconciling"
        operation.error = "Runtime result is not confirmed; checking the same operation"
        _touch(operation)
        await session.commit()
    return public_operation(operation)


def _no_evidence(action: str, exc: BaseException) -> bool:
    """A failed *observation* proves nothing: an offline controller, a 5xx or a
    dropped connection during ``status`` leaves the durable row as it is and the
    worker looks again with backoff. A failed *action* (prepare/apply/cancel) is
    different — the intent may have landed — and stays unconfirmed."""
    if action != "status":
        return False
    if isinstance(exc, ApiError):
        return exc.status_code >= 500
    return isinstance(exc, OSError | TimeoutError)


async def _unobserved(
    session: AsyncSession,
    project_id: UUID,
    owner_id: UUID,
    operation_id: UUID,
) -> RestoreOperation:
    _, operation = await _owned_operation(session, project_id, owner_id, operation_id)
    if _adaptive_generation_owns(operation):
        return public_operation(operation)
    await session.commit()
    return public_operation(operation)


async def _complete(session: AsyncSession, project: Project, operation: Restoration) -> None:
    snapshot_id = uuid5(operation.id, "snapshot")
    workspace = await session.scalar(
        select(ProjectCellWorkspace)
        .where(
            ProjectCellWorkspace.id == operation.workspace_id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if (
        workspace is None
        or workspace.project_id != project.id
        or workspace.owner_id != operation.owner_id
        or workspace.fencing_epoch != operation.fencing_epoch
    ):
        operation.state = "reconciling"
        operation.error = "Runtime activation fence needs reconciliation"
        return
    if project.current_snapshot_id not in {operation.base_draft_snapshot_id, snapshot_id}:
        operation.state = "reconciling"
        operation.error = "Runtime changed but draft identity needs reconciliation"
        return
    source = await session.get(Snapshot, operation.source_snapshot_id)
    if source is None or source.project_id != project.id:
        operation.state = "reconciling"
        operation.error = "Restoration provenance needs reconciliation"
        return
    if operation.planned_commit_sha is None:
        raise ValueError("missing planned restoration commit")
    repo.activate_restore_commit(
        project.id,
        operation.planned_commit_sha,
        operation.base_commit_sha,
        operation.id,
    )
    snapshot = await session.get(Snapshot, snapshot_id)
    if snapshot is None:
        snapshot = Snapshot(
            id=snapshot_id,
            project_id=project.id,
            commit_sha=operation.planned_commit_sha,
            parent_id=operation.base_draft_snapshot_id,
            prompt_text="Восстановление версии",
            model_id=source.model_id,
        )
        session.add(snapshot)
        await session.flush()
    elif (
        snapshot.project_id != project.id
        or snapshot.commit_sha != operation.planned_commit_sha
        or snapshot.parent_id != operation.base_draft_snapshot_id
    ):
        raise ValueError("restoration snapshot identity mismatch")
    project.current_snapshot_id = snapshot.id
    version = await record_restored_version(session, project, snapshot, source)
    operation.applied_snapshot_id, operation.applied_version_id = snapshot.id, version.id
    operation.state, operation.phase, operation.error = "completed", "complete", None
