"""Internal public release commands. Owner authorization remains in apps/api."""

import asyncio
from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Header
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator

from yleum_orchestrator.core.cell_resources import (
    CellCapacityUnavailable,
    CellIdentityConflict,
    CellResourceError,
)
from yleum_orchestrator.core.errors import OrchestratorError
from yleum_orchestrator.core.internal_auth import verify_internal_token
from yleum_orchestrator.schemas.cell_publication import (
    BusinessConfigStatusRequest,
    CellDeployRequest,
)
from yleum_orchestrator.schemas.runtime import DeployResponse
from yleum_orchestrator.services.cell_publication import (
    CellPublicationService,
    get_cell_publication_service,
)
from yleum_orchestrator.services.cell_state import _read_plain_json_file

router = APIRouter(prefix="/internal/projects", tags=["cell-publication"])


class PublicationHistoryRequest(BaseModel):
    project_ids: set[UUID] = Field(min_length=1, max_length=750)
    since: AwareDatetime
    include_restorations: bool = False


class PublicationFact(BaseModel):
    project_id: UUID
    run_id: str
    phase: Literal["done", "failed"]
    started_at: AwareDatetime | None = None
    finished_at: AwareDatetime
    restoration_id: UUID | None = None


class PublicationHistory(BaseModel):
    facts: list[PublicationFact]
    first_success: dict[UUID, AwareDatetime]


def _publication_restoration(
    service: CellPublicationService, project_id: UUID, run_id: str,
) -> UUID | None:
    try:
        name = str(UUID(run_id))
        path = service.root / str(project_id) / "requests" / f"{name}.json"
        if not path.exists():
            return None
        request_data = _read_plain_json_file(path)
        id_ = request_data.get("restoration_operation_id")
        if request_data.get("project_id") == str(project_id) and isinstance(id_, str):
            return UUID(id_)
    except (ValueError, FileNotFoundError):
        return None
    return None


def _monitoring_history(body: PublicationHistoryRequest) -> PublicationHistory:
    service = get_cell_publication_service()
    facts: list[PublicationFact] = []
    first_success: dict[UUID, datetime] = {}
    for project_id in body.project_ids:
        for run in service.history(project_id):
            if run.phase not in ("done", "failed") or not run.run_id or not run.finished_at:
                continue
            if run.detail in ("already_current", "config_only"):
                continue
            if run.phase == "done" and not run.prod_url:
                continue
            finished = datetime.fromisoformat(run.finished_at)
            if run.phase == "done":
                first_success[project_id] = min(first_success.get(project_id, finished), finished)
            if finished >= body.since:
                facts.append(
                    PublicationFact(
                        project_id=project_id,
                        run_id=run.run_id,
                        phase=run.phase,
                        started_at=(
                            datetime.fromisoformat(run.started_at) if run.started_at else None
                        ),
                        finished_at=finished,
                        restoration_id=(
                            _publication_restoration(service, project_id, run.run_id)
                            if run.phase == "done" and body.include_restorations else None
                        ),
                    )
                )
    return PublicationHistory(facts=facts, first_success=first_success)


@router.post("/monitoring/publication-history", response_model=PublicationHistory)
async def monitoring_publication_history(
    body: PublicationHistoryRequest,
    x_internal_token: Annotated[str | None, Header()] = None,
) -> PublicationHistory:
    verify_internal_token(x_internal_token)
    return await asyncio.to_thread(_monitoring_history, body)


class PublicCellConfigRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    owner_id: UUID
    runtime_env: dict[str, str] | None = Field(default=None, repr=False)
    business_config: dict[str, Any] | None = Field(default=None, repr=False)
    business_config_version: int | None = Field(default=None, ge=0)
    delivery_start_before: AwareDatetime | None = None

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
    if body.business_config is not None:
        from yleum_orchestrator.services.app_business_config import app_config_envelope

        try:
            app_config_envelope(body.business_config, body.business_config_version or 0)
        except ValueError:
            raise OrchestratorError(
                code="validation_failed", message="invalid public configuration", status_code=413,
            ) from None
    try:
        return await get_cell_publication_service().configure(
            project_id,
            body.owner_id,
            runtime_env=body.runtime_env,
            business_config=body.business_config,
            business_config_version=body.business_config_version,
            delivery_start_before=body.delivery_start_before,
        )
    except CellResourceError as exc:
        raise _safe_error(exc) from None


@router.post("/{project_id}/cell-deploy/config/status")
async def public_cell_config_status(
    project_id: UUID, body: BusinessConfigStatusRequest,
    x_internal_token: Annotated[str | None, Header()] = None,
) -> dict[str, Any]:
    verify_internal_token(x_internal_token)
    try:
        return await get_cell_publication_service().business_config_status(
            project_id, body.owner_id, body.desired_config_version, body.desired_config_digest,
        )
    except CellResourceError as exc:
        raise _safe_error(exc) from None
