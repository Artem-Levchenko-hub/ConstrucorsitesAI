"""Yleum V2 вЂ” Orchestrator FastAPI app (:8003).

Single entry point. apps/api talks to us over internal HTTP with a shared
secret in `X-Internal-Token`. Web clients never reach this surface.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress

import structlog
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError

from yleum_orchestrator.core.cell_resources import WorkspaceLockTimeout
from yleum_orchestrator.core.config import get_settings
from yleum_orchestrator.core.errors import (
    OrchestratorError,
    orchestrator_error_handler,
    unhandled_error_handler,
    workspace_busy_handler,
)
from yleum_orchestrator.core.sentry import init_sentry
from yleum_orchestrator.routers import (
    cell_publication,
    code_restorations,
    health,
    ingress,
    publication_artifacts,
    runtime,
    workspace,
)
from yleum_orchestrator.services import generation_reader_inventory as readers
from yleum_orchestrator.services import nginx_writer
from yleum_orchestrator.services.cell_reservation_recovery import (
    recover_workspace_provider_capacity,
)
from yleum_orchestrator.services.hibernate import (
    start_hibernate_loop,
    stop_hibernate_loop,
)

_log = structlog.get_logger("yleum_orchestrator.main")


@asynccontextmanager
async def _operational_lifespan(_app: FastAPI) -> AsyncIterator[None]:
    # Upgrade vhosts provisioned before wake-on-request landed. Fail-soft: a
    # broken render rolls back and never takes the shared nginx down.
    try:
        await asyncio.to_thread(readers.require_if_configured)
        await nginx_writer.refresh_vhosts()
    except Exception as exc:  # never block startup on a best-effort migration
        _log.warning("startup.refresh_vhosts_failed", err=str(exc))
    try:
        await asyncio.to_thread(readers.require_if_configured)
        await recover_workspace_provider_capacity(get_settings())
    except Exception as exc:
        # Recovery is fail-closed: an unreadable ledger is preserved and new
        # admission will continue to account for it.
        _log.warning("startup.capacity_reservation_recovery_failed", err=str(exc))
    try:
        from yleum_orchestrator.services.restoration_adaptation_workspace import (
            get_restoration_adaptation_workspace_service,
        )

        await asyncio.to_thread(readers.require_if_configured)
        await get_restoration_adaptation_workspace_service().recover()
    except Exception as exc:
        _log.warning("startup.restoration_adaptation_recovery_failed", err=str(exc))
    try:
        from yleum_orchestrator.services.restoration_adaptation_activation_service import (
            get_restoration_adaptation_activation_service,
        )

        await asyncio.to_thread(readers.require_if_configured)
        batch = await get_restoration_adaptation_activation_service().recover_all()
        for failure in batch.failures:
            _log.warning(
                "startup.restoration_adaptation_activation_journal_failed",
                **failure.model_dump(),
            )
    except Exception as exc:
        _log.warning("startup.restoration_adaptation_activation_recovery_failed", err=str(exc))
    await asyncio.to_thread(readers.require_if_configured)
    await start_hibernate_loop()
    from yleum_orchestrator.services.cell_publication import start_publication_recovery

    publication_recovery = start_publication_recovery()
    from yleum_orchestrator.services.code_restorations import start_restoration_recovery

    restoration_recovery = start_restoration_recovery()
    from yleum_orchestrator.services.restoration_adaptation_workspace import (
        start_restoration_adaptation_recovery,
    )

    adaptation_recovery = start_restoration_adaptation_recovery()
    from yleum_orchestrator.services.restoration_adaptation_activation_service import (
        start_restoration_adaptation_activation_recovery,
    )

    adaptation_activation_recovery = start_restoration_adaptation_activation_recovery()
    try:
        yield
    finally:
        publication_recovery.cancel()
        restoration_recovery.cancel()
        adaptation_recovery.cancel()
        adaptation_activation_recovery.cancel()
        await asyncio.gather(
            publication_recovery,
            restoration_recovery,
            adaptation_recovery,
            adaptation_activation_recovery,
            return_exceptions=True,
        )
        from yleum_orchestrator.services.code_restorations import get_code_restoration_service

        await get_code_restoration_service().close()
        await stop_hibernate_loop()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    async with readers.reader_lifecycle():
        if not get_settings().generation_recovery_inventory_dir:
            async with _operational_lifespan(app):
                yield
            return

        async def deferred_operations() -> None:
            try:
                await readers.wait_ready()
                async with _operational_lifespan(app):
                    await asyncio.Event().wait()
            except Exception as exc:
                await readers.revoke_reader()
                _log.error("recovery_startup_failed", error_type=type(exc).__name__)

        # Liveness and registration precede compatibility; no recovery callback
        # runs while the other release readers are still being replaced.
        operations = asyncio.create_task(deferred_operations())
        try:
            yield
        finally:
            operations.cancel()
            with suppress(asyncio.CancelledError):
                await operations


def create_app() -> FastAPI:
    # Sentry first вЂ” so any FastAPI / Starlette integration patches happen
    # before routers are registered. No-op when SENTRY_DSN is unset.
    init_sentry()
    app = FastAPI(
        title="Yleum Orchestrator",
        version="0.0.1",
        lifespan=lifespan,
        # Internal API вЂ” no auto-generated public docs/openapi in prod.
        docs_url="/internal/docs",
        redoc_url=None,
        openapi_url="/internal/openapi.json",
    )

    app.add_exception_handler(OrchestratorError, orchestrator_error_handler)
    app.add_exception_handler(WorkspaceLockTimeout, workspace_busy_handler)
    app.add_exception_handler(RequestValidationError, unhandled_error_handler)
    app.add_exception_handler(Exception, unhandled_error_handler)

    app.include_router(health.router)
    app.include_router(runtime.router)
    app.include_router(ingress.router)
    app.include_router(workspace.router)
    app.include_router(cell_publication.router)
    app.include_router(publication_artifacts.router)
    app.include_router(code_restorations.router)

    return app


app = create_app()
