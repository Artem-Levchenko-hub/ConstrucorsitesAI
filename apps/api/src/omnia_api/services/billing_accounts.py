from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from omnia_api.core.errors import ApiError
from omnia_api.models.account import BusinessMember
from omnia_api.models.billing import BillingAccount


async def resolve_billing_account(
    session: AsyncSession,
    user_id: UUID,
    *,
    for_update: bool = False,
) -> BillingAccount:
    """Resolve the business account first, then the user's personal account."""

    business_id = (
        await session.execute(
            select(BusinessMember.business_id).where(BusinessMember.user_id == user_id)
        )
    ).scalar_one_or_none()
    statement = (
        select(BillingAccount).where(BillingAccount.business_id == business_id)
        if business_id is not None
        else select(BillingAccount).where(BillingAccount.personal_user_id == user_id)
    )
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


async def promote_personal_account_to_business(
    session: AsyncSession,
    *,
    user_id: UUID,
    business_id: UUID,
) -> BillingAccount:
    """Move the existing personal account to business scope without moving money."""

    existing = (
        await session.execute(
            select(BillingAccount)
            .where(BillingAccount.business_id == business_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing

    account = (
        await session.execute(
            select(BillingAccount)
            .where(BillingAccount.personal_user_id == user_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if account is None:
        raise ApiError(
            "billing_account_not_found",
            "Личный платёжный аккаунт не найден",
            409,
        )
    account.scope = "business"
    account.business_id = business_id
    account.personal_user_id = None
    await session.flush()
    return account
