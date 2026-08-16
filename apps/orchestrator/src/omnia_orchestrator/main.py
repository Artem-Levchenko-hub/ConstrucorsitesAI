"""Omnia.AI V2 — Orchestrator FastAPI app (:8003).

Single entry point. apps/api talks to us over internal HTTP with a shared
secret in `X-Internal-Token`. Web clients never reach this surface.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError

from omnia_orchestrator.core.errors import (
    OrchestratorError,
    orchestrator_error_handler,
    unhandled_error_handler,
)
from omnia_orchestrator.core.sentry import init_sentry
from omnia_orchestrator.routers import build_exe, byo, health, ingress, runtime
from omnia_orchestrator.services import nginx_writer
from omnia_orchestrator.services.hibernate import (
    start_hibernate_loop,
    stop_hibernate_loop,
)

_log = structlog.get_logger("omnia_orchestrator.main")


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    # Upgrade vhosts provisioned before wake-on-request landed. Fail-soft: a
    # broken render rolls back and never takes the shared nginx down.
    try:
        await nginx_writer.refresh_vhosts()
    except Exception as exc:  # never block startup on a best-effort migration
        _log.warning("startup.refresh_vhosts_failed", err=str(exc))
    await start_hibernate_loop()
    try:
        yield
    finally:
        await stop_hibernate_loop()


def create_app() -> FastAPI:
    # Sentry first — so any FastAPI / Starlette integration patches happen
    # before routers are registered. No-op when SENTRY_DSN is unset.
    init_sentry()
    app = FastAPI(
        title="Omnia.AI Orchestrator",
        version="0.0.1",
        lifespan=lifespan,
        # Internal API — no auto-generated public docs/openapi in prod.
        docs_url="/internal/docs",
        redoc_url=None,
        openapi_url="/internal/openapi.json",
    )

    app.add_exception_handler(OrchestratorError, orchestrator_error_handler)
    app.add_exception_handler(RequestValidationError, unhandled_error_handler)
    app.add_exception_handler(Exception, unhandled_error_handler)

    app.include_router(health.router)
    app.include_router(runtime.router)
    app.include_router(ingress.router)
    app.include_router(build_exe.router)
    app.include_router(byo.router)

    return app


app = create_app()
