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


def preview_resolver_args() -> list[str]:
    """Chromium launch args that let a gate browser reach a generated app's PUBLIC
    preview host from inside the worker network (Area C, b2). Returns a
    ``--host-resolver-rules`` arg from ``gate_preview_resolver_rules`` (e.g. "MAP
    *.preview.lead-generator.ru 172.21.0.1") so the headless browser resolves the
    canonical https preview host to the docker-gateway IP where the host nginx
    listens — nginx terminates TLS and proxies to the container as https, so the
    app sees its canonical AUTH_URL origin and secure cookies work. Empty setting →
    no arg (authenticated path off; renders use the URL as-is). Import-cycle-free:
    a lazy get_settings read, no module-level config import."""
    from omnia_api.core.config import get_settings

    rules = (get_settings().gate_preview_resolver_rules or "").strip()
    return [f"--host-resolver-rules={rules}"] if rules else []


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
            browser = await p.chromium.launch(
                headless=True, args=preview_resolver_args()
            )
            try:
                context = await browser.new_context()
                try:
                    page = await context.new_page()
                    # The login MUST go through the renderer's network, NOT a
                    # Playwright APIRequestContext: ``--host-resolver-rules`` (b2,
                    # how the worker reaches the app's canonical https preview host)
                    # is honoured by page navigations + in-page fetch, but NOT by
                    # context.request. So we navigate for the CSRF token and POST the
                    # credentials callback via an in-page fetch (same-origin → carries
                    # the csrf cookie the navigation set).
                    # 1) CSRF token (NextAuth requires it on the callback)
                    csrf_resp = await page.goto(
                        f"{base}/api/auth/csrf", timeout=timeout_ms
                    )
                    csrf = (await csrf_resp.json()).get("csrfToken") if csrf_resp else None
                    if not csrf:
                        log.warning("auth_session: no csrfToken (abstain)")
                        return None
                    # 2) credentials callback (in-page fetch) — sets the session cookie
                    await page.evaluate(
                        """async ([csrf, email, password, cb]) => {
                            const body = new URLSearchParams({
                                csrfToken: csrf, email, password, callbackUrl: cb,
                            });
                            await fetch('/api/auth/callback/credentials', {
                                method: 'POST',
                                headers: {'content-type': 'application/x-www-form-urlencoded'},
                                body: body.toString(),
                                redirect: 'follow',
                            });
                        }""",
                        [csrf, email, password, f"{base}/dashboard"],
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


__all__ = ["derive_seed_password", "establish_session", "preview_resolver_args"]
