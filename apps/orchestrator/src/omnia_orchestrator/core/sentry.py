"""Sentry SDK initialisation — opt-in via SENTRY_DSN env.

Closes the day 14-15 polish item from `agents/AGENT-D-ORCHESTRATOR.md`.
The orchestrator's surface is small (FastAPI on :8003) but the failure
modes are gnarly — Docker SDK timeouts, ACME shell-outs, Redis pub-sub
drops — and journalctl alone doesn't aggregate them across restarts.

Design:
- No-op when `SENTRY_DSN` is empty / unset → safe to call unconditionally
  from `main.create_app()` (no exception, no log noise).
- FastAPI integration auto-captures unhandled exceptions and 5xx responses
  from request handlers.
- `traces_sample_rate` comes from settings (default 0.1) so prod can dial
  performance sampling up/down without code edits.
- `env` tag mirrors `Settings.env` ("dev" | "prod") for filtering.
"""

from __future__ import annotations

import structlog

from omnia_orchestrator.core.config import get_settings

log = structlog.get_logger("omnia_orchestrator.sentry")


def init_sentry() -> None:
    """Initialise Sentry if a DSN is configured. Otherwise no-op.

    Idempotent — re-calling will just re-init the SDK; sentry handles
    that internally without losing the existing scope.
    """
    settings = get_settings()
    dsn_secret = settings.sentry_dsn
    dsn = dsn_secret.get_secret_value() if dsn_secret is not None else ""
    if not dsn:
        log.info("sentry.disabled", reason="no DSN configured")
        return

    # Lazy import: sentry_sdk pulled in only when wiring this up — keeps
    # module-load cost zero for the no-DSN dev path.
    import sentry_sdk
    from sentry_sdk.integrations.fastapi import FastApiIntegration
    from sentry_sdk.integrations.starlette import StarletteIntegration

    sentry_sdk.init(
        dsn=dsn,
        environment=settings.env,
        traces_sample_rate=settings.sentry_traces_sample_rate,
        # send_default_pii=False — orchestrator handles project ids, container
        # names, internal tokens; none of which we want in error envelopes
        # without explicit `set_extra`.
        send_default_pii=False,
        integrations=[
            FastApiIntegration(),
            StarletteIntegration(),
        ],
        # Release tag — fed by the deploy pipeline; falls back to "dev" for
        # local runs.
        release=f"omnia-orchestrator@{settings.env}",
    )
    log.info(
        "sentry.initialised",
        environment=settings.env,
        sample_rate=settings.sentry_traces_sample_rate,
    )
