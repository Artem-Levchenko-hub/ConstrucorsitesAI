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
from yleum_api.core.deps import set_session_cookie
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
    """Добавить строку сессии (flush, без commit — коммитит вызывающий)."""
    settings = get_settings()
    auth_session = AuthSession(
        user_id=user.id,
        user_agent=request_user_agent(request),
        ip_address=request_ip(request),
        expires_at=datetime.now(UTC) + timedelta(days=settings.jwt_ttl_days),
    )
    session.add(auth_session)
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
