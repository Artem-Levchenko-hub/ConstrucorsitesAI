"""Host-only, per-flow OAuth proofs which never leave the initiating browser."""

from __future__ import annotations

import base64
import hashlib
import hmac
from typing import Literal

from fastapi import Request, Response

from yleum_api.core.config import get_settings

Purpose = Literal["state", "ticket"]
MAX_BINDINGS = 8


def _secure() -> bool:
    settings = get_settings()
    return settings.env == "prod" or settings.jwt_cookie_secure


def cookie_name(purpose: Purpose, raw: str) -> str:
    prefix = "__Host-yleum-oauth" if _secure() else "yleum-oauth"
    return f"{prefix}-{purpose}-{hashlib.sha256(raw.encode()).hexdigest()}"


def _proof(purpose: Purpose, provider: str, raw: str) -> str:
    message = "\0".join(("yleum-oauth-browser-v1", purpose, provider, raw)).encode()
    digest = hmac.digest(get_settings().jwt_secret.get_secret_value().encode(), message, "sha256")
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def has_binding(request: Request, purpose: Purpose, provider: str, raw: str) -> bool:
    proof = request.cookies.get(cookie_name(purpose, raw))
    expected = _proof(purpose, provider, raw)
    return proof is not None and hmac.compare_digest(proof.encode(), expected.encode())


def clear_binding(response: Response, purpose: Purpose, raw: str) -> None:
    response.delete_cookie(
        cookie_name(purpose, raw), path="/", secure=_secure(), httponly=True, samesite="lax"
    )
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"


def set_binding(
    request: Request, response: Response, purpose: Purpose, provider: str, raw: str, ttl: int,
    *, replacing: tuple[Purpose, str] | None = None,
) -> None:
    # Cap outstanding browser cookies while preserving independent simultaneous flows.
    # Their values stay out of JSON, URLs, logs and form fields.
    prefix = "__Host-yleum-oauth-" if _secure() else "yleum-oauth-"
    replaced_name = cookie_name(*replacing) if replacing is not None else None
    if replacing is not None:
        clear_binding(response, *replacing)
    old_names = [
        name for name in request.cookies if name.startswith(prefix) and name != replaced_name
    ]
    for name in old_names[: max(0, len(old_names) - MAX_BINDINGS + 1)]:
        response.delete_cookie(name, path="/", secure=_secure(), httponly=True, samesite="lax")
    response.set_cookie(
        cookie_name(purpose, raw), _proof(purpose, provider, raw),
        max_age=ttl, path="/", secure=_secure(), httponly=True, samesite="lax",
    )
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
