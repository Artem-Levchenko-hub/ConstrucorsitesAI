"""Authenticated owner browser API. Runtime credentials remain server-side."""

import re
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError

from yleum_api.core.deps import CurrentUserDep, SessionDep
from yleum_api.core.errors import ApiError
from yleum_api.schemas.max_management import (
    EnableManagementPayload,
    ManagementOverviewPublic,
    OwnerRecordPublic,
    OwnerRecordsPagePublic,
    OwnerRecordsQuery,
    OwnerRecordUpdate,
    ResourceId,
)
from yleum_api.schemas.max_studio import MaxProjectConfigPublic
from yleum_api.services import max_management as service
from yleum_api.services.max_management_contract import new_project_management_modules

router = APIRouter(prefix="/api/projects", tags=["max-management"])


def _invalid_query() -> RequestValidationError:
    return RequestValidationError(
        [
            {
                "type": "value_error",
                "loc": ("query",),
                "msg": "Некорректные параметры запроса",
                "input": None,
            }
        ]
    )


def _query_values(request: Request) -> dict[str, object]:
    values: dict[str, object] = dict(request.query_params)
    if len(values) != len(request.query_params.multi_items()):
        raise _invalid_query()
    return values


def _records_query(request: Request) -> OwnerRecordsQuery:
    values = _query_values(request)
    if "limit" in values:
        limit = str(values["limit"])
        if not re.fullmatch(r"[0-9]{1,3}", limit):
            raise _invalid_query()
        values["limit"] = int(limit)
    try:
        return OwnerRecordsQuery.model_validate(values)
    except ValidationError:
        raise _invalid_query() from None


def _resource_query(request: Request) -> ResourceId:
    values = _query_values(request)
    if set(values) != {"resource"} or not isinstance(values["resource"], str):
        raise _invalid_query()
    try:
        return ResourceId(values["resource"])
    except ValueError:
        raise _invalid_query() from None


def _account(user: CurrentUserDep) -> None:
    if user.is_anon:
        raise ApiError("unauthorized", "Войдите в аккаунт для управления приложением", 401)


@router.get("/{project_id}/max/management", response_model=ManagementOverviewPublic)
async def get_management(
    project_id: UUID,
    session: SessionDep,
    current_user: CurrentUserDep,
) -> ManagementOverviewPublic:
    _account(current_user)
    return await service.get_overview(session, project_id, current_user.id)


@router.post("/{project_id}/max/management/enable", response_model=MaxProjectConfigPublic)
async def enable_management(
    project_id: UUID,
    payload: EnableManagementPayload,
    session: SessionDep,
    current_user: CurrentUserDep,
) -> MaxProjectConfigPublic:
    """Explicitly prepare an older app; source adoption and publishing stay separate."""
    from yleum_api.routers.max_studio import (
        _lock_owned_max_project,
        _put_max_config,
        get_max_config,
    )
    from yleum_api.services.max_legacy_order_management import read_legacy_order_table

    _account(current_user)
    await _lock_owned_max_project(session, project_id, current_user.id)
    current = await get_max_config(project_id, session, current_user)
    if current.config_version != payload.expected_config_version:
        raise ApiError("conflict", "Данные изменились. Обновите страницу и повторите", 409)
    modules = new_project_management_modules(
        current.config.app_type, current.config.management_modules,
    )
    legacy_table = current.config.legacy_orders_table
    if legacy_table is None and "orders" in modules:
        legacy_table = await read_legacy_order_table(session, project_id, current_user.id)
    desired = current.config.model_copy(update={
        "management_modules": modules, "legacy_orders_table": legacy_table,
    })
    # Reuse the normal owner lock, CAS, content protection and delivery path.
    # A concurrent save after the read is rejected by its second version check.
    return await _put_max_config(
        project_id, desired, session, current_user,
        x_max_config_version=payload.expected_config_version,
        legacy_orders_adoption=legacy_table,
    )


@router.get("/{project_id}/max/management/records", response_model=OwnerRecordsPagePublic)
async def get_records(
    project_id: UUID,
    session: SessionDep,
    current_user: CurrentUserDep,
    query: Annotated[OwnerRecordsQuery, Depends(_records_query)],
) -> OwnerRecordsPagePublic:
    _account(current_user)
    return await service.list_records(session, project_id, current_user.id, query)


@router.patch("/{project_id}/max/management/records/{record_id}", response_model=OwnerRecordPublic)
async def patch_record(
    project_id: UUID,
    record_id: UUID,
    payload: OwnerRecordUpdate,
    session: SessionDep,
    current_user: CurrentUserDep,
    resource: Annotated[ResourceId, Depends(_resource_query)],
) -> OwnerRecordPublic:
    _account(current_user)
    return await service.update_record(
        session, project_id, current_user.id, record_id, resource, payload
    )
