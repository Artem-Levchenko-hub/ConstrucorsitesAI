from typing import Annotated

from fastapi import Cookie, Depends, Header, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from omnia_api.core.config import get_settings
from omnia_api.core.db import get_session
from omnia_api.core.errors import ApiError
from omnia_api.core.security import decode_access_token
from omnia_api.models.user import User

SessionDep = Annotated[AsyncSession, Depends(get_session)]


def _extract_token(cookie: str | None, authorization: str | None) -> str | None:
    if cookie:
        return cookie
    if authorization and authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    return None


def set_session_cookie(response: Response, token: str) -> None:
    """Single source of truth for the auth session cookie (R-04).

    Used by the auth router (register/login) and the anon-project seam
    (create_project hands the ephemeral principal a session so their project
    stays editable pre-auth).
    """
    settings = get_settings()
    response.set_cookie(
        key=settings.jwt_cookie_name,
        value=token,
        max_age=settings.jwt_ttl_days * 24 * 3600,
        httponly=True,
        secure=settings.jwt_cookie_secure,
        samesite="lax",
        path="/",
        domain=settings.jwt_cookie_domain,
    )


async def _resolve_user(
    session: AsyncSession, cookie: str | None, authorization: str | None
) -> User | None:
    token = _extract_token(cookie, authorization)
    if token is None:
        return None
    user_id = decode_access_token(token)
    if user_id is None:
        return None
    return await session.get(User, user_id)


async def get_current_user(
    session: SessionDep,
    omnia_session: Annotated[str | None, Cookie()] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> User:
    user = await _resolve_user(session, omnia_session, authorization)
    if user is None:
        raise ApiError("unauthorized", "missing or invalid auth", status.HTTP_401_UNAUTHORIZED)
    return user


async def get_optional_user(
    session: SessionDep,
    omnia_session: Annotated[str | None, Cookie()] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> User | None:
    """Resolve the caller if a valid session is present, else None.

    Unlike ``get_current_user`` this never raises — a missing, malformed, or
    stale cookie simply yields an anonymous caller. The anon-project seam
    (V4.1a) uses this so unauthenticated visitors can create a project.
    """
    return await _resolve_user(session, omnia_session, authorization)


CurrentUserDep = Annotated[User, Depends(get_current_user)]
OptionalUserDep = Annotated[User | None, Depends(get_optional_user)]
