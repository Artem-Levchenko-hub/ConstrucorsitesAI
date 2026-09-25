"""Вход через VK ID и Яндекс ID.

Рукопожатие целиком серверное: ``state`` и PKCE-verifier лежат в
``oauth_login_states`` с TTL, браузер получает только ссылку на провайдера.
Существующий аккаунт (по связке провайдера или по его email) входит сразу и
получает ту же cookie-сессию, что и при входе по паролю. Для нового человека
callback НЕ создаёт аккаунт молча: он выдаёт одноразовый билет и отправляет
браузер на экран подтверждения документов в web; аккаунт появляется только в
``POST /complete`` с теми же согласиями, что и у обычной регистрации.

Callback — это навигация браузера (не fetch), поэтому его ошибки уходят
редиректом на ``/login?oauth_error=<код>``; JSON-ошибки отдают только
``/pending`` и ``/complete``.
"""

from __future__ import annotations

import hashlib
import logging
import secrets
from datetime import UTC, datetime, timedelta
from typing import cast
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Query, Request, Response, status
from fastapi.responses import RedirectResponse
from sqlalchemy import delete, or_, select
from sqlalchemy.exc import IntegrityError

from yleum_api.core.config import get_settings
from yleum_api.core.deps import SessionDep
from yleum_api.core.errors import ApiError
from yleum_api.core.ratelimit import rate_limit_auth
from yleum_api.models.oauth_login import OAuthLoginState, UserIdentity
from yleum_api.models.user import User
from yleum_api.schemas.oauth_login import (
    OAuthCompleteRequest,
    OAuthPendingPublic,
    OAuthProvider,
    OAuthProviderPublic,
    OAuthProvidersPublic,
    OAuthStartPublic,
)
from yleum_api.schemas.user import UserPublic
from yleum_api.services import oauth_login
from yleum_api.services.account_sessions import (
    attach_session_cookie,
    open_session,
    request_ip,
    request_user_agent,
)
from yleum_api.services.accounts import LegalConsent, create_account
from yleum_api.services.oauth_login import OAuthLoginError, ProviderIdentity

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/auth/oauth", tags=["auth"])

# Сколько живёт state до возврата от провайдера и билет до подтверждения документов.
STATE_TTL = timedelta(minutes=10)
TICKET_TTL = timedelta(minutes=15)
# Отработавшие строки убираем при следующем старте, когда им больше суток.
STALE_AFTER = timedelta(days=1)
DEFAULT_NEXT = "/max"


def _hash(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def safe_next(raw: str | None) -> str:
    """Только same-origin путь (те же правила, что у web middleware.ts:safeNext)."""
    if not raw or len(raw) > 512:
        return DEFAULT_NEXT
    if not raw.startswith("/") or raw.startswith("//") or "\\" in raw:
        return DEFAULT_NEXT
    if any(character.isspace() or ord(character) < 32 for character in raw):
        return DEFAULT_NEXT
    return raw


def _web_url(path: str) -> str:
    return f"{get_settings().web_base_url.rstrip('/')}{path}"


def _login_redirect(code: str, next_path: str | None) -> RedirectResponse:
    query: dict[str, str] = {"oauth_error": code}
    if next_path and next_path != DEFAULT_NEXT:
        query["next"] = next_path
    return RedirectResponse(
        _web_url(f"/login?{urlencode(query)}"), status_code=status.HTTP_303_SEE_OTHER
    )


def _require_provider(provider: str) -> OAuthProvider:
    if provider not in oauth_login.PROVIDERS or not oauth_login.is_configured(provider):
        raise ApiError(
            "oauth_provider_unavailable",
            "Вход через этого провайдера не настроен",
            status.HTTP_404_NOT_FOUND,
        )
    return cast(OAuthProvider, provider)


async def _resolve_user(
    session: SessionDep, identity: ProviderIdentity, now: datetime
) -> User | None:
    """Аккаунт по связке провайдера, иначе по его email (тогда связка создаётся).

    Email провайдера считается подтверждённым: найденный по нему аккаунт
    получает ``email_verified_at``. Неактивный аккаунт возвращается как есть —
    без привязки, вызывающий откажет во входе.
    """
    linked = (
        await session.execute(
            select(UserIdentity).where(
                UserIdentity.provider == identity.provider,
                UserIdentity.provider_user_id == identity.provider_user_id,
            )
        )
    ).scalar_one_or_none()
    if linked is not None:
        user = await session.get(User, linked.user_id)
        if user is not None:
            linked.email = identity.email
            return user
    if identity.email is None:
        return None
    user = (
        await session.execute(select(User).where(User.email == identity.email))
    ).scalar_one_or_none()
    if user is None or user.status != "active":
        return user
    session.add(
        UserIdentity(
            user_id=user.id,
            provider=identity.provider,
            provider_user_id=identity.provider_user_id,
            email=identity.email,
        )
    )
    if user.email_verified_at is None:
        user.email_verified_at = now
    return user


async def _sign_in(
    session: SessionDep, user: User, request: Request, response: Response, now: datetime
) -> None:
    user.last_login_at = now
    auth_session = await open_session(session, user, request)
    await session.commit()
    await session.refresh(user)
    attach_session_cookie(response, user, auth_session)


async def _pending_record(session: SessionDep, raw_ticket: str, now: datetime) -> OAuthLoginState:
    record = (
        await session.execute(
            select(OAuthLoginState)
            .where(OAuthLoginState.ticket_hash == _hash(raw_ticket))
            .with_for_update()
        )
    ).scalar_one_or_none()
    if (
        record is None
        or record.completed_at is not None
        or record.ticket_expires_at is None
        or record.ticket_expires_at <= now
        or record.pending_provider_user_id is None
        or record.pending_email is None
    ):
        raise ApiError(
            "oauth_ticket_invalid",
            "Ссылка на подтверждение устарела. Войдите через провайдера ещё раз",
            status.HTTP_400_BAD_REQUEST,
        )
    return record


@router.get("/providers", response_model=OAuthProvidersPublic)
async def list_providers() -> OAuthProvidersPublic:
    """Только настроенные провайдеры — web рисует ровно эти кнопки."""
    settings = get_settings()
    return OAuthProvidersPublic(
        providers=[
            OAuthProviderPublic(
                provider=cast(OAuthProvider, provider),
                label=oauth_login.provider_label(provider),
            )
            for provider in oauth_login.configured_providers()
        ],
        legal_document_version=settings.legal_document_version,
    )


@router.get("/pending", response_model=OAuthPendingPublic)
async def pending_registration(
    session: SessionDep,
    ticket: str = Query(min_length=32, max_length=512),
) -> OAuthPendingPublic:
    """Что показать на экране подтверждения документов (билет не тратится)."""
    record = await _pending_record(session, ticket, datetime.now(UTC))
    return OAuthPendingPublic(
        provider=cast(OAuthProvider, record.provider),
        label=oauth_login.provider_label(record.provider),
        email=cast(str, record.pending_email),
        next=record.next_path or DEFAULT_NEXT,
        legal_document_version=get_settings().legal_document_version,
    )


@router.get(
    "/{provider}/start",
    response_model=OAuthStartPublic,
    dependencies=[Depends(rate_limit_auth)],
)
async def start_login(
    provider: str,
    session: SessionDep,
    next_path: str | None = Query(default=None, alias="next", max_length=512),
) -> OAuthStartPublic:
    provider_key = _require_provider(provider)
    now = datetime.now(UTC)
    await session.execute(
        delete(OAuthLoginState).where(
            OAuthLoginState.expires_at < now - STALE_AFTER,
            or_(
                OAuthLoginState.ticket_expires_at.is_(None),
                OAuthLoginState.ticket_expires_at < now - STALE_AFTER,
            ),
        )
    )
    raw_state = secrets.token_urlsafe(40)
    code_verifier: str | None = None
    code_challenge: str | None = None
    if oauth_login.uses_pkce(provider_key):
        code_verifier, code_challenge = oauth_login.generate_pkce()
    session.add(
        OAuthLoginState(
            provider=provider_key,
            state_hash=_hash(raw_state),
            code_verifier=code_verifier,
            next_path=safe_next(next_path),
            expires_at=now + STATE_TTL,
        )
    )
    await session.commit()
    return OAuthStartPublic(
        authorization_url=oauth_login.authorization_url(
            provider_key, state=raw_state, code_challenge=code_challenge
        )
    )


@router.get("/{provider}/callback", dependencies=[Depends(rate_limit_auth)])
async def finish_login(
    provider: str,
    request: Request,
    session: SessionDep,
    # Любой непустой state ищем в базе: чужой или обрезанный провайдером — это
    # редирект на /login с понятным кодом, а не 422 в лицо пользователю.
    state: str = Query(min_length=1, max_length=256),
    code: str | None = Query(default=None, max_length=4096),
    device_id: str | None = Query(default=None, max_length=512),
    error: str | None = Query(default=None, max_length=256),
) -> RedirectResponse:
    now = datetime.now(UTC)
    record = (
        await session.execute(
            select(OAuthLoginState)
            .where(OAuthLoginState.state_hash == _hash(state))
            .with_for_update()
        )
    ).scalar_one_or_none()
    if (
        record is None
        or record.provider != provider
        or record.used_at is not None
        or record.expires_at <= now
    ):
        return _login_redirect("oauth_state_invalid", None)
    record.used_at = now
    next_path = record.next_path or DEFAULT_NEXT
    if error or not code:
        await session.commit()
        return _login_redirect("oauth_cancelled", next_path)
    if not oauth_login.is_configured(provider):
        await session.commit()
        return _login_redirect("oauth_provider_unavailable", next_path)
    try:
        identity = await oauth_login.fetch_identity(
            provider,
            code=code,
            code_verifier=record.code_verifier,
            device_id=device_id,
            state=state,
        )
    except OAuthLoginError as exc:
        log.warning("oauth login: %s exchange failed: %s", provider, exc)
        await session.commit()
        return _login_redirect("oauth_exchange_failed", next_path)
    if identity.email is None:
        await session.commit()
        return _login_redirect("oauth_email_required", next_path)

    user = await _resolve_user(session, identity, now)
    if user is None:
        raw_ticket = secrets.token_urlsafe(40)
        record.ticket_hash = _hash(raw_ticket)
        record.ticket_expires_at = now + TICKET_TTL
        record.pending_provider_user_id = identity.provider_user_id
        record.pending_email = identity.email
        await session.commit()
        return RedirectResponse(
            _web_url(f"/oauth/complete?{urlencode({'ticket': raw_ticket})}"),
            status_code=status.HTTP_303_SEE_OTHER,
        )
    if user.status != "active":
        await session.commit()
        return _login_redirect("account_unavailable", next_path)
    response = RedirectResponse(_web_url(next_path), status_code=status.HTTP_303_SEE_OTHER)
    await _sign_in(session, user, request, response, now)
    return response


@router.post(
    "/complete",
    response_model=UserPublic,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(rate_limit_auth)],
)
async def complete_registration(
    payload: OAuthCompleteRequest,
    request: Request,
    response: Response,
    session: SessionDep,
) -> User:
    """Создать аккаунт по билету — только после явного принятия документов."""
    settings = get_settings()
    if not (payload.terms_accepted and payload.privacy_accepted and payload.personal_data_accepted):
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
    now = datetime.now(UTC)
    record = await _pending_record(session, payload.ticket, now)
    identity = ProviderIdentity(
        provider=record.provider,
        provider_user_id=cast(str, record.pending_provider_user_id),
        email=cast(str, record.pending_email),
    )
    # Пока человек читал документы, аккаунт с этим email мог появиться (например,
    # регистрация по паролю в соседней вкладке) — тогда просто связываем и входим.
    user = await _resolve_user(session, identity, now)
    if user is not None and user.status != "active":
        raise ApiError("account_unavailable", "account is not active", status.HTTP_403_FORBIDDEN)
    try:
        if user is None:
            user = await create_account(
                session,
                email=cast(str, identity.email),
                password_hash=None,
                # Провайдер подтвердил владение адресом — письмо не нужно.
                email_verified_at=now,
                consent=LegalConsent(
                    document_version=settings.legal_document_version,
                    ip_address=request_ip(request),
                    user_agent=request_user_agent(request),
                    marketing_accepted=payload.marketing_accepted,
                ),
            )
            session.add(
                UserIdentity(
                    user_id=user.id,
                    provider=identity.provider,
                    provider_user_id=identity.provider_user_id,
                    email=identity.email,
                )
            )
        record.completed_at = now
        await _sign_in(session, user, request, response, now)
    except IntegrityError as exc:
        await session.rollback()
        raise ApiError(
            "conflict",
            "Аккаунт с этим email уже есть — войдите через провайдера ещё раз",
            status.HTTP_409_CONFLICT,
        ) from exc
    return user
