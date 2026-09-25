from __future__ import annotations

from fastapi import APIRouter, status

from yleum_api.core.admin import is_admin_user
from yleum_api.core.config import get_settings
from yleum_api.core.deps import CurrentUserDep
from yleum_api.core.errors import ApiError
from yleum_api.models.user import User
from yleum_api.schemas.max_account import MaxAccessPublic
from yleum_api.services.transactional_email import email_delivery_configured

router = APIRouter(prefix="/api/max/account", tags=["max-account"])


def _is_admin(user: User) -> bool:
    return is_admin_user(user)


@router.get("/access", response_model=MaxAccessPublic)
async def get_access(current_user: CurrentUserDep) -> MaxAccessPublic:
    """An account is an email and a password; only the email must be confirmed."""
    settings = get_settings()
    if current_user.is_anon or current_user.email is None:
        reason = "registration_required"
    elif current_user.email_verified_at is None:
        reason = "email_verification_required"
    else:
        reason = None
    return MaxAccessPublic(
        email_verified=current_user.email_verified_at is not None,
        email_delivery_configured=email_delivery_configured(),
        can_create_project=reason is None,
        reason=reason,
        legal_document_version=settings.legal_document_version,
        payments_configured=bool(
            settings.yookassa_shop_id
            and settings.yookassa_secret_key
            and settings.legal_operator_name
            and settings.legal_operator_inn
        ),
    )


@router.get("/admin/access")
async def get_admin_access(current_user: CurrentUserDep) -> dict[str, bool]:
    if not _is_admin(current_user):
        raise ApiError("forbidden", "admin access required", status.HTTP_403_FORBIDDEN)
    return {"is_admin": True}
