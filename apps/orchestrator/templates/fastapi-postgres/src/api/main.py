"""Entry point for the FastAPI + asyncpg starter.

AI extends this file with routers, models, and business logic. The
default shape gives a `/` welcome endpoint and `/health` so the
orchestrator's reachability check passes, plus OpenAPI docs at `/docs`
and `/redoc`.

Per-project Postgres is reachable via `DATABASE_URL` (same DSN shape
as other templates — provisioned by orchestrator into the
`omnia-postgres-users` instance, schema-isolated).
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI

from api.db import close_db, init_db
from api.routers import auth, items


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Initialise the asyncpg pool on boot, dispose on shutdown.

    Pool init also runs the schema creator (idempotent CREATE TABLE
    IF NOT EXISTS) so the first request after provision doesn't 500
    on a missing relation. Same idea as the Next template's `db:push`
    entrypoint but inline because Python has no equivalent CLI.
    """
    await init_db()
    yield
    await close_db()


app = FastAPI(
    title="Omnia API",
    description="Starter FastAPI service. Replace this description per project.",
    version="0.0.1",
    lifespan=lifespan,
)

# Routers — AI adds more by creating `api/routers/<name>.py` with its own
# APIRouter and including it here.
app.include_router(auth.router)
app.include_router(items.router)


@app.get("/", tags=["meta"])
async def root() -> dict[str, str]:
    """Welcome message. AI usually replaces with a real index — for
    example, listing available endpoints or redirecting to /docs."""
    return {
        "service": "omnia-api",
        "docs": "/docs",
        "openapi": "/openapi.json",
    }


@app.get("/health", tags=["meta"])
async def health() -> dict[str, str]:
    """Orchestrator pings this to verify the container is alive.
    Don't add DB work here — health must stay <10 ms even if DB is down."""
    return {"status": "ok"}
