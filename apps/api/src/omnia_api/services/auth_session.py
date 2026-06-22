"""Establish a real Auth.js (NextAuth v5) session in the generated app and
return a Playwright storage_state for the gate legs to replay (Area C, DARK).

The session is the genuine credentials login (CSRF → callback/credentials),
so the encrypted JWE cookie is decryptable by the app's own ``auth()`` — the
middleware cookie-probe AND every page's ``requireUser()`` both accept it. No
forged token, no auth-floor bypass.

The seed operator's password is NOT stored anywhere: it is re-derived from the
per-project ``AUTH_SECRET`` (HMAC), byte-identical to what the template's
``scripts/init-db.mjs`` writes at provision (Node
``crypto.createHmac('sha256', AUTH_SECRET).update('omnia-gate-seed-v1')
.digest('base64url').slice(0, 24)``). The worker fetches ``AUTH_SECRET`` from
the orchestrator and re-derives the same plaintext here — zero password storage.

Fail-soft throughout (R-10): any hiccup (no CSRF, login rejected, render error)
returns ``None`` so the caller can transparently fall back to the anonymous
route-probe; a login failure never blocks a build.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
from typing import Any

log = logging.getLogger(__name__)


def derive_seed_password(auth_secret: str) -> str:
    """Re-derive the seed operator's plaintext password from ``AUTH_SECRET``.

    Mirrors the template's ``init-db.mjs`` byte-for-byte: HMAC-SHA256 keyed by
    ``AUTH_SECRET`` over the fixed message ``b"omnia-gate-seed-v1"``, base64url
    of the digest, padding stripped, first 24 chars. Node's ``base64url`` emits
    no ``=`` padding, so :func:`base64.urlsafe_b64encode` + ``rstrip("=")``
    produces the identical string before the ``[:24]`` slice."""
    mac = hmac.new(auth_secret.encode(), b"omnia-gate-seed-v1", hashlib.sha256).digest()
    return base64.urlsafe_b64encode(mac).decode().rstrip("=")[:24]


async def establish_session(
    base_url: str, email: str, auth_secret: str, *, timeout_ms: int = 15_000
) -> dict[str, Any] | None:
    """Log the seed operator in and return a storage_state dict, or ``None``.

    Drives ``/api/auth/csrf`` then POST ``/api/auth/callback/credentials``,
    mirroring what the signin server action does, and snapshots the resulting
    cookies. Returns the Playwright ``storage_state`` ONLY when a session cookie
    actually landed (``*session-token*``); any hiccup → ``None`` (fail-soft,
    R-10) so the caller falls back to the anonymous path."""
    password = derive_seed_password(auth_secret)
    base = base_url.rstrip("/")
    try:
        from playwright.async_api import async_playwright

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            try:
                context = await browser.new_context()
                try:
                    # 1) CSRF token (NextAuth requires it on the callback)
                    csrf_resp = await context.request.get(
                        f"{base}/api/auth/csrf", timeout=timeout_ms
                    )
                    csrf = (await csrf_resp.json()).get("csrfToken")
                    if not csrf:
                        log.warning("auth_session: no csrfToken (abstain)")
                        return None
                    # 2) credentials callback — sets the session cookie on success
                    await context.request.post(
                        f"{base}/api/auth/callback/credentials",
                        form={
                            "csrfToken": csrf,
                            "email": email,
                            "password": password,
                            "callbackUrl": f"{base}/dashboard",
                        },
                        timeout=timeout_ms,
                    )
                    state = await context.storage_state()
                    # success iff a session cookie landed
                    names = {c.get("name") for c in state.get("cookies", [])}
                    if not any(n and "session-token" in n for n in names):
                        log.warning("auth_session: no session cookie after login")
                        return None
                    return state
                finally:
                    await context.close()
            finally:
                await browser.close()
    except Exception as exc:  # render/login hiccup — abstain, fall back anonymous
        log.warning("auth_session: establish failed (abstain): %r", exc)
        return None


__all__ = ["derive_seed_password", "establish_session"]
