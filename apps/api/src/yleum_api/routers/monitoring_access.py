"""Application-private, read-only monitoring authentication."""

import re
from hashlib import sha256
from hmac import compare_digest
from secrets import token_urlsafe
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field, SecretStr
from redis.exceptions import RedisError

from yleum_api.core.admin import is_admin_user
from yleum_api.core.config import get_settings
from yleum_api.core.deps import OptionalUserDep
from yleum_api.core.errors import ApiError
from yleum_api.core.ratelimit import (
    clear_login_account_limit,
    rate_limit_auth,
    rate_limit_login_account,
)
from yleum_api.core.redis import get_redis
from yleum_api.core.request_origin import require_origin
from yleum_api.core.security import verify_password

router = APIRouter(prefix="/api/admin/monitoring/session", tags=["admin-monitoring"])
_COOKIE = "__Host-yleum_monitoring"
_TTL_SECONDS = 8 * 60 * 60
_TOKEN = re.compile(r"[A-Za-z0-9_-]{43}")
Access = Literal["admin", "viewer"]


class ViewerLogin(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: SecretStr = Field(min_length=1, max_length=256)


class AccessStatus(BaseModel):
    role: Access


def _credential() -> tuple[str, str] | None:
    settings = get_settings()
    encoded = settings.monitoring_viewer_password_hash
    if encoded and settings.monitoring_viewer_username:
        return settings.monitoring_viewer_username, encoded.get_secret_value()
    return None


def _digest(value: str) -> str:
    return sha256(value.encode()).hexdigest()


def _fingerprint(credential: tuple[str, str]) -> str:
    return _digest("\x00".join(credential))


def _session_key(token: str) -> str:
    return f"yleum:monitoring-viewer:{_digest(token)}"


def _viewer_cookie(request: Request) -> str | None:
    token = request.cookies.get(_COOKIE)
    return token if token and _TOKEN.fullmatch(token) else None


def _unavailable() -> ApiError:
    return ApiError("internal_error", "monitoring login is temporarily unavailable", 503)


async def get_monitoring_access(request: Request, user: OptionalUserDep) -> Access:
    admin = user is not None and is_admin_user(user)
    credential = _credential()
    token = _viewer_cookie(request)
    if credential and token:
        try:
            fingerprint = await get_redis().get(_session_key(token))
        except RedisError as exc:
            if not admin:
                raise _unavailable() from exc
        else:
            if fingerprint == _fingerprint(credential):
                return "viewer"
    if admin:
        return "admin"
    if user is not None:
        raise ApiError("forbidden", "monitoring access required", 403)
    raise ApiError("unauthorized", "monitoring session required", 401)


MonitoringAccessDep = Annotated[Access, Depends(get_monitoring_access)]


@router.get("", response_model=AccessStatus)
async def status(response: Response, access: MonitoringAccessDep) -> AccessStatus:
    response.headers["Cache-Control"] = "no-store"
    return AccessStatus(role=access)


@router.post("", status_code=204, dependencies=[Depends(require_origin), Depends(rate_limit_auth)])
async def login(payload: ViewerLogin, response: Response) -> None:
    credential = _credential()
    if credential is None:
        raise _unavailable()
    username, password_hash = credential
    candidate = payload.username.strip()
    rate_key = f"monitoring-viewer:{candidate}"
    await rate_limit_login_account(rate_key)
    try:
        password_ok = await verify_password(payload.password.get_secret_value(), password_hash)
    except ValueError as exc:
        raise _unavailable() from exc
    if not (compare_digest(candidate.encode(), username.encode()) and password_ok):
        raise ApiError("unauthorized", "invalid monitoring credentials", 401)

    token = token_urlsafe(32)
    try:
        await get_redis().set(_session_key(token), _fingerprint(credential), ex=_TTL_SECONDS)
    except RedisError as exc:
        raise _unavailable() from exc
    clear_login_account_limit(rate_key)
    response.headers["Cache-Control"] = "no-store"
    response.set_cookie(
        _COOKIE, token, max_age=_TTL_SECONDS, path="/", httponly=True,
        secure=True, samesite="lax",
    )


@router.delete("", status_code=204, dependencies=[Depends(require_origin)])
async def logout(request: Request, response: Response) -> None:
    if token := _viewer_cookie(request):
        try:
            await get_redis().delete(_session_key(token))
        except RedisError as exc:
            raise _unavailable() from exc
    response.headers["Cache-Control"] = "no-store"
    response.delete_cookie(_COOKIE, path="/", httponly=True, secure=True, samesite="lax")
