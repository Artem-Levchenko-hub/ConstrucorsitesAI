"""Single source of truth for slowapi rate limits.

R-01 (deep module): one `limiter` object + a single key function. Routers
just import `limiter` and decorate. Policy changes live here, not scattered.

Key function strategy:
  - For authenticated routes (prompt, topup, projects mutations): bucket by
    user_id so one user can't exhaust the IP bucket for everyone behind a
    NAT, and one IP can't churn through accounts. Cookie is decoded inline —
    no `Depends(get_current_user)` because slowapi runs before DI.
  - For pre-auth routes (login, register): bucket by IP. `get_remote_address`
    reads `request.client.host`; uvicorn's ProxyHeadersMiddleware (registered
    in main.py) rewrites it from `X-Forwarded-For` so we get real client IP
    behind nginx.
"""

from __future__ import annotations

from fastapi import Request
from slowapi import Limiter
from slowapi.util import get_remote_address

from omnia_api.core.config import get_settings
from omnia_api.core.security import decode_access_token


def user_id_or_ip_key(request: Request) -> str:
    """Bucket by user_id when authenticated, fall back to client IP otherwise.

    `f"user:{uid}"` and `f"ip:{addr}"` are distinct namespaces so a user
    can't collide with a public IP bucket.
    """
    settings = get_settings()
    cookie = request.cookies.get(settings.jwt_cookie_name)
    if cookie:
        try:
            uid = decode_access_token(cookie)
            if uid is not None:
                return f"user:{uid}"
        except Exception:
            # Defensive: a malformed/expired/forged token here must NOT 500 —
            # we silently fall through to IP-bucketing for this request.
            pass
    return f"ip:{get_remote_address(request)}"


limiter = Limiter(
    key_func=user_id_or_ip_key,
    enabled=get_settings().rate_limit_enabled,
    headers_enabled=True,  # X-RateLimit-Limit/Remaining/Reset on every response
)
