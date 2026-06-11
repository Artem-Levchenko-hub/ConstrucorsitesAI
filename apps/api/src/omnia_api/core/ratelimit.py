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
    """Real client IP. Behind nginx the socket peer is the proxy, so prefer the
    first hop in ``X-Forwarded-For`` and fall back to the socket address."""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
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
    item = parse(settings.prompt_rate_limit)
    if not _limiter.hit(item, _rate_key(request)):
        raise ApiError(
            "rate_limited",
            "слишком часто — подождите немного",
            status.HTTP_429_TOO_MANY_REQUESTS,
        )


__all__ = ["rate_limit_prompt"]
