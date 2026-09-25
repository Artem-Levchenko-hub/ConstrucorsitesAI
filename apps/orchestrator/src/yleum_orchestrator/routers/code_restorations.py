"""Internal restoration transport. Public owner authorization remains in apps/api."""

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Header

from yleum_orchestrator.core.cell_resources import CellResourceError
from yleum_orchestrator.core.errors import OrchestratorError
from yleum_orchestrator.core.internal_auth import verify_internal_token
from yleum_orchestrator.schemas.code_restoration import (
    CodeRestorationApply,
    CodeRestorationCancel,
    CodeRestorationPrepare,
)
from yleum_orchestrator.schemas.restoration_adaptation import (
    RestorationAdaptationCleanup,
    RestorationAdaptationOwnerStatus,
    RestorationAdaptationPrepare,
    RestorationAdaptationProof,
    RestorationAdaptationProofRequest,
    RestorationAdaptationWorkspace,
)
from yleum_orchestrator.schemas.restoration_adaptation_activation import (
    RestorationAdaptationActivationCommand,
    RestorationAdaptationActivationOffer,
    RestorationAdaptationActivationOfferRequest,
    RestorationAdaptationActivationStatus,
)
from yleum_orchestrator.services.code_restorations import get_code_restoration_service
from yleum_orchestrator.services.restoration_adaptation_activation_service import (
    get_restoration_adaptation_activation_service,
)
from yleum_orchestrator.services.restoration_adaptation_workspace import (
    get_restoration_adaptation_workspace_service,
)

router = APIRouter(prefix="/internal/workspaces", tags=["code-restorations"])


def _identity(workspace: UUID, operation: UUID, body: Any) -> None:
    if body.workspace_id != workspace or body.operation_id != operation:
        raise OrchestratorError(
            code="conflict", message="restoration identity mismatch", status_code=409
        )


def _conflict() -> OrchestratorError:
    return OrchestratorError(
        code="conflict",
        status_code=409,
        message="Состояние восстановления изменилось. Обновите статус.",
    )


@router.post("/{workspace_id}/code-restorations/{operation_id}/prepare")
async def prepare(
    workspace_id: UUID,
    operation_id: UUID,
    body: CodeRestorationPrepare,
    x_internal_token: Annotated[str | None, Header()] = None,
) -> dict[str, Any]:
    verify_internal_token(x_internal_token)
    _identity(workspace_id, operation_id, body)
    try:
        return await get_code_restoration_service().prepare(body)
    except CellResourceError:
        raise _conflict() from None


@router.post("/{workspace_id}/code-restorations/{operation_id}/apply")
async def apply(
    workspace_id: UUID,
    operation_id: UUID,
    body: CodeRestorationApply,
    x_internal_token: Annotated[str | None, Header()] = None,
) -> dict[str, Any]:
    verify_internal_token(x_internal_token)
    _identity(workspace_id, operation_id, body)
    try:
        return await get_code_restoration_service().apply(body)
    except CellResourceError:
        raise _conflict() from None


@router.post("/{workspace_id}/code-restorations/{operation_id}/cancel")
async def cancel(
    workspace_id: UUID,
    operation_id: UUID,
    body: CodeRestorationCancel,
    x_internal_token: Annotated[str | None, Header()] = None,
) -> dict[str, Any]:
    verify_internal_token(x_internal_token)
    _identity(workspace_id, operation_id, body)
    try:
        return await get_code_restoration_service().cancel(body)
    except CellResourceError:
        raise _conflict() from None


@router.get("/{workspace_id}/code-restorations/{operation_id}")
async def get(
    workspace_id: UUID,
    operation_id: UUID,
    project_id: UUID,
    owner_id: UUID,
    x_internal_token: Annotated[str | None, Header()] = None,
) -> dict[str, Any]:
    verify_internal_token(x_internal_token)
    try:
        return await get_code_restoration_service().get(
            workspace_id, operation_id, project_id, owner_id
        )
    except LookupError:
        raise OrchestratorError(
            code="not_found", message="restoration not found", status_code=404
        ) from None
    except CellResourceError:
        raise _conflict() from None


@router.post(
    "/{workspace_id}/restoration-adaptations/{generation_run_id}/prepare",
    response_model=RestorationAdaptationWorkspace,
)
async def prepare_restoration_adaptation(
    workspace_id: UUID,
    generation_run_id: UUID,
    body: RestorationAdaptationPrepare,
    x_internal_token: Annotated[str | None, Header()] = None,
) -> RestorationAdaptationWorkspace:
    verify_internal_token(x_internal_token)
    if body.workspace_id != workspace_id or body.generation_run_id != generation_run_id:
        raise _conflict()
    try:
        return await get_restoration_adaptation_workspace_service().prepare(body)
    except CellResourceError:
        raise _conflict() from None


@router.post(
    "/{workspace_id}/restoration-adaptations/{generation_run_id}/cleanup",
)
async def cleanup_restoration_adaptation(
    workspace_id: UUID,
    generation_run_id: UUID,
    body: RestorationAdaptationCleanup,
    x_internal_token: Annotated[str | None, Header()] = None,
) -> dict[str, str]:
    verify_internal_token(x_internal_token)
    if body.workspace_id != workspace_id or body.generation_run_id != generation_run_id:
        raise _conflict()
    try:
        await get_restoration_adaptation_workspace_service().cleanup(body)
    except CellResourceError:
        raise _conflict() from None
    return {"state": "cleaned"}


@router.post(
    "/{workspace_id}/restoration-adaptations/{generation_run_id}/proof",
    response_model=RestorationAdaptationProof,
)
async def prove_restoration_adaptation(
    workspace_id: UUID,
    generation_run_id: UUID,
    body: RestorationAdaptationProofRequest,
    x_internal_token: Annotated[str | None, Header()] = None,
) -> RestorationAdaptationProof:
    verify_internal_token(x_internal_token)
    if body.workspace_id != workspace_id or body.generation_run_id != generation_run_id:
        raise _conflict()
    try:
        return await get_restoration_adaptation_workspace_service().prove(body)
    except CellResourceError:
        raise _conflict() from None


@router.post(
    "/{workspace_id}/restoration-adaptations/{generation_run_id}/owner-status",
)
async def update_restoration_adaptation_owner_status(
    workspace_id: UUID,
    generation_run_id: UUID,
    body: RestorationAdaptationOwnerStatus,
    x_internal_token: Annotated[str | None, Header()] = None,
) -> dict[str, str]:
    verify_internal_token(x_internal_token)
    if body.workspace_id != workspace_id or body.generation_run_id != generation_run_id:
        raise _conflict()
    service = get_restoration_adaptation_workspace_service()
    try:
        await service.record_owner_status(body)
        if body.state == "terminal":
            await service.recover()
    except CellResourceError:
        raise _conflict() from None
    return {"state": body.state}


@router.post(
    "/{workspace_id}/restoration-adaptations/{generation_run_id}/activation-offer",
    response_model=RestorationAdaptationActivationOffer,
)
async def offer_restoration_adaptation_activation(
    workspace_id: UUID,
    generation_run_id: UUID,
    body: RestorationAdaptationActivationOfferRequest,
    x_internal_token: Annotated[str | None, Header()] = None,
) -> RestorationAdaptationActivationOffer:
    verify_internal_token(x_internal_token)
    if body.workspace_id != workspace_id or body.generation_run_id != generation_run_id:
        raise _conflict()
    try:
        return await get_restoration_adaptation_activation_service().offer(body)
    except CellResourceError:
        raise _conflict() from None


def _activation_command_identity(
    workspace_id: UUID,
    generation_run_id: UUID,
    activation_id: UUID,
    body: RestorationAdaptationActivationCommand,
) -> None:
    offer = body.offer
    if (
        offer.workspace_id != workspace_id
        or offer.generation_run_id != generation_run_id
        or offer.activation_id != activation_id
    ):
        raise _conflict()


@router.post(
    "/{workspace_id}/restoration-adaptations/{generation_run_id}/activations/{activation_id}/apply",
    response_model=RestorationAdaptationActivationStatus,
)
async def apply_restoration_adaptation_activation(
    workspace_id: UUID,
    generation_run_id: UUID,
    activation_id: UUID,
    body: RestorationAdaptationActivationCommand,
    x_internal_token: Annotated[str | None, Header()] = None,
) -> RestorationAdaptationActivationStatus:
    verify_internal_token(x_internal_token)
    _activation_command_identity(workspace_id, generation_run_id, activation_id, body)
    try:
        return await get_restoration_adaptation_activation_service().apply(body)
    except CellResourceError:
        raise _conflict() from None


@router.post(
    "/{workspace_id}/restoration-adaptations/{generation_run_id}/activations/"
    "{activation_id}/status",
    response_model=RestorationAdaptationActivationStatus,
)
async def status_restoration_adaptation_activation(
    workspace_id: UUID,
    generation_run_id: UUID,
    activation_id: UUID,
    body: RestorationAdaptationActivationCommand,
    x_internal_token: Annotated[str | None, Header()] = None,
) -> RestorationAdaptationActivationStatus:
    verify_internal_token(x_internal_token)
    _activation_command_identity(workspace_id, generation_run_id, activation_id, body)
    try:
        return await get_restoration_adaptation_activation_service().status(body)
    except CellResourceError:
        raise _conflict() from None


@router.post(
    "/{workspace_id}/restoration-adaptations/{generation_run_id}/activations/"
    "{activation_id}/cancel",
    response_model=RestorationAdaptationActivationStatus,
)
async def cancel_restoration_adaptation_activation(
    workspace_id: UUID,
    generation_run_id: UUID,
    activation_id: UUID,
    body: RestorationAdaptationActivationCommand,
    x_internal_token: Annotated[str | None, Header()] = None,
) -> RestorationAdaptationActivationStatus:
    verify_internal_token(x_internal_token)
    _activation_command_identity(workspace_id, generation_run_id, activation_id, body)
    try:
        return await get_restoration_adaptation_activation_service().cancel(body)
    except CellResourceError:
        raise _conflict() from None
