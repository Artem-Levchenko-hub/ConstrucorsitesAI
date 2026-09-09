"""Internal restoration transport. Public owner authorization remains in apps/api."""

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Header

from omnia_orchestrator.core.cell_resources import CellResourceError
from omnia_orchestrator.core.errors import OrchestratorError
from omnia_orchestrator.core.internal_auth import verify_internal_token
from omnia_orchestrator.schemas.code_restoration import (
    CodeRestorationApply,
    CodeRestorationCancel,
    CodeRestorationPrepare,
)
from omnia_orchestrator.services.code_restorations import get_code_restoration_service

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
