"""Buyer-side managed checkout. Merchant credentials remain inside the API."""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Header, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from yleum_api.core.deps import SessionDep
from yleum_api.core.errors import ApiError
from yleum_api.models.max_staff_checkout import MaxStaffPaymentAttempt
from yleum_api.routers.integration_runtime import _runtime_context
from yleum_api.services.max_staff_checkout import create_staff_checkout, get_staff_checkout

router = APIRouter(prefix="/api/runtime/projects", tags=["max-staff-checkout"])


class CheckoutRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    order_id: UUID
    init_data: str = Field(default="", max_length=16_384)
    return_url: str | None = Field(default=None, max_length=512)


class CheckoutPublic(BaseModel):
    order_id: UUID
    status: Literal["pending", "succeeded", "canceled", "needs_review"]
    confirmation_url: str | None
    failure_reason: Literal["receipt_required"] | None
    test: bool | None
    paid_at: datetime | None


def _receipt(attempt: MaxStaffPaymentAttempt, response: Response) -> CheckoutPublic:
    response.headers["Cache-Control"] = "no-store"
    return CheckoutPublic.model_validate(
        {
            "order_id": attempt.order_id,
            "status": attempt.status,
            "confirmation_url": attempt.confirmation_url if attempt.status == "pending" else None,
            "failure_reason": attempt.failure_reason,
            "test": attempt.is_test,
            "paid_at": attempt.paid_at if attempt.status == "succeeded" else None,
        }
    )


@router.post("/{project_id}/orders/checkout", response_model=CheckoutPublic)
async def checkout(
    project_id: UUID,
    payload: CheckoutRequest,
    session: SessionDep,
    request: Request,
    response: Response,
    x_max_init_data: Annotated[str, Header(alias="X-MAX-Init-Data")] = "",
) -> CheckoutPublic:
    actor = await _runtime_context(
        session, project_id, x_max_init_data or payload.init_data, request
    )
    if not 0 < actor.max_user_id < 2**63:
        raise ApiError("max_init_data_invalid", "Не удалось подтвердить запуск из MAX", 401)
    return _receipt(
        await create_staff_checkout(
            session,
            project_id,
            payload.order_id,
            actor.max_user_id,
            payload.return_url,
        ),
        response,
    )


@router.post("/{project_id}/orders/checkout/status", response_model=CheckoutPublic)
async def checkout_status(
    project_id: UUID,
    payload: CheckoutRequest,
    session: SessionDep,
    request: Request,
    response: Response,
    x_max_init_data: Annotated[str, Header(alias="X-MAX-Init-Data")] = "",
) -> CheckoutPublic:
    actor = await _runtime_context(
        session, project_id, x_max_init_data or payload.init_data, request
    )
    if not 0 < actor.max_user_id < 2**63:
        raise ApiError("max_init_data_invalid", "Не удалось подтвердить запуск из MAX", 401)
    return _receipt(
        await get_staff_checkout(session, project_id, payload.order_id, actor.max_user_id), response
    )
