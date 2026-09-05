"""Internal public release commands. Owner authorization remains in apps/api."""

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Header
from pydantic import BaseModel, ConfigDict, Field, field_validator

from omnia_orchestrator.core.cell_resources import (
    CellCapacityUnavailable,
    CellIdentityConflict,
    CellResourceError,
)
from omnia_orchestrator.core.errors import OrchestratorError
from omnia_orchestrator.core.internal_auth import verify_internal_token
from omnia_orchestrator.schemas.cell_publication import CellDeployRequest
from omnia_orchestrator.schemas.runtime import DeployResponse
from omnia_orchestrator.services.cell_publication import get_cell_publication_service

router = APIRouter(prefix="/internal/projects", tags=["cell-publication"])


class PublicCellConfigRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    owner_id: UUID
    runtime_env: dict[str, str] | None = Field(default=None, repr=False)
    business_config: dict[str, Any] | None = Field(default=None, repr=False)
    business_config_version: int | None = Field(default=None, gt=0)

    @field_validator("runtime_env")
    @classmethod
    def safe_env(cls, value: dict[str, str] | None) -> dict[str, str] | None:
        return CellDeployRequest.validate_runtime_env(value) if value is not None else None


def _safe_error(exc: CellResourceError) -> OrchestratorError:
    if isinstance(exc, CellCapacityUnavailable):
        return OrchestratorError(
            code="conflict", message="Недостаточно ресурсов для публикации", status_code=409
        )
    if isinstance(exc, CellIdentityConflict):
        return OrchestratorError(
            code="conflict",
            message="Состояние публикации изменилось. Обновите страницу и повторите действие",
            status_code=409,
        )
    return OrchestratorError(
        code="conflict", message="Публикация пока не готова к этому действию", status_code=409
    )


@router.post("/{project_id}/cell-deploy", response_model=DeployResponse)
async def publish_cell(
    project_id: UUID,
    body: CellDeployRequest,
    x_internal_token: Annotated[str | None, Header()] = None,
) -> DeployResponse:
    verify_internal_token(x_internal_token)
    if body.project_id != project_id:
        raise OrchestratorError(
            code="conflict", message="publication project mismatch", status_code=409
        )
    try:
        return await get_cell_publication_service().submit(body)
    except CellResourceError as exc:
        raise _safe_error(exc) from None


@router.put("/{project_id}/cell-deploy/config")
async def configure_cell(
    project_id: UUID,
    body: PublicCellConfigRequest,
    x_internal_token: Annotated[str | None, Header()] = None,
) -> dict[str, bool]:
    verify_internal_token(x_internal_token)
    if (body.business_config is None) != (body.business_config_version is None):
        raise OrchestratorError(
            code="validation_failed", message="configuration version required", status_code=400
        )
    try:
        return await get_cell_publication_service().configure(
            project_id,
            body.owner_id,
            runtime_env=body.runtime_env,
            business_config=body.business_config,
            business_config_version=body.business_config_version,
        )
    except CellResourceError as exc:
        raise _safe_error(exc) from None
