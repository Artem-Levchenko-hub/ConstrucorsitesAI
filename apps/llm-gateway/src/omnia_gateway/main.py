"""FastAPI application factory + lifespan."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from omnia_gateway.core.db import close_pool, init_pool
from omnia_gateway.core.http import close_http, init_http
from omnia_gateway.core.logging import configure_logging
from omnia_gateway.core.redis import close_redis, init_redis
from omnia_gateway.routers import chat, health, models


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    configure_logging()
    await init_pool()
    await init_redis()
    await init_http()
    try:
        yield
    finally:
        await close_http()
        await close_redis()
        await close_pool()


def create_app() -> FastAPI:
    app = FastAPI(
        title="Omnia LLM Gateway",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.include_router(health.router)
    app.include_router(models.router)
    app.include_router(chat.router)
    return app


app = create_app()
