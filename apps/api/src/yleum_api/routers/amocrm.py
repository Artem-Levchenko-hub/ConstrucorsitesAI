"""Project CRM setup and receipt-authorized current lead status."""

from datetime import UTC, datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Header, Request
from pydantic import BaseModel, Field
from sqlalchemy import select

from yleum_api.core.deps import CurrentUserDep, SessionDep
from yleum_api.core.errors import ApiError
from yleum_api.models.app_integration import AccountIntegration, ProjectIntegrationBinding
from yleum_api.models.integration_operation import IntegrationOperation
from yleum_api.routers.app_integrations import _account_connection, _binding, _owned_max_project
from yleum_api.routers.integration_runtime import _connections, _runtime_context, _secrets
from yleum_api.schemas.integration_runtime import (
    RuntimeLeadListPublic,
    RuntimeLeadStatusPublic,
    RuntimeLeadStatusRequest,
)
from yleum_api.services import amocrm
from yleum_api.services.integration_responses import invalid_response

router = APIRouter(tags=["amocrm"])
_SETUP = "/api/projects/{project_id}/app-integrations/amocrm"
_RUNTIME = "/api/runtime/projects/{project_id}/leads"


class AmoCRMSettings(BaseModel):
    pipeline_id: int = Field(gt=0, strict=True)
    status_id: int = Field(gt=0, strict=True)


async def _setup_connection(
    session: SessionDep,
    project_id: UUID,
    owner_id: UUID,
) -> tuple[AccountIntegration, ProjectIntegrationBinding]:
    await _owned_max_project(session, project_id, owner_id)
    connection = await _account_connection(session, owner_id, "amocrm")
    binding = await _binding(session, project_id, "amocrm")
    if (
        connection is None
        or connection.status != "active"
        or binding is None
        or not binding.enabled
    ):
        raise ApiError("crm_integration_required", "Подключите amoCRM к приложению", 409)
    return connection, binding


@router.get(_SETUP + "/options")
async def crm_options(
    project_id: UUID,
    session: SessionDep,
    current_user: CurrentUserDep,
) -> dict[str, Any]:
    connection, _ = await _setup_connection(session, project_id, current_user.id)
    credentials = await _secrets(session, connection)
    async with amocrm.client(credentials) as client:
        body = await amocrm.read(client, amocrm.base_url(connection) + "/api/v4/leads/pipelines")
    return {"pipelines": amocrm.pipeline_options(body)}


@router.put(_SETUP + "/settings")
async def save_crm_settings(
    project_id: UUID,
    payload: AmoCRMSettings,
    session: SessionDep,
    current_user: CurrentUserDep,
) -> dict[str, str]:
    _, binding = await _setup_connection(session, project_id, current_user.id)
    options = await crm_options(project_id, session, current_user)
    if not any(
        p["id"] == payload.pipeline_id and any(s["id"] == payload.status_id for s in p["statuses"])
        for p in options["pipelines"]
    ):
        raise ApiError(
            "integration_configuration_invalid",
            "Выберите доступную воронку и начальный этап amoCRM",
            422,
        )
    binding.config = {**binding.config, **payload.model_dump()}
    await session.commit()
    return {"status": "saved"}


async def _owned_receipts(
    session: SessionDep,
    project_id: UUID,
    max_user_id: int,
    connection: AccountIntegration,
    lead_id: str | None = None,
) -> list[IntegrationOperation]:
    query = select(IntegrationOperation).where(
        IntegrationOperation.project_id == project_id,
        IntegrationOperation.max_user_id == str(max_user_id),
        IntegrationOperation.integration_id == connection.id,
        IntegrationOperation.provider == "amocrm",
        IntegrationOperation.kind == "lead",
        IntegrationOperation.status == "succeeded",
        # Reauthorizing the same integration against another account must not
        # grant access to an unrelated lead with a coincidentally identical ID.
        IntegrationOperation.result["_account_base_url"].astext == amocrm.base_url(connection),
    )
    if lead_id is not None:
        query = query.where(IntegrationOperation.result["id"].astext == lead_id)
    return list(
        (
            await session.scalars(query.order_by(IntegrationOperation.created_at.desc()).limit(21))
        ).all()
    )


async def _current_statuses(
    session: SessionDep,
    connection: AccountIntegration,
    receipts: list[IntegrationOperation],
) -> list[RuntimeLeadStatusPublic]:
    if not receipts:
        return []
    credentials = await _secrets(session, connection)
    base = amocrm.base_url(connection)
    result = []
    async with amocrm.client(credentials) as client:
        pipelines = amocrm.pipeline_options(
            await amocrm.read(client, base + "/api/v4/leads/pipelines"),
            include_closed=True,
        )
        for receipt in receipts:
            identifier = str(receipt.result.get("id", ""))
            if not identifier.isdecimal():
                raise invalid_response()
            body = await amocrm.read(client, f"{base}/api/v4/leads/{identifier}")
            try:
                if str(amocrm.positive_id(body["id"])) != identifier:
                    raise ValueError
                pipeline_id = amocrm.positive_id(body["pipeline_id"])
                status_id = amocrm.positive_id(body["status_id"])
                pipeline = next((p for p in pipelines if p["id"] == pipeline_id), None)
                stage = (
                    next((s for s in pipeline["statuses"] if s["id"] == status_id), None)
                    if pipeline
                    else None
                )
                if pipeline is None or stage is None:
                    raise ValueError
                result.append(
                    RuntimeLeadStatusPublic(
                        provider="amocrm",
                        id=identifier,
                        name=amocrm.label(body["name"]),
                        pipeline_id=pipeline_id,
                        pipeline_name=pipeline["name"],
                        status_id=status_id,
                        status_name=stage["name"],
                        updated_at=amocrm.positive_id(body["updated_at"]),
                        checked_at=datetime.now(UTC).isoformat(),
                        details_status=receipt.result.get("details_status", "unknown"),
                        warning=receipt.result.get("warning"),
                    )
                )
            except (KeyError, TypeError, ValueError) as exc:
                raise invalid_response() from exc
    return result


async def _runtime_connection(session: SessionDep, project_id: UUID) -> AccountIntegration:
    connection = (await _connections(session, project_id)).get("amocrm")
    if connection is None:
        raise ApiError("crm_integration_required", "Подключите amoCRM к приложению", 409)
    return connection


@router.get(_RUNTIME, response_model=RuntimeLeadListPublic)
async def list_my_leads(
    project_id: UUID,
    session: SessionDep,
    request: Request,
    x_max_init_data: Annotated[str, Header(alias="X-MAX-Init-Data")] = "",
) -> RuntimeLeadListPublic:
    context = await _runtime_context(session, project_id, x_max_init_data, request)
    connection = await _runtime_connection(session, project_id)
    receipts = await _owned_receipts(session, project_id, context.max_user_id, connection)
    return RuntimeLeadListPublic(
        items=await _current_statuses(session, connection, receipts[:20]),
        has_more=len(receipts) > 20,
    )


@router.post(_RUNTIME + "/status", response_model=RuntimeLeadStatusPublic)
async def my_lead_status(
    project_id: UUID,
    payload: RuntimeLeadStatusRequest,
    session: SessionDep,
    request: Request,
    x_max_init_data: Annotated[str, Header(alias="X-MAX-Init-Data")] = "",
) -> RuntimeLeadStatusPublic:
    context = await _runtime_context(session, project_id, x_max_init_data, request)
    connection = await _runtime_connection(session, project_id)
    receipts = await _owned_receipts(
        session, project_id, context.max_user_id, connection, payload.lead_id
    )
    if not receipts:
        raise ApiError("not_found", "Заявка не найдена", 404)
    return (await _current_statuses(session, connection, receipts[:1]))[0]
