"""Per-user rate limiting for the costly generate/edit endpoint (slowapi).

A single ``Limiter`` keyed by authenticated user, falling back to the real client
IP. The generate/edit path triggers an expensive LLM build, so an unthrottled
actor (script, abuser, or a runaway client) can drain a wallet and DoS the single
VPS. This is the abuse/cost floor that must sit in front of any public exposure.

Deep module: callers only touch ``limiter`` (decorate an endpoint with
``@limiter.limit(...)``) and ``rate_limit_handler`` (registered once in main).
The key strategy and proxy-IP handling live here, hidden behind that surface.
"""

from __future__ import annotations

from fastapi import Request
from slowapi import Limiter
from slowapi.util import get_remote_address

from omnia_api.core.config import get_settings
from omnia_api.core.deps import _extract_token
from omnia_api.core.security import decode_access_token


def _client_ip(request: Request) -> str:
    """Real client IP. Behind nginx the socket peer is the proxy, so prefer the
    first hop in ``X-Forwarded-For`` and fall back to the socket address."""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return get_remote_address(request)


def _rate_key(request: Request) -> str:
    """Rate-limit bucket key: the authenticated user when present, else client IP.

    The JWT is *verified* (cheap HS256, no DB) purely to derive a stable per-user
    key — a forged/expired token simply can't claim a user bucket and falls to the
    IP bucket (the route's auth dep still rejects it). This gives every real user
    their own generous bucket while anonymous/abusive traffic shares per-IP limits.
    """
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


# headers_enabled stays OFF: with it on slowapi requires every limited endpoint to
# also declare a `response: Response` param (to inject X-RateLimit-* headers), which
# we don't want to thread through the large generate/edit handler. The 429 body
# already tells the client it's throttled.
limiter = Limiter(key_func=_rate_key, enabled=get_settings().rate_limit_enabled)

__all__ = ["limiter"]
