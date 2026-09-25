import asyncio
import logging
from collections.abc import AsyncIterator
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
from yleum_api.core.redis import dispose_redis
from yleum_api.core.release import normalize_release_sha
from yleum_api.routers import account as account_router
from yleum_api.routers import admin as admin_router
from yleum_api.routers import app_integrations as app_integrations_router
from yleum_api.routers import auth as auth_router
from yleum_api.routers import auth_oauth as auth_oauth_router
from yleum_api.routers import backups as backups_router
from yleum_api.routers import billing as billing_router
from yleum_api.routers import deploy_targets as deploy_targets_router
from yleum_api.routers import domains as domains_router
from yleum_api.routers import draft_changes as draft_changes_router
from yleum_api.routers import integration_runtime as integration_runtime_router
from yleum_api.routers import max_accounts as max_accounts_router
from yleum_api.routers import max_integrations as max_integrations_router
from yleum_api.routers import max_studio as max_studio_router
from yleum_api.routers import messages as messages_router
from yleum_api.routers import models_router
from yleum_api.routers import payments as payments_router
from yleum_api.routers import product_advice as product_advice_router
from yleum_api.routers import project_versions as project_versions_router
from yleum_api.routers import projects as projects_router
from yleum_api.routers import public as public_router
from yleum_api.routers import restorations as restorations_router
from yleum_api.routers import rollback as rollback_router
from yleum_api.routers import runtime as runtime_router
from yleum_api.routers import snapshots as snapshots_router
from yleum_api.routers import task_board as task_board_router
from yleum_api.routers import transcribe as transcribe_router
from yleum_api.routers import wallet as wallet_router
from yleum_api.routers import ws as ws_router
from yleum_api.services import readiness
from yleum_api.services.generation.supervisor import resume_capacity_queued_generations
from yleum_api.services.generation_runs import recover_interrupted_generation_runs
from yleum_api.services.project_cell_runtime import advance_owner_wake_operations
from yleum_api.services.project_cells import recover_interrupted_cell_operations
from yleum_api.services.ws_hub import hub

logger = logging.getLogger(__name__)


async def _monitor_capacity_queued_generations() -> None:
    """Reclaim expired durable dispatch leases after rolling restarts/crashes."""

    while True:
        await asyncio.sleep(10)
        try:
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
            advanced = await advance_owner_wake_operations()
        except Exception:
            logger.exception("owner preview capacity queue recovery scan failed")
            continue
        if advanced:
            logger.warning(
                "advanced capacity-queued owner preview wakes",
                extra={"project_cell_operation_count": advanced},
            )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    get_engine()
    recovered = await recover_interrupted_generation_runs()
    if recovered:
        logger.warning(
            "finalised interrupted generation runs after API restart",
            extra={"generation_run_count": recovered},
        )
    recovered_cells = await recover_interrupted_cell_operations()
    if recovered_cells:
        logger.warning(
            "finalised interrupted Project Cell operations after API restart",
            extra={"project_cell_operation_count": recovered_cells},
        )
    await hub.start_listener()
    resumed = await resume_capacity_queued_generations()
    if resumed:
        logger.warning(
            "resumed capacity-queued generation runs after API restart",
            extra={"generation_run_count": resumed},
        )
    capacity_monitor = asyncio.create_task(_monitor_capacity_queued_generations())
    owner_wake_monitor = asyncio.create_task(_monitor_owner_wake_operations())
    try:
        yield
    finally:
        capacity_monitor.cancel()
        owner_wake_monitor.cancel()
        with suppress(asyncio.CancelledError):
            await capacity_monitor
        with suppress(asyncio.CancelledError):
            await owner_wake_monitor
        await hub.stop_listener()
        await dispose_redis()
        await dispose_engine()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="Yleum Backend", version="0.0.1", lifespan=lifespan)

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
    app.include_router(account_router.router)
    app.include_router(app_integrations_router.router)
    app.include_router(integration_runtime_router.router)
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
    app.include_router(rollback_router.router)
    app.include_router(task_board_router.router)
    app.include_router(transcribe_router.router)
    app.include_router(runtime_router.router)
    app.include_router(deploy_targets_router.router)
    app.include_router(domains_router.router)
    app.include_router(wallet_router.router)
    app.include_router(models_router.router)
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
