"""Canonical ownership proof during the explicit phone verification rollout."""

from typing import Literal

from sqlalchemy.ext.asyncio import AsyncSession

from yleum_api.core.config import get_settings
from yleum_api.core.errors import ApiError
from yleum_api.models.user import User
from yleum_api.services.accounts import has_verified_email

RegistrationVerificationReason = Literal[
    "phone_verification_required",
    "email_verification_required",
]


async def registration_verification_reason(
    session: AsyncSession,
    user: User,
) -> RegistrationVerificationReason | None:
    """Proven phone wins; an enabled phone policy never falls back to email.

    Before rollout, preserve the canonical email authority instead of opening
    admission to an account with neither ownership proof. Anonymous identity is
    handled independently by each admission boundary and never gains access.
    """
    if user.is_anon:
        return None
    if user.phone_e164 and user.phone_verified_at:
        return None
    if get_settings().phone_verification_enabled:
        return "phone_verification_required"
    if await has_verified_email(session, user):
        return None
    return "email_verification_required"


async def require_registration_verification(session: AsyncSession, user: User) -> None:
    if user.is_anon or user.email is None:
        raise ApiError("max_registration_required", "Для Yleum нужна регистрация", 403)
    if user.status != "active":
        raise ApiError("account_unavailable", "Аккаунт недоступен", 403)
    reason = await registration_verification_reason(session, user)
    if reason:
        message = (
            "Подтвердите телефон для продолжения"
            if reason == "phone_verification_required"
            else "Подтвердите email для продолжения"
        )
        raise ApiError(reason, message, 403)
