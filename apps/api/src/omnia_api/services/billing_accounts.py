from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from omnia_api.core.errors import ApiError
from omnia_api.models.billing import BillingAccount


async def resolve_billing_account(
    session: AsyncSession,
    user_id: UUID,
    *,
    for_update: bool = False,
) -> BillingAccount:
    """Every billing account is personal: one user owns one wallet and one ledger."""

    statement = select(BillingAccount).where(BillingAccount.personal_user_id == user_id)
    if for_update:
        statement = statement.with_for_update()
    account = (await session.execute(statement)).scalar_one_or_none()
    if account is None:
        raise ApiError(
            "billing_account_not_found",
            "Платёжный аккаунт не инициализирован",
            409,
        )
    return account
