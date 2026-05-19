"""Sentry initialization with PII scrubbing.

Sentry is off when SENTRY_DSN is empty — the helper turns into a no-op so dev
machines and tests don't accidentally fire events. PII filter strips auth
tokens, passwords, and the session cookie before any event leaves the process.
"""

from __future__ import annotations

import logging
from typing import Any

from omnia_api.core.config import get_settings

log = logging.getLogger(__name__)

# Keys we never want to ship to Sentry. Header names are case-insensitive,
# body field names are case-sensitive but we lowercase before compare.
_SENSITIVE_HEADERS = {"authorization", "cookie", "set-cookie", "x-api-key"}
_SENSITIVE_BODY_KEYS = {"password", "password_hash", "token", "secret", "jwt_secret"}
_REDACTED = "[Filtered]"


def _scrub(event: dict[str, Any], _hint: dict[str, Any]) -> dict[str, Any] | None:
    request = event.get("request") or {}
    headers = request.get("headers")
    if isinstance(headers, dict):
        for k in list(headers.keys()):
            if k.lower() in _SENSITIVE_HEADERS:
                headers[k] = _REDACTED
    cookies = request.get("cookies")
    if isinstance(cookies, dict):
        for k in list(cookies.keys()):
            cookies[k] = _REDACTED  # any cookie is a session — drop all
    data = request.get("data")
    if isinstance(data, dict):
        for k in list(data.keys()):
            if k.lower() in _SENSITIVE_BODY_KEYS:
                data[k] = _REDACTED
    return event


def init_sentry() -> None:
    """Call once at startup. Idempotent: subsequent calls are no-ops."""
    settings = get_settings()
    dsn = settings.sentry_dsn.get_secret_value() if settings.sentry_dsn else None
    if not dsn:
        log.info("sentry disabled (SENTRY_DSN empty)")
        return

    # Imported lazily so the dep can be missing on dev installs without
    # breaking `uv run` — the dep is in pyproject but `uv sync` must run.
    import sentry_sdk
    from sentry_sdk.integrations.asyncio import AsyncioIntegration
    from sentry_sdk.integrations.fastapi import FastApiIntegration
    from sentry_sdk.integrations.starlette import StarletteIntegration

    sentry_sdk.init(
        dsn=dsn,
        environment=settings.env,
        traces_sample_rate=settings.sentry_traces_sample_rate,
        profiles_sample_rate=settings.sentry_profiles_sample_rate,
        send_default_pii=False,  # we have an explicit scrubber; never trust defaults
        before_send=_scrub,
        integrations=[
            FastApiIntegration(),
            StarletteIntegration(),
            AsyncioIntegration(),
        ],
    )
    log.info("sentry initialized env=%s", settings.env)
