from __future__ import annotations

import hashlib
import hmac
import secrets
from collections.abc import Awaitable
from datetime import UTC, datetime, timedelta
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Cookie, Depends, Header, Request, Response, status
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from yleum_api.core.config import get_settings
from yleum_api.core.deps import CurrentUserDep, SessionDep, extract_access_claims
from yleum_api.core.errors import ApiError
from yleum_api.core.ratelimit import _client_ip, rate_limit_auth, rate_limit_email
from yleum_api.core.security import consume_dummy_verify, hash_password, verify_password
from yleum_api.models.account import AuthSession, AuthToken
from yleum_api.models.user import User
from yleum_api.schemas.user import (
    EmailCodeConsume,
    EmailTokenConsume,
    EmailTokenRequest,
    PasswordResetConsume,
    PhoneCodeConsume,
    PhoneCodeRequest,
    SessionPublic,
    UserCreate,
    UserLogin,
    UserPublic,
)
from yleum_api.services.account_sessions import (
    attach_session_cookie,
    open_session,
    request_ip,
    request_user_agent,
)
from yleum_api.services.accounts import (
    REQUIRED_LEGAL_DOCUMENTS,
    LegalConsent,
    accepted_legal_documents,
    create_account,
    has_verified_email,
    require_current_legal_acceptance,
)
from yleum_api.services.phone_verification import (
    normalize_phone,
    request_phone_code,
    verify_phone_code,
)
from yleum_api.services.registration_verification import registration_verification_reason
from yleum_api.services.transactional_email import (
    EmailDeliveryFailed,
    EmailDeliveryNotConfigured,
    send_transactional_email,
)

router = APIRouter(prefix="/api/auth", tags=["auth"])


async def _public_user(session: SessionDep, user: User) -> UserPublic:
    return UserPublic.model_validate(user).model_copy(update={
        "registration_verification_reason": await registration_verification_reason(session, user),
    })


def _token_hash(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def _verification_code(user_id: UUID, raw: str) -> str:
    key = get_settings().jwt_secret.get_secret_value().encode()
    digest = hmac.digest(key, f"email-code:{user_id}:{raw}".encode(), "sha256")
    return f"{int.from_bytes(digest[:8], 'big') % 1_000_000:06d}"


def _verification_code_hash(user_id: UUID, token_hash: str, code: str) -> str:
    key = get_settings().jwt_secret.get_secret_value().encode()
    message = f"email-code-hash:{user_id}:{token_hash}:{code}".encode()
    return hmac.new(key, message, hashlib.sha256).hexdigest()


async def _issue_email_token(
    session: SessionDep,
    user: User,
    purpose: str,
    *,
    ttl: timedelta,
) -> str:
    # Serialize issuance and consumption for this account in the same order.
    await session.execute(select(User.id).where(User.id == user.id).with_for_update())
    now = datetime.now(UTC)
    await session.execute(
        update(AuthToken)
        .where(
            AuthToken.user_id == user.id,
            AuthToken.purpose == purpose,
            AuthToken.used_at.is_(None),
        )
        .values(used_at=now)
    )
    raw = secrets.token_urlsafe(48)
    token_hash = _token_hash(raw)
    code = _verification_code(user.id, raw) if purpose == "verify_email" else None
    session.add(
        AuthToken(
            user_id=user.id,
            purpose=purpose,
            token_hash=token_hash,
            code_hash=_verification_code_hash(user.id, token_hash, code) if code else None,
            code_expires_at=now + timedelta(minutes=10) if code else None,
            expires_at=now + ttl,
        )
    )
    await session.commit()
    return raw


async def _consume_email_token(
    session: SessionDep, raw: str, purpose: str, now: datetime
) -> User:
    """Spend the one-time token and return its user; the caller commits. The row
    lock makes two concurrent requests with one link see it spent exactly once."""
    candidate = (
        await session.execute(
            select(AuthToken.user_id)
            .where(
                AuthToken.token_hash == _token_hash(raw),
                AuthToken.purpose == purpose,
            )
        )
    ).scalar_one_or_none()
    if candidate is None:
        raise ApiError(
            "token_invalid", "Ссылка недействительна или истекла", status.HTTP_400_BAD_REQUEST
        )
    user = (
        await session.execute(select(User).where(User.id == candidate).with_for_update())
    ).scalar_one_or_none()
    token = (
        await session.execute(
            select(AuthToken)
            .where(
                AuthToken.token_hash == _token_hash(raw),
                AuthToken.purpose == purpose,
                AuthToken.used_at.is_(None),
                AuthToken.expires_at > now,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    if token is None:
        raise ApiError(
            "token_invalid",
            "Ссылка недействительна или истекла",
            status.HTTP_400_BAD_REQUEST,
        )
    if user is None:
        raise ApiError("token_invalid", "Ссылка недействительна", status.HTTP_400_BAD_REQUEST)
    token.used_at = now
    return user


async def _deliver(letter: Awaitable[None]) -> None:
    """A letter the user asked for must not fail silently."""
    try:
        await letter
    except (EmailDeliveryNotConfigured, EmailDeliveryFailed) as exc:
        raise ApiError(
            "email_delivery_unavailable",
            "Отправка писем ещё не настроена. Обратитесь в поддержку",
            status.HTTP_503_SERVICE_UNAVAILABLE,
        ) from exc


async def _send_verification(user: User, raw_token: str) -> None:
    from yleum_api.services.email_templates import verification_email

    settings = get_settings()
    link = f"{settings.web_base_url.rstrip('/')}/max/verify-email?token={raw_token}"
    code = _verification_code(user.id, raw_token)
    mail = verification_email(code=code, link=link)
    await send_transactional_email(
        recipient=str(user.email),
        subject=mail.subject, text=mail.text, html=mail.html,
    )


@router.post(
    "/register",
    response_model=UserPublic,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(rate_limit_auth)],
)
async def register(
    payload: UserCreate,
    request: Request,
    response: Response,
    session: SessionDep,
) -> UserPublic:
    settings = get_settings()
    if payload.product == "max":
        require_current_legal_acceptance(payload)
        if payload.phone is None:
            raise ApiError("phone_invalid", "Укажите мобильный телефон")
    if payload.phone is not None:
        # Validation does not reserve the number; only a successful OTP can.
        normalize_phone(payload.phone)

    pwd_hash = await hash_password(payload.password)
    consent = (
        LegalConsent(
            document_version=settings.legal_document_version,
            ip_address=request_ip(request),
            user_agent=request_user_agent(request),
            marketing_accepted=payload.marketing_accepted,
        )
        if payload.product == "max"
        else None
    )
    try:
        user = await create_account(
            session,
            email=payload.email,
            password_hash=pwd_hash,
            # A client-selected product cannot prove control of an email address.
            # General registration still creates an account and opens its session.
            email_verified_at=None,
            consent=consent,
            signup_source=payload.source,
            referrer_project_id=payload.referrer_project_id,
        )
        auth_session = await open_session(session, user, request)
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise ApiError(
            "conflict",
            "email already registered",
            status.HTTP_409_CONFLICT,
        ) from exc
    await session.refresh(user)
    attach_session_cookie(response, user, auth_session)

    if payload.product == "max":
        raw_token = await _issue_email_token(session, user, "verify_email", ttl=timedelta(hours=24))
        try:
            await _send_verification(user, raw_token)
        except (EmailDeliveryNotConfigured, EmailDeliveryFailed):
            # Account creation must be durable even when delivery credentials
            # are being configured. The onboarding endpoint exposes readiness.
            pass
    return await _public_user(session, user)


@router.post("/phone/request", status_code=status.HTTP_202_ACCEPTED,
             dependencies=[Depends(rate_limit_auth)])
async def request_phone_verification(
    payload: PhoneCodeRequest, request: Request,
    current_user: CurrentUserDep, session: SessionDep,
) -> dict[str, bool | int]:
    accepted = await accepted_legal_documents(
        session, current_user.id, get_settings().legal_document_version,
    )
    if not set(REQUIRED_LEGAL_DOCUMENTS).issubset(accepted):
        raise ApiError(
            "legal_acceptance_required",
            "Примите актуальные документы Yleum перед отправкой SMS",
            status.HTTP_403_FORBIDDEN,
        )
    # Reuse the trusted nginx X-Real-IP policy; never the first forwarded hop.
    return await request_phone_code(
        session, current_user, payload.phone, ip_address=_client_ip(request),
    )


@router.post("/phone/verify", dependencies=[Depends(rate_limit_auth)])
async def verify_phone_verification(
    payload: PhoneCodeConsume, current_user: CurrentUserDep, session: SessionDep,
) -> dict[str, bool]:
    await verify_phone_code(session, current_user, payload.code)
    return {"verified": True}


@router.post("/login", response_model=UserPublic, dependencies=[Depends(rate_limit_auth)])
async def login(
    payload: UserLogin,
    request: Request,
    response: Response,
    session: SessionDep,
) -> UserPublic:
    result = await session.execute(select(User).where(User.email == payload.email))
    user = result.scalar_one_or_none()
    if user is None or user.password_hash is None:
        await consume_dummy_verify()
        raise ApiError("unauthorized", "invalid credentials", status.HTTP_401_UNAUTHORIZED)
    if not await verify_password(payload.password, user.password_hash):
        raise ApiError("unauthorized", "invalid credentials", status.HTTP_401_UNAUTHORIZED)
    if user.status != "active":
        raise ApiError("account_unavailable", "account is not active", status.HTTP_403_FORBIDDEN)
    user.last_login_at = datetime.now(UTC)
    auth_session = await open_session(session, user, request)
    await session.commit()
    await session.refresh(user)
    attach_session_cookie(response, user, auth_session)
    return await _public_user(session, user)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    response: Response,
    session: SessionDep,
    omnia_session: Annotated[str | None, Cookie()] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> None:
    claims = extract_access_claims(omnia_session, authorization)
    if claims and claims.session_id:
        auth_session = await session.get(AuthSession, claims.session_id)
        if auth_session and auth_session.revoked_at is None:
            auth_session.revoked_at = datetime.now(UTC)
            await session.commit()
    # Browser HTTP caches outlive session cookies; preserve unrelated site data.
    response.headers["Clear-Site-Data"] = '"cache"'
    response.headers["Cache-Control"] = "no-store"
    settings = get_settings()
    response.delete_cookie(
        key=settings.jwt_cookie_name,
        path="/",
        domain=settings.jwt_cookie_domain,
    )


@router.get("/me", response_model=UserPublic)
async def me(current_user: CurrentUserDep, session: SessionDep) -> UserPublic:
    from yleum_api.services.entitlements import free_chat_allowance

    limit, remaining = await free_chat_allowance(session, current_user.id)
    from yleum_api.services.generation_credits import credits_view, uses_unit_billing

    units = {}
    if await uses_unit_billing(session, current_user.id):
        credits = await credits_view(session, current_user.id)
        units = {
            "generation_credit_units_available": credits["included_edits_remaining"]
            + credits["available_units"],
            "generation_initials_available": credits["included_generations_remaining"]
            + credits["flexible_units"] // 5,
        }
        await session.commit()
    return (await _public_user(session, current_user)).model_copy(
        update={
            "user_chat_messages_limit": limit,
            "user_chat_messages_remaining": remaining,
            **units,
        }
    )


@router.post(
    "/email/verify/request",
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(rate_limit_email)],
)
async def request_email_verification(
    current_user: CurrentUserDep,
    session: SessionDep,
) -> dict[str, bool]:
    if current_user.is_anon or current_user.email is None:
        raise ApiError("forbidden", "Нужен аккаунт с email", status.HTTP_403_FORBIDDEN)
    if await has_verified_email(session, current_user):
        return {"accepted": True}
    raw = await _issue_email_token(session, current_user, "verify_email", ttl=timedelta(hours=24))
    await _deliver(_send_verification(current_user, raw))
    return {"accepted": True}


@router.post("/email/verify", dependencies=[Depends(rate_limit_auth)])
async def verify_email(payload: EmailTokenConsume, session: SessionDep) -> dict[str, bool]:
    now = datetime.now(UTC)
    user = await _consume_email_token(session, payload.token, "verify_email", now)
    user.email_verified_at = now
    await session.commit()
    return {"verified": True}


@router.post("/email/verify/code", dependencies=[Depends(rate_limit_auth)])
async def verify_email_code(
    payload: EmailCodeConsume, current_user: CurrentUserDep, session: SessionDep
) -> dict[str, bool]:
    now = datetime.now(UTC)
    user = (
        await session.execute(select(User).where(User.id == current_user.id).with_for_update())
    ).scalar_one()
    token = (
        await session.execute(
            select(AuthToken)
            .where(
                AuthToken.user_id == user.id,
                AuthToken.purpose == "verify_email",
                AuthToken.used_at.is_(None),
            )
            .order_by(AuthToken.created_at.desc())
            .limit(1)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if await has_verified_email(session, user) or token is None or token.code_hash is None:
        raise ApiError(
            "code_invalid", "Код недействителен. Запросите новый", status.HTTP_400_BAD_REQUEST
        )
    if token.code_expires_at is None or token.code_expires_at <= now or token.expires_at <= now:
        raise ApiError(
            "code_expired", "Срок кода истёк. Запросите новый", status.HTTP_400_BAD_REQUEST
        )
    if token.code_attempts >= 5:
        raise ApiError(
            "code_attempts_exceeded",
            "Попытки исчерпаны. Запросите новый код",
            status.HTTP_400_BAD_REQUEST,
        )
    expected = _verification_code_hash(user.id, token.token_hash, payload.code)
    if not hmac.compare_digest(token.code_hash, expected):
        token.code_attempts += 1
        await session.commit()
        raise ApiError("code_invalid", "Неверный код", status.HTTP_400_BAD_REQUEST)
    token.used_at = now
    user.email_verified_at = now
    await session.commit()
    return {"verified": True}


@router.post(
    "/password/forgot",
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(rate_limit_email)],
)
async def forgot_password(
    payload: EmailTokenRequest,
    session: SessionDep,
) -> dict[str, bool]:
    user = (
        await session.execute(select(User).where(User.email == payload.email))
    ).scalar_one_or_none()
    if user is None:
        return {"accepted": True}
    raw = await _issue_email_token(session, user, "reset_password", ttl=timedelta(minutes=30))
    settings = get_settings()
    link = f"{settings.web_base_url.rstrip('/')}/reset-password?token={raw}"
    await _deliver(
        send_transactional_email(
            recipient=str(user.email),
            subject="Сброс пароля Yleum",
            text=(
                f"Чтобы задать новый пароль, откройте ссылку:\n\n{link}\n\nОна действует 30 минут."
            ),
        )
    )
    return {"accepted": True}


@router.post("/password/reset", dependencies=[Depends(rate_limit_auth)])
async def reset_password(payload: PasswordResetConsume, session: SessionDep) -> dict[str, bool]:
    now = datetime.now(UTC)
    user = await _consume_email_token(session, payload.token, "reset_password", now)
    user.password_hash = await hash_password(payload.password)
    user.session_version += 1
    await session.execute(
        update(AuthSession)
        .where(AuthSession.user_id == user.id, AuthSession.revoked_at.is_(None))
        .values(revoked_at=now)
    )
    await session.commit()
    return {"reset": True}


@router.get("/sessions", response_model=list[SessionPublic])
async def list_sessions(
    current_user: CurrentUserDep,
    session: SessionDep,
    omnia_session: Annotated[str | None, Cookie()] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> list[SessionPublic]:
    claims = extract_access_claims(omnia_session, authorization)
    rows = (
        await session.execute(
            select(AuthSession)
            .where(
                AuthSession.user_id == current_user.id,
                AuthSession.revoked_at.is_(None),
                AuthSession.expires_at > datetime.now(UTC),
            )
            .order_by(AuthSession.created_at.desc())
        )
    ).scalars()
    return [
        SessionPublic(
            id=row.id,
            current=bool(claims and claims.session_id == row.id),
            user_agent=row.user_agent,
            ip_address=row.ip_address,
            created_at=row.created_at,
            last_seen_at=row.last_seen_at,
        )
        for row in rows
    ]


@router.delete("/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_session(
    session_id: UUID,
    current_user: CurrentUserDep,
    session: SessionDep,
) -> None:
    auth_session = await session.get(AuthSession, session_id)
    if auth_session is None or auth_session.user_id != current_user.id:
        raise ApiError("not_found", "session not found", status.HTTP_404_NOT_FOUND)
    auth_session.revoked_at = datetime.now(UTC)
    await session.commit()


@router.delete("/sessions", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_all_sessions(current_user: CurrentUserDep, session: SessionDep) -> None:
    now = datetime.now(UTC)
    current_user.session_version += 1
    await session.execute(
        update(AuthSession)
        .where(AuthSession.user_id == current_user.id, AuthSession.revoked_at.is_(None))
        .values(revoked_at=now)
    )
    await session.commit()
