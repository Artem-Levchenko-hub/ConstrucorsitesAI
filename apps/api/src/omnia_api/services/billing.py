"""Атомарное списание за токены: usage + wallet_charge + decrement balance."""

from __future__ import annotations

from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from omnia_api.core.errors import ApiError
from omnia_api.models.usage import Usage
from omnia_api.models.wallet import Wallet
from omnia_api.models.wallet_charge import WalletCharge
from omnia_api.services.billing_accounts import resolve_billing_account


async def charge_for_message(
    session: AsyncSession,
    user_id: UUID,
    message_id: UUID | None,
    project_id: UUID | None,
    model_id: str,
    tokens_in: int,
    tokens_out: int,
    cost_rub: Decimal,
    description: str,
) -> Decimal:
    account = await resolve_billing_account(session, user_id)
    res = await session.execute(
        select(Wallet)
        .where(Wallet.billing_account_id == account.id)
        .with_for_update()
    )
    wallet = res.scalar_one()
    new_balance = wallet.balance_rub - cost_rub
    if new_balance < 0:
        raise ApiError("wallet_empty", "insufficient balance", 402)
    wallet.balance_rub = new_balance

    usage_id = uuid4()
    session.add(
        Usage(
            id=usage_id,
            user_id=user_id,
            project_id=project_id,
            message_id=message_id,
            model_id=model_id,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            cost_rub=cost_rub,
        )
    )
    session.add(
        WalletCharge(
            billing_account_id=account.id,
            user_id=user_id,
            message_id=message_id,
            entry_type="usage",
            amount_rub=-cost_rub,
            balance_after_rub=new_balance,
            external_ref=f"usage:{usage_id}",
            description=description,
        )
    )
    return new_balance


async def topup(
    session: AsyncSession, user_id: UUID, amount_rub: Decimal, description: str
) -> Decimal:
    account = await resolve_billing_account(session, user_id)
    res = await session.execute(
        select(Wallet)
        .where(Wallet.billing_account_id == account.id)
        .with_for_update()
    )
    wallet = res.scalar_one()
    wallet.balance_rub = wallet.balance_rub + amount_rub
    topup_id = uuid4()
    session.add(
        WalletCharge(
            id=topup_id,
            billing_account_id=account.id,
            user_id=user_id,
            message_id=None,
            entry_type="topup",
            amount_rub=amount_rub,
            balance_after_rub=wallet.balance_rub,
            external_ref=f"topup:{topup_id}",
            description=description,
        )
    )
    return wallet.balance_rub
