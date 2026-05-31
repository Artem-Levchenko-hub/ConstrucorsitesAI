from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware

from omnia_api.core.config import get_settings
from omnia_api.core.db import dispose_engine, get_engine
from omnia_api.core.errors import (
    ApiError,
    api_error_handler,
    unhandled_error_handler,
    validation_error_handler,
)
from omnia_api.core.redis import dispose_redis
from omnia_api.routers import auth as auth_router
from omnia_api.routers import design_presets as design_presets_router
from omnia_api.routers import github as github_router
from omnia_api.routers import messages as messages_router
from omnia_api.routers import models_router
from omnia_api.routers import projects as projects_router
from omnia_api.routers import public as public_router
from omnia_api.routers import rollback as rollback_router
from omnia_api.routers import runtime as runtime_router
from omnia_api.routers import snapshots as snapshots_router
from omnia_api.routers import wallet as wallet_router
from omnia_api.routers import ws as ws_router
from omnia_api.services.ws_hub import hub


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    get_engine()
    await hub.start_listener()
    try:
        yield
    finally:
        await hub.stop_listener()
        await dispose_redis()
        await dispose_engine()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="Omnia.AI Backend", version="0.0.1", lifespan=lifespan)

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
    app.include_router(github_router.router)
    app.include_router(projects_router.router)
    app.include_router(snapshots_router.router)
    app.include_router(messages_router.router)
    app.include_router(rollback_router.router)
    app.include_router(runtime_router.router)
    app.include_router(wallet_router.router)
    app.include_router(models_router.router)
    app.include_router(design_presets_router.router)
    app.include_router(public_router.router)
    app.include_router(public_router.kit_router)
    app.include_router(ws_router.router)

    @app.get("/health", tags=["meta"])
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
