"""FastAPI application factory + lifespan.

Lifespan starts in **degraded mode** if Postgres or Redis are unreachable:
the gateway still answers `/health`, `/v1/models`, and `/v1/chat/completions`
(non-billing path with `user=null`). This makes the demo runnable without
docker-compose for the shared infra.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress

import structlog
from fastapi import FastAPI

from omnia_gateway.core.db import close_pool, init_pool
from omnia_gateway.core.http import close_http, init_http
from omnia_gateway.core.logging import configure_logging
from omnia_gateway.core.redis import close_redis, init_redis
from omnia_gateway.routers import audio, chat, health, images, messages_native, models
from omnia_gateway.services.warmup import run_warmup_loop


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    configure_logging()
    log = structlog.get_logger("omnia_gateway.main")

    try:
        await init_pool()
    except Exception as exc:
        log.warning("startup.postgres_unavailable", error=str(exc), mode="degraded")

    try:
        await init_redis()
    except Exception as exc:
        log.warning("startup.redis_unavailable", error=str(exc), mode="degraded")

    await init_http()  # local resource, never fails

    # Keep proxyapi.ru hot — without this, the first request to Haiku/nano
    # after idle returns a 4-10 char empty body that trips the empty-content
    # fallback chain in apps/api/routers/messages.py. See services/warmup.py.
    warmup_task = asyncio.create_task(run_warmup_loop(), name="proxyapi-warmup")

    try:
        yield
    finally:
        warmup_task.cancel()
        with suppress(asyncio.CancelledError, Exception):
            await warmup_task
        with suppress(Exception):
            await close_http()
        with suppress(Exception):
            await close_redis()
        with suppress(Exception):
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
    app.include_router(messages_native.router)  # native Anthropic /v1/messages (tool-use agent)
    app.include_router(images.router)
    app.include_router(audio.router)
    return app


app = create_app()
