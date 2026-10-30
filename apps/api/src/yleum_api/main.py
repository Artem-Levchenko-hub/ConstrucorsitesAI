import asyncio
import logging
import os
import signal
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from yleum_api.core.config import get_settings
from yleum_api.core.db import dispose_engine, get_engine
from yleum_api.core.errors import (
    ApiError,
    api_error_handler,
    unhandled_error_handler,
    validation_error_handler,
)
from yleum_api.core.minio import reconcile_public_bucket_policies
from yleum_api.core.redis import dispose_redis
from yleum_api.core.release import normalize_release_sha
from yleum_api.core.request_origin import CookieOriginMiddleware
from yleum_api.routers import account as account_router
from yleum_api.routers import account_credentials as account_credentials_router
from yleum_api.routers import admin as admin_router
from yleum_api.routers import amocrm as amocrm_router
from yleum_api.routers import app_integrations as app_integrations_router
from yleum_api.routers import auth as auth_router
from yleum_api.routers import auth_oauth as auth_oauth_router
from yleum_api.routers import backups as backups_router
from yleum_api.routers import billing as billing_router
from yleum_api.routers import draft_changes as draft_changes_router
from yleum_api.routers import generation_response_capacity as generation_response_capacity_router
from yleum_api.routers import integration_orders as integration_orders_router
from yleum_api.routers import integration_runtime as integration_runtime_router
from yleum_api.routers import max_accounts as max_accounts_router
from yleum_api.routers import max_analytics as max_analytics_router
from yleum_api.routers import max_integrations as max_integrations_router
from yleum_api.routers import max_management as max_management_router
from yleum_api.routers import max_studio as max_studio_router
from yleum_api.routers import messages as messages_router
from yleum_api.routers import models_router
from yleum_api.routers import moysklad_vendor as moysklad_vendor_router
from yleum_api.routers import payments as payments_router
from yleum_api.routers import product_advice as product_advice_router
from yleum_api.routers import project_versions as project_versions_router
from yleum_api.routers import projects as projects_router
from yleum_api.routers import public as public_router
from yleum_api.routers import publication_migrations as publication_migrations_router
from yleum_api.routers import restorations as restorations_router
from yleum_api.routers import rollback as rollback_router
from yleum_api.routers import runtime as runtime_router
from yleum_api.routers import snapshots as snapshots_router
from yleum_api.routers import support as support_router
from yleum_api.routers import task_board as task_board_router
from yleum_api.routers import transcribe as transcribe_router
from yleum_api.routers import wallet as wallet_router
from yleum_api.routers import ws as ws_router
from yleum_api.services import readiness
from yleum_api.services.generation.supervisor import resume_capacity_queued_generations
from yleum_api.services.generation_runs import recover_interrupted_generation_runs
from yleum_api.services.project_cell_capacity import advance_terminal_generation_cleanup
from yleum_api.services.project_cell_runtime import advance_owner_wake_operations
from yleum_api.services.project_cells import recover_interrupted_cell_operations
from yleum_api.services.support_realtime import support_hub
from yleum_api.services.ws_hub import hub

logger = logging.getLogger(__name__)


async def _reader_compatibility_gate() -> None:
    from yleum_api.services.generation.recovery_readers import wait_for_readers

    await wait_for_readers()


async def _monitor_capacity_queued_generations() -> None:
    """Reclaim expired durable dispatch leases after rolling restarts/crashes."""

    while True:
        await asyncio.sleep(10)
        try:
            await _reader_compatibility_gate()
            resumed = await resume_capacity_queued_generations()
        except Exception:
            logger.exception("generation capacity queue recovery scan failed")
            continue
        if resumed:
            logger.warning(
                "resumed expired capacity-queued generation dispatches",
                extra={"generation_run_count": resumed},
            )


async def _monitor_owner_wake_operations() -> None:
    """Advance owner preview wakes without blocking generation admission recovery."""

    while True:
        await asyncio.sleep(10)
        try:
            await _reader_compatibility_gate()
            advanced = await advance_owner_wake_operations()
        except Exception:
            logger.exception("owner preview capacity queue recovery scan failed")
            continue
        if advanced:
            logger.warning(
                "advanced capacity-queued owner preview wakes",
                extra={"project_cell_operation_count": advanced},
            )


async def _monitor_terminal_generation_cleanup() -> None:
    """Retry retained activities independently of generation and owner admission."""
    while True:
        await asyncio.sleep(10)
        try:
            await _reader_compatibility_gate()
            await advance_terminal_generation_cleanup()
        except Exception:
            logger.exception("terminal generation cleanup scan failed")


_BACKGROUND_CLEANUP_SECONDS = 5


@asynccontextmanager
async def _background_services(
    *,
    on_failure: Callable[[], None] | None = None,
) -> AsyncIterator[None]:
    initialized = False
    monitors: list[asyncio.Task[None]] = []
    try:
        await _reader_compatibility_gate()
        await asyncio.to_thread(reconcile_public_bucket_policies)
        get_engine()
        initialized = True
        await _reader_compatibility_gate()
        recovered = await recover_interrupted_generation_runs()
        if recovered:
            logger.warning(
                "finalised interrupted generation runs after API restart",
                extra={"generation_run_count": recovered},
            )
        await _reader_compatibility_gate()
        recovered_cells = await recover_interrupted_cell_operations()
        if recovered_cells:
            logger.warning(
                "finalised interrupted Project Cell operations after API restart",
                extra={"project_cell_operation_count": recovered_cells},
            )
        await hub.start_listener()
        await support_hub.start()
        await _reader_compatibility_gate()
        resumed = await resume_capacity_queued_generations()
        if resumed:
            logger.warning(
                "resumed capacity-queued generation runs after API restart",
                extra={"generation_run_count": resumed},
            )
        monitors = [
            asyncio.create_task(_monitor_capacity_queued_generations()),
            asyncio.create_task(_monitor_owner_wake_operations()),
            asyncio.create_task(_monitor_terminal_generation_cleanup()),
        ]
        yield
    except Exception:
        # A configured API already serves liveness while admission waits. Stop
        # it on a fatal startup error before any potentially blocked cleanup.
        if on_failure is not None:
            on_failure()
        raise
    finally:
        if initialized:
            async with asyncio.timeout(
                _BACKGROUND_CLEANUP_SECONDS if on_failure is not None else None
            ):
                for task in monitors:
                    task.cancel()
                for task in monitors:
                    with suppress(asyncio.CancelledError):
                        await task
                await hub.stop_listener()
                await support_hub.stop()
                await dispose_redis()
                await dispose_engine()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    from yleum_api.services.generation.recovery_readers import (
        reader_lifecycle,
        wait_for_readers,
    )

    async with reader_lifecycle("api", wait=False):
        if not get_settings().generation_recovery_inventory_dir:
            async with _background_services():
                yield
            return

        stopping = False
        failed = False

        def fail_stop() -> None:
            nonlocal failed
            if not stopping and not failed:
                failed = True
                app.state.background_services_failed = True
                os.kill(os.getpid(), signal.SIGTERM)

        async def admitted_background() -> None:
            try:
                await wait_for_readers()
                async with _background_services(on_failure=fail_stop):
                    await asyncio.Event().wait()
            except Exception:
                # Also cover gate/cleanup failure; consume the task exception
                # now, not for the first time during lifespan shutdown.
                fail_stop()
                logger.exception("API background startup failed; process stop requested")

        # Liveness must work while the release replaces the other readers.
        # No recovery callback runs until the full target inventory is ready.
        background = asyncio.create_task(admitted_background())
        try:
            yield
        finally:
            stopping = True
            background.cancel()
            with suppress(asyncio.CancelledError):
                await background


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="Yleum Backend", version="0.0.1", lifespan=lifespan)

    app.add_middleware(CookieOriginMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.add_exception_handler(ApiError, api_error_handler)
    app.add_exception_handler(RequestValidationError, validation_error_handler)
    app.add_exception_handler(Exception, unhandled_error_handler)

    app.include_router(auth_router.router)
    app.include_router(auth_oauth_router.router)
    app.include_router(backups_router.router)
    app.include_router(billing_router.router)
    app.include_router(admin_router.router)
    app.include_router(support_router.router)
    app.include_router(account_router.router)
    app.include_router(account_credentials_router.router)
    app.include_router(app_integrations_router.router)
    app.include_router(amocrm_router.router)
    app.include_router(moysklad_vendor_router.router)
    app.include_router(integration_runtime_router.router)
    app.include_router(integration_orders_router.router)
    app.include_router(max_analytics_router.router)
    app.include_router(account_router.legal_router)
    app.include_router(projects_router.router)
    app.include_router(product_advice_router.router)
    app.include_router(snapshots_router.router)
    app.include_router(project_versions_router.router)
    app.include_router(restorations_router.router)
    app.include_router(draft_changes_router.router)
    app.include_router(messages_router.router)
    app.include_router(max_integrations_router.router)
    app.include_router(max_accounts_router.router)
    app.include_router(max_studio_router.router)
    app.include_router(max_management_router.router)
    app.include_router(publication_migrations_router.router)
    app.include_router(rollback_router.router)
    app.include_router(task_board_router.router)
    app.include_router(transcribe_router.router)
    app.include_router(runtime_router.router)
    app.include_router(wallet_router.router)
    app.include_router(models_router.router)
    app.include_router(generation_response_capacity_router.router)
    app.include_router(payments_router.router)
    app.include_router(public_router.kit_router)
    app.include_router(ws_router.router)

    @app.get("/health", tags=["meta"])
    async def health() -> dict[str, str]:
        return {
            "status": "ok",
            "release_sha": normalize_release_sha(get_settings().omnia_release_sha),
        }

    @app.get("/api/health", tags=["meta"])
    async def readiness_health() -> JSONResponse:
        report = await readiness.probe_readiness()
        if getattr(app.state, "background_services_failed", False):
            report.checks["background_services"] = "failed"
        healthy = all(value == "ok" for value in report.checks.values())
        return JSONResponse(
            status_code=200 if healthy else 503,
            content={
                "status": "ok" if healthy else "degraded",
                "service": "api",
                "release_sha": normalize_release_sha(get_settings().omnia_release_sha),
                "checks": report.checks,
                "dependencies": report.dependencies,
            },
        )

    return app


app = create_app()
