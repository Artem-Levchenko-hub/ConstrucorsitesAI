from __future__ import annotations

from fastapi import status

from yleum_api.core.errors import ApiError
from yleum_api.models.user import User


def require_max_studio_access(user: User) -> None:
    """Yleum needs a registered account with a verified email, nothing else.

    The business behind a bot is verified by MAX itself, so the platform never
    asks for (or stores) the owner's legal identity.
    """
    if user.is_anon or user.email is None:
        raise ApiError(
            "max_registration_required",
            "Для Yleum нужна регистрация",
            status.HTTP_403_FORBIDDEN,
        )
    if user.email_verified_at is None:
        raise ApiError(
            "email_verification_required",
            "Подтвердите email перед созданием MAX Mini App",
            status.HTTP_403_FORBIDDEN,
        )
