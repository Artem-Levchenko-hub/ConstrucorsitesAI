"""Per-user rate limiting as a FastAPI DEPENDENCY (not a decorator).

The costly generate/edit path triggers an expensive LLM build, so an unthrottled
actor (script, abuser, runaway client) can drain a wallet and DoS the single VPS.
This is the abuse/cost floor that must sit in front of any public exposure.

Why a dependency, not slowapi's ``@limiter.limit`` decorator: the decorator wraps
the endpoint with ``functools.wraps``, and combined with ``from __future__ import
annotations`` in the routers that breaks FastAPI's ForwardRef resolution for typed
Path params (``project_id: UUID``) — the route 500s with a PydanticUserError. A
dependency leaves the endpoint signature untouched, so we drive slowapi's own
engine (the ``limits`` library) directly behind ``Depends(...)``.

The api runs a single uvicorn process, so in-memory storage is exact; promote to
``limits`` Redis storage if it ever scales to multiple workers.
"""

from __future__ import annotations

import hashlib
import hmac

from fastapi import Request, status
from limits import RateLimitItem, parse
from limits.storage import MemoryStorage
from limits.strategies import MovingWindowRateLimiter

from yleum_api.core.config import Settings, get_settings
from yleum_api.core.deps import _extract_token
from yleum_api.core.errors import ApiError
from yleum_api.core.security import decode_access_token

_storage = MemoryStorage()
_limiter = MovingWindowRateLimiter(_storage)


def _client_ip(request: Request) -> str:
    """Return the client IP supplied by the trusted same-host reverse proxy.

    Production nginx overwrites ``X-Real-IP`` with ``$remote_addr``.  A client
    may prepend arbitrary values to ``X-Forwarded-For``, so trusting its first
    entry would let an attacker choose a fresh rate-limit bucket per request.
    Direct host ports are loopback-only; the last forwarded hop is therefore a
    safe compatibility fallback when ``X-Real-IP`` is unavailable.
    """
    real_ip = request.headers.get("x-real-ip")
    if real_ip:
        return real_ip.strip()
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[-1].strip()
    client = request.client
    return client.host if client else "unknown"


def _rate_key(request: Request) -> str:
    """Rate-limit bucket key: the authenticated user when present, else client IP.

    The JWT is *verified* (cheap HS256, no DB) purely to derive a stable per-user
    key — a forged/expired token can't claim a user bucket and falls to the IP
    bucket (the route's auth dep still rejects it). Real users get their own
    generous bucket; anonymous/abusive traffic shares per-IP limits."""
    settings = get_settings()
    token = _extract_token(
        request.cookies.get(settings.jwt_cookie_name),
        request.headers.get("authorization"),
    )
    if token:
        user_id = decode_access_token(token)
        if user_id is not None:
            return f"user:{user_id}"
    return f"ip:{_client_ip(request)}"


async def rate_limit_prompt(request: Request) -> None:
    """Dependency: throttle the costly generate/edit/transcribe endpoints.

    Tunable via ``PROMPT_RATE_LIMIT`` (default 20/minute) and killable via
    ``RATE_LIMIT_ENABLED=false`` — no code change. Raises 429 when exceeded."""
    settings = get_settings()
    if not settings.rate_limit_enabled:
        return
    ip_item = parse(settings.prompt_ip_rate_limit)
    if not _limiter.hit(ip_item, f"prompt-ip:{_client_ip(request)}"):
        raise ApiError(
            "rate_limited",
            "слишком часто — подождите немного",
            status.HTTP_429_TOO_MANY_REQUESTS,
        )
    actor_item = parse(settings.prompt_rate_limit)
    if not _limiter.hit(actor_item, f"prompt-actor:{_rate_key(request)}"):
        raise ApiError(
            "rate_limited",
            "слишком часто — подождите немного",
            status.HTTP_429_TOO_MANY_REQUESTS,
        )


def _auth_rate_error() -> ApiError:
    return ApiError(
        "rate_limited",
        "Слишком много попыток. Подождите и попробуйте снова",
        status.HTTP_429_TOO_MANY_REQUESTS,
    )


def _login_account_bucket(settings: Settings, email: str) -> tuple[RateLimitItem, str]:
    # The login schema validates the address first. A case-insensitive account
    # keeps one bucket across source IPs; no plaintext address is stored as a key.
    digest = hmac.new(
        settings.jwt_secret.get_secret_value().encode(),
        email.strip().casefold().encode(),
        hashlib.sha256,
    ).hexdigest()
    return parse(settings.auth_account_rate_limit), f"auth-login-account:{digest}"


async def rate_limit_login_account(email: str) -> None:
    """Bound attempts before user lookup or password verification on every IP."""
    settings = get_settings()
    if settings.rate_limit_enabled:
        item, key = _login_account_bucket(settings, email)
        if not _limiter.hit(item, key):
            raise _auth_rate_error()


def clear_login_account_limit(email: str | None) -> None:
    """Only a successful login or proven credential recovery may clear this key."""
    settings = get_settings()
    if email and settings.rate_limit_enabled:
        item, key = _login_account_bucket(settings, email)
        _limiter.clear(item, key)


async def rate_limit_auth(request: Request) -> None:
    """Protect password hashing and login from brute force and CPU exhaustion."""
    settings = get_settings()
    if not settings.rate_limit_enabled:
        return
    client_ip = _client_ip(request)
    if not _limiter.hit(parse(settings.auth_ip_rate_limit), f"auth-ip:{client_ip}"):
        raise _auth_rate_error()
    if not _limiter.hit(parse(settings.auth_rate_limit), f"auth-actor:{_rate_key(request)}"):
        raise _auth_rate_error()


async def rate_limit_email(request: Request) -> None:
    """Prevent verification and password-reset email amplification."""
    settings = get_settings()
    if not settings.rate_limit_enabled:
        return
    if not _limiter.hit(parse(settings.email_rate_limit), f"email-ip:{_client_ip(request)}"):
        raise ApiError(
            "rate_limited",
            "Слишком много писем. Повторите позже",
            status.HTTP_429_TOO_MANY_REQUESTS,
        )


async def rate_limit_task_board(request: Request) -> None:
    """Keep the intentionally public team board usable under abusive traffic."""
    settings = get_settings()
    if not settings.rate_limit_enabled:
        return
    if not _limiter.hit(
        parse("30/minute"),
        f"task-board-ip:{_client_ip(request)}",
    ):
        raise ApiError(
            "rate_limited",
            "Слишком много изменений. Подождите и попробуйте снова",
            status.HTTP_429_TOO_MANY_REQUESTS,
        )


async def rate_limit_max_content_image(request: Request) -> None:
    """A catalog photo is the only owner-supplied binary in Studio."""
    settings = get_settings()
    if not settings.rate_limit_enabled:
        return
    if not _limiter.hit(parse("40/hour"), f"max-content-image:{_rate_key(request)}"):
        raise ApiError(
            "rate_limited",
            "Слишком много загрузок подряд. Подождите и повторите",
            status.HTTP_429_TOO_MANY_REQUESTS,
        )


__all__ = [
    "clear_login_account_limit",
    "rate_limit_auth",
    "rate_limit_email",
    "rate_limit_login_account",
    "rate_limit_max_content_image",
    "rate_limit_prompt",
    "rate_limit_task_board",
]
