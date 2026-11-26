from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from functools import partial
from typing import cast
from uuid import UUID

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from yleum_api.models.project_cell import ProjectCellOperation, ProjectCellWorkspace
from yleum_api.services.orchestrator_client import (
    ControlProjectCellResourcesRequest,
    EnsureProjectCellResourcesRequest,
    ObserveProjectCellResourcesRequest,
    OrchestratorBadRequest,
    OrchestratorReaderInventoryUnavailable,
    OrchestratorUnavailable,
    ProjectCellCapacityWait,
    ProjectCellOrchestratorClient,
    ProjectCellPreEffectRejection,
    ProjectCellReleaseEnvironmentBinding,
    ProjectCellResourceResponse,
)
from yleum_api.services.project_cells import (
    TERMINAL_OPERATION_STATUSES,
    ClaimedCellOperation,
    ProjectCellBusy,
    ProjectCellNotFound,
    ProjectCellStateConflict,
    ProjectCellValidationError,
    _hashed_error,
    claim_cell_operation_committed,
    complete_cell_operation,
    fail_cell_operation,
    mark_cell_operation_indeterminate,
    park_cell_operation_for_capacity,
    reclaim_indeterminate_cell_operation_committed,
)

_CHECKPOINT_KINDS = frozenset({"pause", "stop", "restore"})
_STATEFUL_CONTROL_KINDS = frozenset({"wake", "pause", "stop", "destroy", "restore", "release"})
RELEASE_PROOF_REJECTIONS = frozenset({
    "recovery_release_source_changed",
    "recovery_release_manifest_changed",
    "recovery_release_environment_changed",
    "recovery_release_inventory_unavailable",
    "recovery_release_capture_unproved",
})


@dataclass(frozen=True, slots=True)
class ProjectCellOperationOutcome:
    operation_id: UUID
    workspace_id: UUID
    kind: str
    status: str
    fencing_epoch: int | None
    response: ProjectCellResourceResponse | None
    result_payload: dict[str, object] | None
    error: str | None
    reconciles_operation_id: UUID | None


def matches_current_quiescent_observation(
    workspace: ProjectCellWorkspace,
    operation: ProjectCellOperation,
    response: ProjectCellResourceResponse,
    *,
    generation_run_id: UUID,
) -> bool:
    """Bind the controller's no-lease proof to the current owned workspace and operation."""
    proof = response.lease_observation
    return (
        operation.kind in {"status", "reconcile"}
        and operation.status == "completed"
        and operation.finished_at is not None
        and operation.generation_run_id == generation_run_id
        and response.state == "resources_ready"
        and proof is not None
        and proof.quiescent
        and proof.active_generation_run_id is None
        and proof.active_generation_fencing_epoch is None
        and response.workspace_id == proof.workspace_id == operation.workspace_id == workspace.id
        and response.fencing_epoch == proof.fencing_epoch == operation.fencing_epoch
        == workspace.fencing_epoch
        and proof.project_id == workspace.project_id
        and proof.owner_id == workspace.owner_id
        and proof.operation_id == operation.id
        and proof.request_digest == operation.request_digest
    )


@dataclass(frozen=True, slots=True)
class _OperationSnapshot:
    operation_id: UUID
    workspace_id: UUID
    kind: str
    status: str
    fencing_epoch: int | None


def reader_inventory_denied(outcome: ProjectCellOperationOutcome) -> bool:
    """Recognize only our fixed classification after normal error redaction."""
    return (
        outcome.status == "indeterminate"
        and outcome.error == _hashed_error("reader_inventory_unavailable")
    )


async def execute_cell_operation(
    session_factory: async_sessionmaker[AsyncSession],
    operation_id: UUID,
    client: ProjectCellOrchestratorClient,
    *,
    expected_workspace_fence: int | None = None,
) -> ProjectCellOperationOutcome:
    replay = await _maybe_replayable_outcome(session_factory, operation_id)
    if replay is not None:
        return replay

    initial = claim_cell_operation_committed
    claim = initial if expected_workspace_fence is None else partial(
        initial, expected_workspace_fence=expected_workspace_fence,
    )
    try:
        claimed = await _claim_for_dispatch_cancellation_safe(
            session_factory,
            operation_id,
            claim,
            cancellation_error="cancelled_after_claim",
        )
    except (ProjectCellBusy, ProjectCellStateConflict):
        return await _load_operation_outcome(session_factory, operation_id)

    return await _dispatch_claimed_cell_operation(session_factory, claimed, client)


async def replay_indeterminate_cell_operation(
    session_factory: async_sessionmaker[AsyncSession],
    operation_id: UUID,
    client: ProjectCellOrchestratorClient,
    *,
    expected_workspace_fence: int | None = None,
) -> ProjectCellOperationOutcome:
    """Redrive the exact durable envelope so provider idempotency proves its effect."""

    reclaim = reclaim_indeterminate_cell_operation_committed
    claim = reclaim if expected_workspace_fence is None else partial(
        reclaim, expected_workspace_fence=expected_workspace_fence,
    )
    try:
        claimed = await _claim_for_dispatch_cancellation_safe(
            session_factory,
            operation_id,
            claim,
            cancellation_error="cancelled_after_reclaim",
        )
    except (ProjectCellBusy, ProjectCellStateConflict):
        return await _load_operation_outcome(session_factory, operation_id)
    return await _dispatch_claimed_cell_operation(
        session_factory,
        claimed,
        client,
        replaying_indeterminate=True,
    )


async def _dispatch_claimed_cell_operation(
    session_factory: async_sessionmaker[AsyncSession],
    claimed: ClaimedCellOperation,
    client: ProjectCellOrchestratorClient,
    *,
    replaying_indeterminate: bool = False,
) -> ProjectCellOperationOutcome:

    try:
        method_name, request = _build_client_call(claimed)
    except (ProjectCellValidationError, ValueError) as exc:
        return await _persist_failed_outcome(
            session_factory,
            claimed.operation_id,
            f"local_validation:{exc}",
        )

    try:
        response = await _invoke_client(client, method_name, request)
    except ProjectCellCapacityWait as exc:
        return await _persist_capacity_wait(
            session_factory,
            claimed,
            exc,
        )
    except OrchestratorBadRequest as exc:
        reason = _strict_release_rejection(claimed, exc)
        if reason is not None:
            # The orchestration claim advanced the SQL fence. Only an exact
            # replay may use that fence, even when the last call was pre-effect.
            return await _persist_indeterminate_outcome(
                session_factory, claimed.operation_id, reason,
            )
        if _is_confirmed_pre_effect_rejection(claimed, exc):
            if replaying_indeterminate:
                return await _persist_indeterminate_outcome(
                    session_factory,
                    claimed.operation_id,
                    f"replay_rejected_pre_effect:{exc.status_code}",
                )
            return await _persist_failed_outcome(
                session_factory,
                claimed.operation_id,
                f"orchestrator_rejected:{exc.status_code}",
            )
        return await _persist_indeterminate_outcome(
            session_factory,
            claimed.operation_id,
            f"unconfirmed_rejection:{exc.status_code}",
        )
    except asyncio.CancelledError:
        await _persist_indeterminate_outcome(
            session_factory,
            claimed.operation_id,
            "cancelled_after_dispatch",
        )
        raise
    except OrchestratorReaderInventoryUnavailable:
        # This is not a pre-effect receipt. Preserve uncertainty while letting
        # the release owner redrive the exact durable envelope after readiness.
        return await _persist_indeterminate_outcome(
            session_factory, claimed.operation_id, "reader_inventory_unavailable",
        )
    except (OrchestratorUnavailable, httpx.RequestError) as exc:
        return await _persist_indeterminate_outcome(
            session_factory,
            claimed.operation_id,
            exc.__class__.__name__,
        )
    except Exception as exc:
        return await _persist_indeterminate_outcome(
            session_factory,
            claimed.operation_id,
            exc.__class__.__name__,
        )
    if not isinstance(response, ProjectCellResourceResponse):
        return await _persist_indeterminate_outcome(
            session_factory,
            claimed.operation_id,
            "invalid_response_object",
        )
    try:
        _validate_response_identity(claimed, response)
    except ProjectCellValidationError as exc:
        return await _persist_indeterminate_outcome(
            session_factory,
            claimed.operation_id,
            f"response_identity_mismatch:{exc}",
        )

    return await _persist_completed_outcome(
        session_factory,
        claimed.operation_id,
        response,
    )


async def reconcile_indeterminate_cell_operation(
    session_factory: async_sessionmaker[AsyncSession],
    indeterminate_operation_id: UUID,
    reconcile_operation_id: UUID,
    client: ProjectCellOrchestratorClient,
) -> ProjectCellOperationOutcome:
    replay = await _maybe_replayable_outcome(session_factory, reconcile_operation_id)
    if replay is not None:
        return replay

    try:
        claimed = await _claim_for_dispatch_cancellation_safe(
            session_factory,
            reconcile_operation_id,
            claim_cell_operation_committed,
            cancellation_error="cancelled_after_reconcile_claim",
        )
    except ProjectCellStateConflict:
        return await _load_operation_outcome(session_factory, reconcile_operation_id)

    try:
        target = await _load_operation_snapshot(session_factory, indeterminate_operation_id)
        _validate_reconcile_preconditions(
            indeterminate_operation_id=indeterminate_operation_id,
            target=target,
            claimed=claimed,
        )
        request = ObserveProjectCellResourcesRequest(
            workspace_id=claimed.workspace_id,
            operation_id=claimed.operation_id,
            fencing_epoch=claimed.fencing_epoch,
            request_digest=claimed.request_digest,
        )
    except (
        ProjectCellNotFound,
        ProjectCellStateConflict,
        ProjectCellValidationError,
        ValueError,
    ) as exc:
        return await _persist_failed_outcome(
            session_factory,
            claimed.operation_id,
            f"reconcile_precondition:{exc}",
        )

    try:
        response = await client.observe_resources(request)
    except OrchestratorBadRequest as exc:
        if _is_confirmed_pre_effect_rejection(claimed, exc):
            return await _persist_failed_outcome(
                session_factory,
                claimed.operation_id,
                f"orchestrator_rejected:{exc.status_code}",
            )
        return await _persist_indeterminate_outcome(
            session_factory,
            claimed.operation_id,
            f"unconfirmed_rejection:{exc.status_code}",
        )
    except asyncio.CancelledError:
        await _persist_indeterminate_outcome(
            session_factory,
            claimed.operation_id,
            "cancelled_after_dispatch",
        )
        raise
    except (OrchestratorUnavailable, httpx.RequestError) as exc:
        return await _persist_indeterminate_outcome(
            session_factory,
            claimed.operation_id,
            exc.__class__.__name__,
        )
    except Exception as exc:
        return await _persist_indeterminate_outcome(
            session_factory,
            claimed.operation_id,
            exc.__class__.__name__,
        )
    if not isinstance(response, ProjectCellResourceResponse):
        return await _persist_indeterminate_outcome(
            session_factory,
            claimed.operation_id,
            "invalid_response_object",
        )
    try:
        _validate_response_identity(claimed, response)
    except ProjectCellValidationError as exc:
        return await _persist_indeterminate_outcome(
            session_factory,
            claimed.operation_id,
            f"response_identity_mismatch:{exc}",
        )

    return await _persist_completed_outcome(
        session_factory,
        claimed.operation_id,
        response,
        reconciles_operation_id=indeterminate_operation_id,
    )


def _build_client_call(
    claimed: ClaimedCellOperation,
) -> tuple[str, object]:
    if claimed.kind == "ensure":
        return "ensure", _build_ensure_request(claimed)
    if claimed.kind == "status":
        _require_exact_request(claimed.request, set())
        return (
            "observe_resources",
            ObserveProjectCellResourcesRequest(
                workspace_id=claimed.workspace_id,
                operation_id=claimed.operation_id,
                fencing_epoch=claimed.fencing_epoch,
                request_digest=claimed.request_digest,
            ),
        )
    if claimed.kind in _STATEFUL_CONTROL_KINDS:
        return "control", _build_control_request(claimed)
    raise ProjectCellValidationError(f"unsupported lifecycle dispatch kind {claimed.kind!r}")


def _build_ensure_request(
    claimed: ClaimedCellOperation,
) -> EnsureProjectCellResourcesRequest:
    _require_exact_request(claimed.request, {"profile_version"})
    profile_version = claimed.request.get("profile_version")
    if type(profile_version) is not str or not profile_version:
        raise ProjectCellValidationError("ensure request requires non-empty profile_version")
    if claimed.generation_run_id is None:
        raise ProjectCellValidationError("ensure operation requires generation_run_id")
    return EnsureProjectCellResourcesRequest(
        workspace_id=claimed.workspace_id,
        project_id=claimed.project_id,
        owner_id=claimed.owner_id,
        generation_run_id=claimed.generation_run_id,
        profile_version=profile_version,
        operation_id=claimed.operation_id,
        fencing_epoch=claimed.fencing_epoch,
        request_digest=claimed.request_digest,
    )


def _build_control_request(
    claimed: ClaimedCellOperation,
) -> ControlProjectCellResourcesRequest:
    checkpoint_ref: str | None = None
    release_proof: ProjectCellReleaseEnvironmentBinding | None = None
    if claimed.kind in _CHECKPOINT_KINDS:
        _require_exact_request(claimed.request, {"checkpoint_ref"})
        raw_checkpoint_ref = claimed.request.get("checkpoint_ref")
        if type(raw_checkpoint_ref) is not str or not raw_checkpoint_ref:
            raise ProjectCellValidationError(
                f"{claimed.kind!r} request requires non-empty checkpoint_ref"
            )
        checkpoint_ref = raw_checkpoint_ref
    elif claimed.kind == "release" and "require_environment_proof" in claimed.request:
        _require_exact_request(claimed.request, {"require_environment_proof"})
        release_proof = ProjectCellReleaseEnvironmentBinding.model_validate(
            claimed.request["require_environment_proof"],
        )
    else:
        _require_exact_request(claimed.request, set())

    return ControlProjectCellResourcesRequest(
        workspace_id=claimed.workspace_id,
        kind=claimed.kind,
        checkpoint_ref=checkpoint_ref,
        operation_id=claimed.operation_id,
        fencing_epoch=claimed.fencing_epoch,
        request_digest=claimed.request_digest,
        require_environment_proof=release_proof,
    )


def _validate_reconcile_preconditions(
    *,
    indeterminate_operation_id: UUID,
    target: _OperationSnapshot,
    claimed: ClaimedCellOperation,
) -> None:
    if claimed.kind != "reconcile":
        raise ProjectCellValidationError("reconcile executor requires a reconcile operation")
    if target.status != "indeterminate":
        raise ProjectCellStateConflict(f"cannot reconcile operation in state {target.status!r}")
    if target.workspace_id != claimed.workspace_id:
        raise ProjectCellStateConflict("reconcile operation must target the same workspace")
    if target.fencing_epoch is None or claimed.fencing_epoch <= target.fencing_epoch:
        raise ProjectCellStateConflict("reconcile operation must claim a higher fencing epoch")

    _require_exact_request(claimed.request, {"indeterminate_operation_id"})
    raw_target_id = claimed.request.get("indeterminate_operation_id")
    if type(raw_target_id) is not str or UUID(raw_target_id) != indeterminate_operation_id:
        raise ProjectCellValidationError(
            "reconcile request must match the targeted indeterminate operation id"
        )


def _require_exact_request(
    request: dict[str, object],
    expected_keys: set[str],
) -> None:
    if set(request) != expected_keys:
        raise ProjectCellValidationError(
            f"request payload keys must be exactly {sorted(expected_keys)!r}"
        )


def _is_confirmed_pre_effect_rejection(
    claimed: ClaimedCellOperation,
    exc: OrchestratorBadRequest,
) -> bool:
    try:
        rejection = ProjectCellPreEffectRejection.from_json(exc.details)
    except ValueError:
        return False
    return (
        rejection.operation_id == claimed.operation_id
        and rejection.fencing_epoch == claimed.fencing_epoch
        and rejection.request_digest == claimed.request_digest
    )


def _strict_release_rejection(
    claimed: ClaimedCellOperation, exc: OrchestratorBadRequest,
) -> str | None:
    if (claimed.kind != "release" or "require_environment_proof" not in claimed.request
            or exc.status_code != 409 or exc.upstream_code != "conflict"
            or not isinstance(exc.details, dict)):
        return None
    reason = exc.details.get("reason")
    if (not isinstance(reason, str) or reason not in RELEASE_PROOF_REJECTIONS
            or exc.details.get("operation_id") != str(claimed.operation_id)
            or type(exc.details.get("fencing_epoch")) is not int
            or exc.details["fencing_epoch"] != claimed.fencing_epoch
            or exc.details.get("request_digest") != claimed.request_digest):
        return None
    # Without a definite pre-effect rejection the same reason could come after
    # an interrupted capture. Preserve generic uncertainty for that envelope.
    if (reason.endswith("_changed") and exc.details.get("effect_applied") is not False):
        return None
    return reason


def _validate_response_identity(
    claimed: ClaimedCellOperation,
    response: ProjectCellResourceResponse,
) -> None:
    if response.workspace_id != claimed.workspace_id:
        raise ProjectCellValidationError("response workspace_id does not match claimed workspace")
    if response.fencing_epoch != claimed.fencing_epoch:
        raise ProjectCellValidationError("response fencing_epoch does not match claimed fence")
    observation = response.lease_observation
    if observation is not None and (
        observation.workspace_id != claimed.workspace_id
        or observation.project_id != claimed.project_id
        or observation.owner_id != claimed.owner_id
        or observation.operation_id != claimed.operation_id
        or observation.fencing_epoch != claimed.fencing_epoch
        or observation.request_digest != claimed.request_digest
    ):
        raise ProjectCellValidationError("lease observation identity mismatch")
    if claimed.kind in _CHECKPOINT_KINDS:
        expected_checkpoint_ref = claimed.request.get("checkpoint_ref")
        if (
            type(expected_checkpoint_ref) is not str
            or response.checkpoint_ref != expected_checkpoint_ref
        ):
            raise ProjectCellValidationError(
                "response checkpoint_ref does not match claimed request"
            )


async def _invoke_client(
    client: ProjectCellOrchestratorClient,
    method_name: str,
    request: object,
) -> ProjectCellResourceResponse:
    if method_name == "ensure":
        return await client.ensure(cast(EnsureProjectCellResourcesRequest, request))
    if method_name == "control":
        return await client.control(cast(ControlProjectCellResourcesRequest, request))
    if method_name == "observe_resources":
        return await client.observe_resources(cast(ObserveProjectCellResourcesRequest, request))
    raise ProjectCellValidationError(f"unsupported Project Cell client method {method_name!r}")


async def _maybe_replayable_outcome(
    session_factory: async_sessionmaker[AsyncSession],
    operation_id: UUID,
) -> ProjectCellOperationOutcome | None:
    outcome = await _load_operation_outcome(session_factory, operation_id)
    if outcome.status in {"pending", "waiting_capacity"}:
        return None
    return outcome


async def _load_operation_snapshot(
    session_factory: async_sessionmaker[AsyncSession],
    operation_id: UUID,
) -> _OperationSnapshot:
    async with session_factory() as session:
        operation = await session.get(ProjectCellOperation, operation_id)
        if operation is None:
            raise ProjectCellNotFound("Project Cell operation was not found")
        return _OperationSnapshot(
            operation_id=operation.id,
            workspace_id=operation.workspace_id,
            kind=operation.kind,
            status=operation.status,
            fencing_epoch=operation.fencing_epoch,
        )


async def _load_operation_outcome(
    session_factory: async_sessionmaker[AsyncSession],
    operation_id: UUID,
) -> ProjectCellOperationOutcome:
    async with session_factory() as session:
        operation = await session.get(ProjectCellOperation, operation_id)
        if operation is None:
            raise ProjectCellNotFound("Project Cell operation was not found")

        result_payload = _result_payload_dict(operation)
        response = (
            ProjectCellResourceResponse.from_json(result_payload, allow_extra=True)
            if result_payload is not None
            else None
        )
        reconciles_operation_id = _reconciles_operation_id(result_payload)
        return ProjectCellOperationOutcome(
            operation_id=operation.id,
            workspace_id=operation.workspace_id,
            kind=operation.kind,
            status=operation.status,
            fencing_epoch=operation.fencing_epoch,
            response=response,
            result_payload=result_payload,
            error=operation.error,
            reconciles_operation_id=reconciles_operation_id,
        )


def _result_payload_dict(operation: ProjectCellOperation) -> dict[str, object] | None:
    payload = operation.result_payload
    if payload is None:
        return None
    if type(payload) is not dict:
        raise ProjectCellValidationError("stored operation result payload must be a JSON object")
    normalized: dict[str, object] = {}
    for key, value in payload.items():
        if type(key) is not str:
            raise ProjectCellValidationError("stored operation result payload keys must be strings")
        normalized[key] = value
    return normalized


def _reconciles_operation_id(result_payload: dict[str, object] | None) -> UUID | None:
    if result_payload is None:
        return None
    raw_value = result_payload.get("reconciles_operation_id")
    if raw_value is None:
        return None
    if type(raw_value) is not str:
        raise ProjectCellValidationError("reconciles_operation_id must be a UUID string")
    return UUID(raw_value)


async def _persist_completed_outcome(
    session_factory: async_sessionmaker[AsyncSession],
    operation_id: UUID,
    response: ProjectCellResourceResponse,
    *,
    reconciles_operation_id: UUID | None = None,
) -> ProjectCellOperationOutcome:
    result_payload = response.to_wire_json()
    if reconciles_operation_id is not None:
        result_payload["reconciles_operation_id"] = str(reconciles_operation_id)
    return await _persist_terminal_with_fallback(
        session_factory,
        operation_id,
        lambda session: complete_cell_operation(session, operation_id, result_payload),
        "terminal_commit_failed:completed",
    )


async def _persist_failed_outcome(
    session_factory: async_sessionmaker[AsyncSession],
    operation_id: UUID,
    error: str,
) -> ProjectCellOperationOutcome:
    return await _persist_terminal_with_fallback(
        session_factory,
        operation_id,
        lambda session: fail_cell_operation(session, operation_id, error),
        "terminal_commit_failed:failed",
    )


async def _persist_capacity_wait(
    session_factory: async_sessionmaker[AsyncSession],
    claimed: ClaimedCellOperation,
    exc: ProjectCellCapacityWait,
) -> ProjectCellOperationOutcome:
    rejection = exc.rejection
    if (
        rejection.operation_id != claimed.operation_id
        or rejection.fencing_epoch != claimed.fencing_epoch
        or rejection.request_digest != claimed.request_digest
    ):
        return await _persist_indeterminate_outcome(
            session_factory,
            claimed.operation_id,
            "capacity_identity_mismatch",
        )
    try:
        async with session_factory() as session:
            await park_cell_operation_for_capacity(
                session,
                claimed.operation_id,
                reason=rejection.reason,
                retry_after_seconds=rejection.retry_after_seconds,
            )
            await session.commit()
    except asyncio.CancelledError:
        await _ensure_capacity_wait_persisted_or_indeterminate(
            session_factory,
            claimed.operation_id,
            "capacity_wait_commit_cancelled",
        )
        raise
    except Exception:
        await _ensure_capacity_wait_persisted_or_indeterminate(
            session_factory,
            claimed.operation_id,
            "capacity_wait_commit_failed",
        )
    return await _load_operation_outcome(session_factory, claimed.operation_id)


async def _claim_for_dispatch_cancellation_safe(
    session_factory: async_sessionmaker[AsyncSession],
    operation_id: UUID,
    claim: Callable[
        [async_sessionmaker[AsyncSession], UUID],
        Awaitable[ClaimedCellOperation],
    ],
    *,
    cancellation_error: str,
) -> ClaimedCellOperation:
    """Finish our claim before classifying cancellation; never infer foreign ownership."""

    async def _claim() -> ClaimedCellOperation:
        return await claim(session_factory, operation_id)

    claim_task: asyncio.Task[ClaimedCellOperation] = asyncio.create_task(_claim())
    try:
        return await asyncio.shield(claim_task)
    except asyncio.CancelledError as cancelled:
        try:
            claimed = await asyncio.shield(claim_task)
        except (asyncio.CancelledError, Exception):
            # Without our own claim receipt, a concurrent worker may own `running`.
            raise cancelled from None
        await _ensure_running_operation_indeterminate(
            session_factory,
            claimed.operation_id,
            cancellation_error,
        )
        raise cancelled


async def _persist_indeterminate_outcome(
    session_factory: async_sessionmaker[AsyncSession],
    operation_id: UUID,
    error: str,
) -> ProjectCellOperationOutcome:
    return await _persist_terminal_with_fallback(
        session_factory,
        operation_id,
        lambda session: mark_cell_operation_indeterminate(session, operation_id, error),
        "terminal_commit_failed:indeterminate",
    )


async def _persist_terminal_with_fallback(
    session_factory: async_sessionmaker[AsyncSession],
    operation_id: UUID,
    persist: Callable[[AsyncSession], Awaitable[None]],
    fallback_error: str,
) -> ProjectCellOperationOutcome:
    try:
        async with session_factory() as session:
            await persist(session)
            await session.commit()
    except asyncio.CancelledError:
        await _ensure_running_operation_indeterminate(
            session_factory,
            operation_id,
            fallback_error,
        )
        raise
    except Exception:
        await _ensure_running_operation_indeterminate(
            session_factory,
            operation_id,
            fallback_error,
        )
    return await _load_operation_outcome(session_factory, operation_id)


async def _ensure_running_operation_indeterminate(
    session_factory: async_sessionmaker[AsyncSession],
    operation_id: UUID,
    error: str,
) -> None:
    async with session_factory() as session:
        operation = await session.scalar(
            select(ProjectCellOperation)
            .where(ProjectCellOperation.id == operation_id)
            .with_for_update()
        )
        if operation is None:
            raise ProjectCellNotFound("Project Cell operation was not found")
        if operation.status == "running":
            await mark_cell_operation_indeterminate(session, operation_id, error)
            await session.commit()
            return
        if operation.status == "indeterminate" or operation.status in TERMINAL_OPERATION_STATUSES:
            await session.rollback()
            return
        raise ProjectCellStateConflict(
            f"cannot persist terminal fallback from state {operation.status!r}"
        )


async def _ensure_capacity_wait_persisted_or_indeterminate(
    session_factory: async_sessionmaker[AsyncSession],
    operation_id: UUID,
    error: str,
) -> None:
    async with session_factory() as session:
        operation = await session.scalar(
            select(ProjectCellOperation)
            .where(ProjectCellOperation.id == operation_id)
            .with_for_update()
        )
        if operation is None:
            raise ProjectCellNotFound("Project Cell operation was not found")
        if operation.status == "running":
            await mark_cell_operation_indeterminate(session, operation_id, error)
            await session.commit()
            return
        if (
            operation.status in {"waiting_capacity", "indeterminate"}
            or operation.status in TERMINAL_OPERATION_STATUSES
        ):
            await session.rollback()
            return
        raise ProjectCellStateConflict(
            f"cannot persist capacity fallback from state {operation.status!r}"
        )
