from __future__ import annotations

from fastapi import APIRouter, Request, status

from yleum_api.core.admin import is_admin_user
from yleum_api.core.config import get_settings
from yleum_api.core.deps import CurrentUserDep, SessionDep
from yleum_api.core.errors import ApiError
from yleum_api.models.user import User
from yleum_api.schemas.max_account import MaxAccessPublic
from yleum_api.schemas.user import LegalAcceptanceRequest
from yleum_api.services.account_sessions import request_ip, request_user_agent
from yleum_api.services.accounts import (
    LegalConsent,
    has_verified_email,
    record_legal_acceptance,
    require_current_legal_acceptance,
)
from yleum_api.services.max_access import max_studio_access_reason
from yleum_api.services.phone_verification import phone_verification_required
from yleum_api.services.registration_verification import registration_verification_reason
from yleum_api.services.sms_delivery import sms_delivery_configured
from yleum_api.services.transactional_email import email_delivery_configured

router = APIRouter(prefix="/api/max/account", tags=["max-account"])


def _is_admin(user: User) -> bool:
    return is_admin_user(user)


@router.get("/access", response_model=MaxAccessPublic)
async def get_access(current_user: CurrentUserDep, session: SessionDep) -> MaxAccessPublic:
    """The same persisted admission decision used by MAX project creation."""
    settings = get_settings()
    reason = await max_studio_access_reason(session, current_user)
    return MaxAccessPublic(
        email_verified=await has_verified_email(session, current_user),
        email_delivery_configured=email_delivery_configured(),
        phone_verified=bool(current_user.phone_e164 and current_user.phone_verified_at),
        phone_verification_required=phone_verification_required(current_user),
        phone_verification_available=sms_delivery_configured(),
        # Legal admission is shown first, while this separate field remains the
        # canonical identity decision used by the authenticated account UI.
        registration_verification_reason=await registration_verification_reason(
            session, current_user,
        ),
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


@router.post("/acceptances")
async def accept_documents(
    payload: LegalAcceptanceRequest, request: Request,
    current_user: CurrentUserDep, session: SessionDep,
) -> dict[str, bool]:
    if current_user.is_anon or current_user.email is None:
        raise ApiError("max_registration_required", "Для Yleum нужна регистрация", 403)
    require_current_legal_acceptance(payload)
    await record_legal_acceptance(session, current_user, LegalConsent(
        document_version=get_settings().legal_document_version,
        ip_address=request_ip(request), user_agent=request_user_agent(request),
        marketing_accepted=payload.marketing_accepted,
    ))
    await session.commit()
    return {"accepted": True}


@router.get("/admin/access")
async def get_admin_access(current_user: CurrentUserDep) -> dict[str, bool]:
    if not _is_admin(current_user):
        raise ApiError("forbidden", "admin access required", status.HTTP_403_FORBIDDEN)
    return {"is_admin": True}
