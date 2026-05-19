"""Sentry init for the LLM gateway.

Mirror of apps/api/core/sentry.py — kept separate because the two services
deploy independently and we don't want a shared lib. Off by default.
"""

from __future__ import annotations

from typing import Any

import structlog

from omnia_gateway.core.config import get_settings

_SENSITIVE_HEADERS = {"authorization", "cookie", "set-cookie", "x-api-key"}
_SENSITIVE_BODY_KEYS = {"password", "token", "secret", "api_key"}
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
            cookies[k] = _REDACTED
    data = request.get("data")
    if isinstance(data, dict):
        for k in list(data.keys()):
            if k.lower() in _SENSITIVE_BODY_KEYS:
                data[k] = _REDACTED
        # Also: messages[] often contains the user's prompt — that's fine to
        # send (it's the thing failing), but model API responses can echo keys.
        # We do NOT scrub messages content here, on purpose. If you change your
        # mind, gate it on env or a config flag.
    return event


def init_sentry() -> None:
    settings = get_settings()
    log = structlog.get_logger("omnia_gateway.sentry")
    dsn = settings.sentry_dsn.get_secret_value() if settings.sentry_dsn else None
    if not dsn:
        log.info("sentry.disabled", reason="SENTRY_DSN empty")
        return

    import sentry_sdk
    from sentry_sdk.integrations.asyncio import AsyncioIntegration
    from sentry_sdk.integrations.fastapi import FastApiIntegration
    from sentry_sdk.integrations.starlette import StarletteIntegration

    sentry_sdk.init(
        dsn=dsn,
        environment=settings.env,
        traces_sample_rate=settings.sentry_traces_sample_rate,
        profiles_sample_rate=settings.sentry_profiles_sample_rate,
        send_default_pii=False,
        before_send=_scrub,
        integrations=[
            FastApiIntegration(),
            StarletteIntegration(),
            AsyncioIntegration(),
        ],
    )
    log.info("sentry.initialized", env=settings.env)
