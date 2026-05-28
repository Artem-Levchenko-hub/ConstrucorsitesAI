from __future__ import annotations

from sqlalchemy import select

from fastapi import APIRouter

from omnia_api.core.config import FREE_GENERATION_LIMIT
from omnia_api.core.deps import CurrentUserDep, SessionDep
from omnia_api.core.errors import ApiError
from omnia_api.core.redis import publish_event
from omnia_api.models.wallet import Wallet
from omnia_api.models.wallet_charge import WalletCharge
from omnia_api.schemas.wallet import (
    ChargePublic,
    TopupRequest,
    TopupResponse,
    WalletPublic,
)
from omnia_api.services.billing import topup as topup_svc

router = APIRouter(prefix="/api/wallet", tags=["wallet"])


@router.get("", response_model=WalletPublic)
async def get_wallet(session: SessionDep, current_user: CurrentUserDep) -> WalletPublic:
    wallet = await session.get(Wallet, current_user.id)
    if wallet is None:
        raise ApiError("not_found", "wallet not initialized", 404)
    res = await session.execute(
        select(WalletCharge)
        .where(WalletCharge.user_id == current_user.id)
        .order_by(WalletCharge.created_at.desc())
        .limit(20)
    )
    charges = [ChargePublic.model_validate(c) for c in res.scalars().all()]
    used = current_user.free_generations_used or 0
    return WalletPublic(
        balance_rub=wallet.balance_rub,
        recent_charges=charges,
        free_generations_left=max(0, FREE_GENERATION_LIMIT - used),
        free_generation_limit=FREE_GENERATION_LIMIT,
    )


@router.post("/topup", response_model=TopupResponse)
async def topup_wallet(
    payload: TopupRequest, session: SessionDep, current_user: CurrentUserDep
) -> TopupResponse:
    new_balance = await topup_svc(
        session, current_user.id, payload.amount_rub, "Top-up (MVP stub)"
    )
    await session.commit()
    # Шлём wallet.updated всем проектам пользователя? В MVP — только если открыт WS.
    # Здесь fan-out по project_id невозможен без list проектов; шлём по user-каналу.
    # TODO когда будет user-канал в WS hub — публиковать туда.
    return TopupResponse(balance_rub=new_balance)
