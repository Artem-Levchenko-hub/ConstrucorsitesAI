import asyncio
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from uuid import UUID

from jose import JWTError, jwt
from passlib.context import CryptContext

from omnia_api.core.config import get_settings

_pwd_context = CryptContext(schemes=["bcrypt"], bcrypt__rounds=12, deprecated="auto")


@lru_cache(maxsize=1)
def _dummy_hash() -> str:
    """Постоянный хэш для constant-time fallback при login с несуществующим email."""
    return _pwd_context.hash("__omnia_dummy_password_for_constant_time__")


async def hash_password(password: str) -> str:
    return await asyncio.to_thread(_pwd_context.hash, password)


async def verify_password(password: str, password_hash: str) -> bool:
    return await asyncio.to_thread(_pwd_context.verify, password, password_hash)


async def consume_dummy_verify() -> None:
    """Имитирует bcrypt-verify, чтобы login на несуществующий email занимал то же время."""
    await asyncio.to_thread(_pwd_context.verify, "x", _dummy_hash())


def create_access_token(user_id: UUID) -> str:
    settings = get_settings()
    now = datetime.now(UTC)
    payload = {
        "sub": str(user_id),
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(days=settings.jwt_ttl_days)).timestamp()),
    }
    return jwt.encode(
        payload,
        settings.jwt_secret.get_secret_value(),
        algorithm=settings.jwt_algorithm,
    )


def decode_access_token(token: str) -> UUID | None:
    settings = get_settings()
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret.get_secret_value(),
            algorithms=[settings.jwt_algorithm],
        )
    except JWTError:
        return None
    sub = payload.get("sub")
    if not isinstance(sub, str):
        return None
    try:
        return UUID(sub)
    except ValueError:
        return None


# Short-lived signed CSRF token for the GitHub OAuth round-trip. Stateless
# (no DB/Redis) — the user id is carried in the token and verified on callback.
_OAUTH_STATE_TTL_SECONDS = 600


def create_oauth_state(user_id: UUID) -> str:
    settings = get_settings()
    now = int(datetime.now(UTC).timestamp())
    payload = {
        "sub": str(user_id),
        "purpose": "github_oauth",
        "iat": now,
        "exp": now + _OAUTH_STATE_TTL_SECONDS,
    }
    return jwt.encode(
        payload,
        settings.jwt_secret.get_secret_value(),
        algorithm=settings.jwt_algorithm,
    )


def verify_oauth_state(state: str) -> UUID | None:
    settings = get_settings()
    try:
        payload = jwt.decode(
            state,
            settings.jwt_secret.get_secret_value(),
            algorithms=[settings.jwt_algorithm],
        )
    except JWTError:
        return None
    if payload.get("purpose") != "github_oauth":
        return None
    sub = payload.get("sub")
    if not isinstance(sub, str):
        return None
    try:
        return UUID(sub)
    except ValueError:
        return None
