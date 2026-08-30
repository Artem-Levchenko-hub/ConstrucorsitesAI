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

from fastapi import Request, status
from limits import parse
from limits.storage import MemoryStorage
from limits.strategies import MovingWindowRateLimiter

from omnia_api.core.config import get_settings
from omnia_api.core.deps import _extract_token
from omnia_api.core.errors import ApiError
from omnia_api.core.security import decode_access_token

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


async def rate_limit_auth(request: Request) -> None:
    """Protect password hashing and login from brute force and CPU exhaustion."""
    settings = get_settings()
    if not settings.rate_limit_enabled:
        return
    client_ip = _client_ip(request)
    if not _limiter.hit(parse(settings.auth_ip_rate_limit), f"auth-ip:{client_ip}"):
        raise ApiError(
            "rate_limited",
            "Слишком много попыток. Подождите и попробуйте снова",
            status.HTTP_429_TOO_MANY_REQUESTS,
        )
    if not _limiter.hit(parse(settings.auth_rate_limit), f"auth-actor:{_rate_key(request)}"):
        raise ApiError(
            "rate_limited",
            "Слишком много попыток. Подождите и попробуйте снова",
            status.HTTP_429_TOO_MANY_REQUESTS,
        )


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


__all__ = [
    "rate_limit_auth",
    "rate_limit_email",
    "rate_limit_prompt",
    "rate_limit_task_board",
]
