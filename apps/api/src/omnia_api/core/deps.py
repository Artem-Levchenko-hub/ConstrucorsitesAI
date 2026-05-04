from typing import Annotated

from fastapi import Cookie, Depends, Header, status
from sqlalchemy.ext.asyncio import AsyncSession

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


async def get_current_user(
    session: SessionDep,
    omnia_session: Annotated[str | None, Cookie()] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> User:
    token = _extract_token(omnia_session, authorization)
    if token is None:
        raise ApiError("unauthorized", "missing auth token", status.HTTP_401_UNAUTHORIZED)

    user_id = decode_access_token(token)
    if user_id is None:
        raise ApiError("unauthorized", "invalid or expired token", status.HTTP_401_UNAUTHORIZED)

    user = await session.get(User, user_id)
    if user is None:
        raise ApiError("unauthorized", "user not found", status.HTTP_401_UNAUTHORIZED)

    return user


CurrentUserDep = Annotated[User, Depends(get_current_user)]
