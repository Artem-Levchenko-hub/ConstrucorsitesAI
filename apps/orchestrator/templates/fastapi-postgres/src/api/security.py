"""JWT + bcrypt — the auth primitives every route in this template uses.

The flow:
1. `/auth/signup` (in `routers/auth.py`) — hash password, INSERT into users.
2. `/auth/signin` — verify password, sign a JWT, return it to the client.
3. Protected routes — `Depends(current_user)` validates the bearer token
   and returns the matching `User` row.

`SECRET_KEY` is injected by orchestrator (`AUTH_SECRET` env, same value
as the Next template's). Don't hard-code it — that's a security regression.
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from passlib.context import CryptContext
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.db import get_session
from api.models import User

# bcrypt rounds 10 ≈ ~80ms on modern hw — balances UX and brute-force cost.
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/signin")

ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_DAYS = 30


# Brute-forceable below this; boot-time validate_runtime_config() also enforces it.
_MIN_SECRET_LEN = 16


def _secret() -> str:
    secret = os.environ.get("AUTH_SECRET") or os.environ.get("JWT_SECRET")
    if not secret:
        raise RuntimeError(
            "AUTH_SECRET missing — orchestrator provisions it; restart container"
        )
    # Defense in depth: never sign with a weak key even if the boot check was
    # somehow bypassed (e.g. the var was set after start).
    if len(secret) < _MIN_SECRET_LEN:
        raise RuntimeError(
            f"AUTH_SECRET too short (need >= {_MIN_SECRET_LEN} chars)"
        )
    return secret


def hash_password(plain: str) -> str:
    return pwd_context.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


def issue_token(user_id: uuid.UUID) -> str:
    """Sign a JWT with `sub=user_id` + 30-day expiry. Token is the only
    thing the client needs to authenticate subsequent calls — no
    server-side session storage (stateless)."""
    payload: dict[str, Any] = {
        "sub": str(user_id),
        "exp": datetime.now(tz=timezone.utc)
        + timedelta(days=ACCESS_TOKEN_EXPIRE_DAYS),
    }
    return jwt.encode(payload, _secret(), algorithm=ALGORITHM)


async def current_user(
    token: str = Depends(oauth2_scheme),
    session: AsyncSession = Depends(get_session),
) -> User:
    """FastAPI dependency — returns the User matching the bearer token,
    raises 401 otherwise. Use as `Depends(current_user)` in any
    protected route."""
    creds_error = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="invalid credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, _secret(), algorithms=[ALGORITHM])
        sub = payload.get("sub")
        if not sub:
            raise creds_error
        user_id = uuid.UUID(str(sub))
    except (JWTError, ValueError) as exc:
        raise creds_error from exc

    user = (
        await session.execute(select(User).where(User.id == user_id))
    ).scalar_one_or_none()
    if user is None:
        raise creds_error
    return user
