"""Cookie-сессия платформы: один способ открыть сессию и выдать cookie.

Используется обычным входом/регистрацией (routers/auth.py) и входом через
VK ID / Яндекс ID (routers/auth_oauth.py) — пользователь, вошедший через
провайдера, получает ровно ту же сессию, что и вошедший по паролю.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from yleum_api.core.config import get_settings
from yleum_api.core.deps import extract_access_claims, set_session_cookie
from yleum_api.core.security import create_access_token
from yleum_api.models.account import AuthSession
from yleum_api.models.user import User


def request_ip(request: Request) -> str | None:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",", 1)[0].strip()[:128]
    return request.client.host[:128] if request.client else None


def request_user_agent(request: Request) -> str | None:
    return request.headers.get("user-agent", "")[:1000] or None


async def open_session(session: AsyncSession, user: User, request: Request) -> AuthSession:
    """Renew the caller's proven session or create one; the caller commits.

    Browser/IP metadata cannot identify a device. Only the signed session the
    caller already holds may be reused, including during password/OAuth relogin.
    """
    settings = get_settings()
    now = datetime.now(UTC)
    claims = extract_access_claims(
        request.cookies.get(settings.jwt_cookie_name), request.headers.get("authorization")
    )
    auth_session = None
    if (
        claims is not None
        and claims.user_id == user.id
        and claims.session_version == user.session_version
        and claims.session_id is not None
    ):
        existing = await session.get(AuthSession, claims.session_id)
        if (
            existing is not None
            and existing.user_id == user.id
            and existing.revoked_at is None
            and existing.expires_at > now
        ):
            auth_session = existing
    if auth_session is None:
        auth_session = AuthSession(user_id=user.id)
        session.add(auth_session)
    auth_session.user_agent = request_user_agent(request)
    auth_session.ip_address = request_ip(request)
    auth_session.last_seen_at = now
    auth_session.expires_at = now + timedelta(days=settings.jwt_ttl_days)
    await session.flush()
    return auth_session


def attach_session_cookie(response: Response, user: User, auth_session: AuthSession) -> None:
    set_session_cookie(
        response,
        create_access_token(
            user.id,
            session_id=auth_session.id,
            session_version=user.session_version,
        ),
    )


__all__ = ["attach_session_cookie", "open_session", "request_ip", "request_user_agent"]
