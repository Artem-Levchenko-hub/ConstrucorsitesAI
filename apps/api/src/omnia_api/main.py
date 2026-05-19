from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from omnia_api.core.config import get_settings
from omnia_api.core.db import dispose_engine, get_engine
from omnia_api.core.errors import (
    ApiError,
    api_error_handler,
    rate_limit_handler,
    unhandled_error_handler,
    validation_error_handler,
)
from omnia_api.core.rate_limit import limiter
from omnia_api.core.redis import dispose_redis
from omnia_api.core.sentry import init_sentry
from omnia_api.routers import auth as auth_router
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
    # Sentry must be initialized before the FastAPI app is constructed so its
    # integration can patch the exception flow. No-op when SENTRY_DSN is empty.
    init_sentry()

    settings = get_settings()
    app = FastAPI(title="Omnia.AI Backend", version="0.0.1", lifespan=lifespan)

    # Middleware order matters: Starlette wraps `user_middleware` in REVERSE,
    # so the LAST `add_middleware` call becomes the OUTERMOST wrapper. For
    # ProxyHeadersMiddleware to rewrite scope["client"] from X-Forwarded-For
    # BEFORE slowapi keys its bucket, it must be registered LAST. The intended
    # request flow (outer → inner) is:
    #     ProxyHeaders (real IP) → SlowAPI (rate-limit) → CORS → router
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins_list,
        allow_credentials=True,
        allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type", "Authorization"],
    )

    app.state.limiter = limiter
    app.add_middleware(SlowAPIMiddleware)

    app.add_middleware(ProxyHeadersMiddleware, trusted_hosts="*")

    app.add_exception_handler(RateLimitExceeded, rate_limit_handler)
    app.add_exception_handler(ApiError, api_error_handler)
    app.add_exception_handler(RequestValidationError, validation_error_handler)
    app.add_exception_handler(Exception, unhandled_error_handler)

    app.include_router(auth_router.router)
    app.include_router(projects_router.router)
    app.include_router(snapshots_router.router)
    app.include_router(messages_router.router)
    app.include_router(rollback_router.router)
    app.include_router(runtime_router.router)
    app.include_router(wallet_router.router)
    app.include_router(models_router.router)
    app.include_router(public_router.router)
    app.include_router(ws_router.router)

    @app.get("/health", tags=["meta"])
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
