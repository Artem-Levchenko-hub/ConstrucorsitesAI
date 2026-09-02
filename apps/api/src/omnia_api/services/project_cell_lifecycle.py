from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import cast
from uuid import UUID

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from omnia_api.models.project_cell import ProjectCellOperation
from omnia_api.services.orchestrator_client import (
    ControlProjectCellResourcesRequest,
    EnsureProjectCellResourcesRequest,
    ObserveProjectCellResourcesRequest,
    OrchestratorBadRequest,
    OrchestratorUnavailable,
    ProjectCellCapacityWait,
    ProjectCellOrchestratorClient,
    ProjectCellPreEffectRejection,
    ProjectCellResourceResponse,
)
from omnia_api.services.project_cells import (
    TERMINAL_OPERATION_STATUSES,
    ClaimedCellOperation,
    ProjectCellNotFound,
    ProjectCellStateConflict,
    ProjectCellValidationError,
    claim_cell_operation_committed,
    complete_cell_operation,
    fail_cell_operation,
    mark_cell_operation_indeterminate,
    park_cell_operation_for_capacity,
)

_CHECKPOINT_KINDS = frozenset({"pause", "stop", "restore"})
_STATEFUL_CONTROL_KINDS = frozenset(
    {"wake", "pause", "stop", "destroy", "restore", "release"}
)


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


@dataclass(frozen=True, slots=True)
class _OperationSnapshot:
    operation_id: UUID
    workspace_id: UUID
    kind: str
    status: str
    fencing_epoch: int | None


async def execute_cell_operation(
    session_factory: async_sessionmaker[AsyncSession],
    operation_id: UUID,
    client: ProjectCellOrchestratorClient,
) -> ProjectCellOperationOutcome:
    replay = await _maybe_replayable_outcome(session_factory, operation_id)
    if replay is not None:
        return replay

    try:
        claimed = await claim_cell_operation_committed(session_factory, operation_id)
    except ProjectCellStateConflict:
        return await _load_operation_outcome(session_factory, operation_id)

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
        claimed = await claim_cell_operation_committed(session_factory, reconcile_operation_id)
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
    if claimed.kind in _CHECKPOINT_KINDS:
        _require_exact_request(claimed.request, {"checkpoint_ref"})
        raw_checkpoint_ref = claimed.request.get("checkpoint_ref")
        if type(raw_checkpoint_ref) is not str or not raw_checkpoint_ref:
            raise ProjectCellValidationError(
                f"{claimed.kind!r} request requires non-empty checkpoint_ref"
            )
        checkpoint_ref = raw_checkpoint_ref
    else:
        _require_exact_request(claimed.request, set())

    return ControlProjectCellResourcesRequest(
        workspace_id=claimed.workspace_id,
        kind=claimed.kind,
        checkpoint_ref=checkpoint_ref,
        operation_id=claimed.operation_id,
        fencing_epoch=claimed.fencing_epoch,
        request_digest=claimed.request_digest,
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
        raise ProjectCellStateConflict(
            f"cannot reconcile operation in state {target.status!r}"
        )
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


def _validate_response_identity(
    claimed: ClaimedCellOperation,
    response: ProjectCellResourceResponse,
) -> None:
    if response.workspace_id != claimed.workspace_id:
        raise ProjectCellValidationError("response workspace_id does not match claimed workspace")
    if response.fencing_epoch != claimed.fencing_epoch:
        raise ProjectCellValidationError("response fencing_epoch does not match claimed fence")
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
    async with session_factory() as session:
        await park_cell_operation_for_capacity(
            session,
            claimed.operation_id,
            reason=rejection.reason,
            retry_after_seconds=rejection.retry_after_seconds,
        )
        await session.commit()
    return await _load_operation_outcome(session_factory, claimed.operation_id)


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
