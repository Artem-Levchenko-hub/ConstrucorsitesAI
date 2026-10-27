from fastapi import APIRouter, Depends, Request, Response

from yleum_api.core.config import get_settings
from yleum_api.core.deps import CurrentUserDep, OptionalUserDep, SessionDep
from yleum_api.core.errors import ApiError
from yleum_api.core.ratelimit import rate_limit_auth, rate_limit_email
from yleum_api.schemas.account_credentials import (
    ConfirmationSent,
    CredentialInfo,
    EmailChange,
    EmailChangeConfirm,
    EmailConfirmed,
    PasswordChange,
    PasswordChanged,
)
from yleum_api.services import account_credentials as credentials
from yleum_api.services.email_templates import email_change_confirmation_email
from yleum_api.services.transactional_email import (
    EmailDeliveryFailed,
    EmailDeliveryNotConfigured,
    send_transactional_email,
)

router = APIRouter(prefix="/api/account/credentials", tags=["account"])


def clear_session(response: Response) -> None:
    settings = get_settings()
    response.delete_cookie(
        key=settings.jwt_cookie_name, path="/", domain=settings.jwt_cookie_domain
    )
    response.headers["Cache-Control"] = "no-store"


def require_origin(request: Request) -> None:
    settings = get_settings()
    allowed = {
        settings.web_base_url.rstrip("/"),
        *(value.rstrip("/") for value in settings.cors_origins_list),
    }
    if request.headers.get("origin") not in allowed:
        raise ApiError("forbidden", "Недопустимый источник запроса", 403)


@router.get("", response_model=CredentialInfo)
async def info(user: CurrentUserDep, response: Response) -> CredentialInfo:
    response.headers["Cache-Control"] = "no-store"
    return CredentialInfo(has_password=user.password_hash is not None)


@router.post("/password", response_model=PasswordChanged, dependencies=[Depends(rate_limit_auth)])
async def password_change(
    data: PasswordChange,
    request: Request,
    response: Response,
    user: CurrentUserDep,
    session: SessionDep,
) -> PasswordChanged:
    require_origin(request)
    await credentials.change_password(session, user.id, data.current_password, data.new_password)
    clear_session(response)
    return PasswordChanged()


@router.post(
    "/email",
    status_code=202,
    response_model=ConfirmationSent,
    dependencies=[Depends(rate_limit_auth), Depends(rate_limit_email)],
)
async def email_change(
    data: EmailChange, request: Request, user: CurrentUserDep, session: SessionDep
) -> ConfirmationSent:
    require_origin(request)
    old_email, new_email, old_token, new_token = await credentials.request_email_change(
        session, user.id, data.current_password, str(data.new_email)
    )
    base = get_settings().web_base_url.rstrip("/")
    for recipient, token, current_address in (
        (old_email, old_token, True),
        (new_email, new_token, False),
    ):
        mail = email_change_confirmation_email(
            link=f"{base}/confirm-email-change#token={token}",
            new_email=new_email,
            current_address=current_address,
        )
        try:
            await send_transactional_email(
                recipient=recipient, subject=mail.subject, text=mail.text, html=mail.html
            )
        except (EmailDeliveryNotConfigured, EmailDeliveryFailed):
            raise ApiError(
                "email_delivery_unavailable",
                "Не удалось отправить оба подтверждения. Повторите запрос позже.",
                503,
            ) from None
    return ConfirmationSent()


@router.post(
    "/email/confirm", response_model=EmailConfirmed, dependencies=[Depends(rate_limit_auth)]
)
async def email_confirm(
    data: EmailChangeConfirm,
    request: Request,
    response: Response,
    session: SessionDep,
    current_user: OptionalUserDep,
) -> EmailConfirmed:
    require_origin(request)
    incoming_user_id = current_user.id if current_user is not None else None
    target_user_id = await credentials.confirm_email_change(session, data.token)
    complete = target_user_id is not None
    reauthenticate = complete and incoming_user_id == target_user_id
    response.headers["Cache-Control"] = "no-store"
    if reauthenticate:
        clear_session(response)
    return EmailConfirmed(
        status="completed" if complete else "pending",
        reauthenticate=reauthenticate,
    )
