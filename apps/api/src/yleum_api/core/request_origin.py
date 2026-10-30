"""Exact browser-origin checks for cookie-authenticated HTTP and WebSocket requests."""

import re
from urllib.parse import urlsplit

from fastapi import Request
from starlette.datastructures import Headers
from starlette.requests import cookie_parser
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from yleum_api.core.config import get_settings
from yleum_api.core.errors import ApiError

_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
_ORIGIN = re.compile(r"https?://(?:[A-Za-z0-9._-]+|\[[0-9a-fA-F:.]+\])(?::[0-9]{1,5})?")
_FORBIDDEN_MESSAGE = "Недопустимый источник запроса"


def _normalized_origin(value: str) -> str | None:
    """Parse an HTTP(S) origin without accepting paths, userinfo or header lists."""
    if _ORIGIN.fullmatch(value) is None:
        return None
    try:
        parsed = urlsplit(value)
        hostname, port = parsed.hostname, parsed.port
    except ValueError:
        return None
    if not hostname or port == 0:
        return None
    authority = f"[{hostname}]" if ":" in hostname else hostname
    if port is not None and (parsed.scheme, port) not in {("http", 80), ("https", 443)}:
        authority = f"{authority}:{port}"
    return f"{parsed.scheme}://{authority}"


def allowed_origin(origin: str | None) -> bool:
    """Trust one browser-serialized origin from deployment configuration, never Host."""
    if origin is None or _normalized_origin(origin) != origin:
        return False
    settings = get_settings()
    configured = [settings.web_base_url, *settings.cors_origins_list]
    return origin in {
        normalized
        for value in configured
        if (normalized := _normalized_origin(value.strip().removesuffix("/"))) is not None
    }


def request_origin_allowed(headers: Headers) -> bool:
    """Duplicate Origin fields are ambiguous even when their values are identical."""
    origins = headers.getlist("origin")
    return len(origins) == 1 and allowed_origin(origins[0])


def require_origin(request: Request) -> None:
    if not request_origin_allowed(request.headers):
        raise ApiError("forbidden", _FORBIDDEN_MESSAGE, 403)


def has_session_cookie(headers: Headers) -> bool:
    """Do not let duplicate empty cookies or a Bearer header hide a browser session."""
    # Existing FastAPI Cookie parameters use omnia_session; issuance also supports
    # the configurable name. Both must be protected while that contract exists.
    names = {"omnia_session", get_settings().jwt_cookie_name}
    return any(
        name in names and bool(value)
        for field in headers.getlist("cookie")
        for chunk in field.split(";")
        for name, value in cookie_parser(chunk).items()
    )


class CookieOriginMiddleware:
    """Reject unsafe cookie requests before body parsing, persistence or dispatch."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope["method"].upper() not in _SAFE_METHODS:
            headers = Headers(scope=scope)
            if has_session_cookie(headers) and not request_origin_allowed(headers):
                response = JSONResponse(
                    {"error": {"code": "forbidden", "message": _FORBIDDEN_MESSAGE}},
                    status_code=403,
                    headers={"Cache-Control": "no-store"},
                )
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)
