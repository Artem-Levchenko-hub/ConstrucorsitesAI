from __future__ import annotations

from fastapi import status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from omnia_api.core.errors import ApiError
from omnia_api.models.account import BusinessMember, BusinessProfile
from omnia_api.models.user import User


async def get_user_business(session: AsyncSession, user_id: object) -> BusinessProfile | None:
    return (
        await session.execute(
            select(BusinessProfile)
            .join(BusinessMember, BusinessMember.business_id == BusinessProfile.id)
            .where(BusinessMember.user_id == user_id)
        )
    ).scalar_one_or_none()


async def require_max_business(session: AsyncSession, user: User) -> BusinessProfile:
    if user.is_anon or user.email is None:
        raise ApiError(
            "max_registration_required",
            "Для MAX Studio нужна регистрация",
            status.HTTP_403_FORBIDDEN,
        )
    if user.email_verified_at is None:
        raise ApiError(
            "email_verification_required",
            "Подтвердите email перед созданием MAX Mini App",
            status.HTTP_403_FORBIDDEN,
        )
    business = await get_user_business(session, user.id)
    if business is None:
        raise ApiError(
            "business_profile_required",
            "Добавьте юридическое лицо, ИП или профиль самозанятого",
            status.HTTP_403_FORBIDDEN,
        )
    if business.status != "verified":
        raise ApiError(
            "business_verification_required",
            "Бизнес-профиль ещё не подтверждён",
            status.HTTP_403_FORBIDDEN,
        )
    return business
