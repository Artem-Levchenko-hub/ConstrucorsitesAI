from __future__ import annotations

import hashlib
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
from yleum_api.core.ratelimit import rate_limit_auth, rate_limit_email
from yleum_api.core.security import consume_dummy_verify, hash_password, verify_password
from yleum_api.models.account import AuthSession, AuthToken
from yleum_api.models.user import User
from yleum_api.schemas.user import (
    EmailTokenConsume,
    EmailTokenRequest,
    PasswordResetConsume,
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
from yleum_api.services.accounts import LegalConsent, create_account
from yleum_api.services.transactional_email import (
    EmailDeliveryFailed,
    EmailDeliveryNotConfigured,
    send_transactional_email,
)

router = APIRouter(prefix="/api/auth", tags=["auth"])


def _token_hash(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


async def _issue_email_token(
    session: SessionDep,
    user: User,
    purpose: str,
    *,
    ttl: timedelta,
) -> str:
    await session.execute(
        update(AuthToken)
        .where(
            AuthToken.user_id == user.id,
            AuthToken.purpose == purpose,
            AuthToken.used_at.is_(None),
        )
        .values(used_at=datetime.now(UTC))
    )
    raw = secrets.token_urlsafe(48)
    session.add(
        AuthToken(
            user_id=user.id,
            purpose=purpose,
            token_hash=_token_hash(raw),
            expires_at=datetime.now(UTC) + ttl,
        )
    )
    await session.commit()
    return raw


async def _consume_email_token(
    session: SessionDep, raw: str, purpose: str, now: datetime
) -> User:
    """Spend the one-time token and return its user; the caller commits. The row
    lock makes two concurrent requests with one link see it spent exactly once."""
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
    user = await session.get(User, token.user_id)
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
    settings = get_settings()
    link = f"{settings.web_base_url.rstrip('/')}/max/verify-email?token={raw_token}"
    await send_transactional_email(
        recipient=str(user.email),
        subject="Подтвердите email для Yleum",
        text=(
            "Подтвердите email, чтобы продолжить создание MAX Mini App:\n\n"
            f"{link}\n\nСсылка действует 24 часа. Если это были не вы, ничего не делайте."
        ),
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
) -> User:
    settings = get_settings()
    if payload.product == "max":
        if not (
            payload.terms_accepted and payload.privacy_accepted and payload.personal_data_accepted
        ):
            raise ApiError(
                "legal_acceptance_required",
                "Для Yleum нужно принять условия, политику и согласие на обработку данных",
                status.HTTP_422_UNPROCESSABLE_ENTITY,
            )
        if payload.document_version != settings.legal_document_version:
            raise ApiError(
                "legal_version_outdated",
                "Юридические документы обновились. Обновите страницу и подтвердите их снова",
                status.HTTP_409_CONFLICT,
            )

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
            # General constructor keeps its existing frictionless behaviour. MAX
            # accounts must prove control of the address before creating a project.
            email_verified_at=datetime.now(UTC) if payload.product == "general" else None,
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
    return user


@router.post("/login", response_model=UserPublic, dependencies=[Depends(rate_limit_auth)])
async def login(
    payload: UserLogin,
    request: Request,
    response: Response,
    session: SessionDep,
) -> User:
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
    return user


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
    settings = get_settings()
    response.delete_cookie(
        key=settings.jwt_cookie_name,
        path="/",
        domain=settings.jwt_cookie_domain,
    )


@router.get("/me", response_model=UserPublic)
async def me(current_user: CurrentUserDep) -> User:
    return current_user


@router.post(
    "/email/verify/request",
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(rate_limit_email)],
)
async def request_email_verification(
    payload: EmailTokenRequest,
    session: SessionDep,
) -> dict[str, bool]:
    user = (
        await session.execute(select(User).where(User.email == payload.email))
    ).scalar_one_or_none()
    if user is None or user.email_verified_at is not None:
        return {"accepted": True}
    raw = await _issue_email_token(session, user, "verify_email", ttl=timedelta(hours=24))
    await _deliver(_send_verification(user, raw))
    return {"accepted": True}


@router.post("/email/verify", dependencies=[Depends(rate_limit_auth)])
async def verify_email(payload: EmailTokenConsume, session: SessionDep) -> dict[str, bool]:
    now = datetime.now(UTC)
    user = await _consume_email_token(session, payload.token, "verify_email", now)
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
            .where(AuthSession.user_id == current_user.id, AuthSession.revoked_at.is_(None))
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
