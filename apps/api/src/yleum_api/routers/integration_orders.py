"""Read durable order receipts, scoped by authenticated MAX actor and project.

No credentials/provider HTTP are used here. Unknown outcomes are not reconciled
by resubmission. Historical own receipts remain readable after disconnect.
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Header, Request, Response
from pydantic import ValidationError
from sqlalchemy import Select, select

from yleum_api.core.deps import SessionDep
from yleum_api.core.errors import ApiError
from yleum_api.models.integration_operation import IntegrationOperation
from yleum_api.routers.integration_runtime import _runtime_context
from yleum_api.schemas.integration_runtime import (
    RuntimeOrderDetailsRequest,
    RuntimeOrderListPublic,
    RuntimeOrderPublic,
    RuntimeOrderReceiptPublic,
    RuntimeOrderSnapshot,
    RuntimeOrderStatusRequest,
)

router = APIRouter(prefix="/api/runtime/projects", tags=["integration-orders"])


def _public(row: IntegrationOperation) -> RuntimeOrderReceiptPublic:
    try:
        snapshot = (
            RuntimeOrderSnapshot.model_validate(row.result["snapshot"])
            if row.result.get("snapshot")
            else None
        )
        order = RuntimeOrderPublic.model_validate(row.result) if row.status == "succeeded" else None
        return RuntimeOrderReceiptPublic(
            idempotency_key=row.client_key,
            status=row.status,
            created_at=row.created_at,
            finished_at=row.finished_at,
            submitted_snapshot=snapshot,
            order=order,
            snapshot_status="recorded" if snapshot is not None else "unavailable",
        )
    except ValidationError as exc:
        raise ApiError(
            "integration_response_invalid", "Квитанция заказа временно недоступна", 502
        ) from exc


def _query(project_id: UUID, actor: int) -> Select[tuple[IntegrationOperation]]:
    return select(IntegrationOperation).where(
        IntegrationOperation.project_id == project_id,
        IntegrationOperation.max_user_id == str(actor),
        IntegrationOperation.provider == "moysklad",
        IntegrationOperation.kind == "customer_order",
    )


@router.get("/{project_id}/orders", response_model=RuntimeOrderListPublic)
async def list_runtime_orders(
    project_id: UUID,
    session: SessionDep,
    request: Request,
    response: Response,
    x_max_init_data: Annotated[str, Header(alias="X-MAX-Init-Data")] = "",
) -> RuntimeOrderListPublic:
    context = await _runtime_context(session, project_id, x_max_init_data, request)
    response.headers["Cache-Control"] = "private, no-store"
    rows = list(
        (
            await session.scalars(
                _query(project_id, context.max_user_id)
                .order_by(IntegrationOperation.created_at.desc(), IntegrationOperation.id.desc())
                .limit(21)
            )
        ).all()
    )
    return RuntimeOrderListPublic(
        items=[_public(row) for row in rows[:20]], has_more=len(rows) > 20
    )


@router.post("/{project_id}/orders/status", response_model=RuntimeOrderReceiptPublic)
async def runtime_order_status(
    project_id: UUID,
    payload: RuntimeOrderStatusRequest,
    session: SessionDep,
    request: Request,
    response: Response,
    x_max_init_data: Annotated[str, Header(alias="X-MAX-Init-Data")] = "",
) -> RuntimeOrderReceiptPublic:
    context = await _runtime_context(session, project_id, x_max_init_data, request)
    response.headers["Cache-Control"] = "private, no-store"
    row = (
        await session.scalars(
            _query(project_id, context.max_user_id).where(
                IntegrationOperation.client_key == payload.idempotency_key
            )
        )
    ).one_or_none()
    if row is None:
        raise ApiError("not_found", "Квитанция заказа не найдена", 404)
    return _public(row)


@router.post("/{project_id}/orders/details", response_model=RuntimeOrderReceiptPublic)
async def runtime_order_details(
    project_id: UUID,
    payload: RuntimeOrderDetailsRequest,
    session: SessionDep,
    request: Request,
    response: Response,
    x_max_init_data: Annotated[str, Header(alias="X-MAX-Init-Data")] = "",
) -> RuntimeOrderReceiptPublic:
    context = await _runtime_context(session, project_id, x_max_init_data, request)
    response.headers["Cache-Control"] = "private, no-store"
    row = (
        await session.scalars(
            _query(project_id, context.max_user_id)
            .where(
                IntegrationOperation.status == "succeeded",
                IntegrationOperation.result["id"].astext == str(payload.order_id),
            )
            .order_by(IntegrationOperation.created_at.desc())
            .limit(1)
        )
    ).one_or_none()
    if row is None:
        raise ApiError("not_found", "Квитанция заказа не найдена", 404)
    return _public(row)
