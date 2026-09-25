"""Пополнение кошелька. Списание за токены ведёт llm-gateway, не API."""

from __future__ import annotations

from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from yleum_api.models.wallet import Wallet
from yleum_api.models.wallet_charge import WalletCharge
from yleum_api.services.billing_accounts import resolve_billing_account


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
