import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from typing import cast
from uuid import UUID

import jwt
from jwt.exceptions import InvalidTokenError
from passlib.context import CryptContext

from omnia_api.core.config import get_settings

_pwd_context = CryptContext(schemes=["bcrypt"], bcrypt__rounds=12, deprecated="auto")


@lru_cache(maxsize=1)
def _dummy_hash() -> str:
    """Постоянный хэш для constant-time fallback при login с несуществующим email."""
    return cast(str, _pwd_context.hash("__omnia_dummy_password_for_constant_time__"))


async def hash_password(password: str) -> str:
    return cast(str, await asyncio.to_thread(_pwd_context.hash, password))


async def verify_password(password: str, password_hash: str) -> bool:
    return await asyncio.to_thread(_pwd_context.verify, password, password_hash)


async def consume_dummy_verify() -> None:
    """Имитирует bcrypt-verify, чтобы login на несуществующий email занимал то же время."""
    await asyncio.to_thread(_pwd_context.verify, "x", _dummy_hash())


@dataclass(frozen=True)
class AccessClaims:
    user_id: UUID
    session_id: UUID | None
    session_version: int | None


def create_access_token(
    user_id: UUID,
    *,
    session_id: UUID | None = None,
    session_version: int | None = None,
) -> str:
    settings = get_settings()
    now = datetime.now(UTC)
    payload = {
        "sub": str(user_id),
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(days=settings.jwt_ttl_days)).timestamp()),
    }
    if session_id is not None:
        payload["sid"] = str(session_id)
    if session_version is not None:
        payload["ver"] = session_version
    return jwt.encode(
        payload,
        settings.jwt_secret.get_secret_value(),
        algorithm=settings.jwt_algorithm,
    )


def decode_access_claims(token: str) -> AccessClaims | None:
    settings = get_settings()
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret.get_secret_value(),
            algorithms=[settings.jwt_algorithm],
        )
    except InvalidTokenError:
        return None
    sub = payload.get("sub")
    if not isinstance(sub, str):
        return None
    try:
        user_id = UUID(sub)
    except ValueError:
        return None
    session_id: UUID | None = None
    sid = payload.get("sid")
    if isinstance(sid, str):
        try:
            session_id = UUID(sid)
        except ValueError:
            return None
    version = payload.get("ver")
    if version is not None and not isinstance(version, int):
        return None
    return AccessClaims(
        user_id=user_id,
        session_id=session_id,
        session_version=version,
    )


def decode_access_token(token: str) -> UUID | None:
    claims = decode_access_claims(token)
    return claims.user_id if claims is not None else None
