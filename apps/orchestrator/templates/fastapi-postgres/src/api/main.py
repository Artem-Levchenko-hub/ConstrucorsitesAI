"""Entry point for the FastAPI + asyncpg starter.

AI extends this file with routers, models, and business logic. The
default shape gives a `/` welcome endpoint and `/health` so the
orchestrator's reachability check passes, plus OpenAPI docs at `/docs`
and `/redoc`.

Per-project Postgres is reachable via `DATABASE_URL` (same DSN shape
as other templates — provisioned by orchestrator into the
`omnia-postgres-users` instance, schema-isolated).

Secure-by-default baseline (FIXED — the AI keeps these on):
  * fail-fast config validation at boot — a missing/weak AUTH_SECRET or a
    missing DATABASE_URL refuses to start instead of silently signing tokens
    with a weak key.
  * a request body-size cap (413 over the limit) so a single huge payload
    can't exhaust memory.
  * security response headers (nosniff, DENY framing, no-referrer).
  * CORS locked to an explicit allowlist (`ALLOWED_ORIGINS` env); same-origin
    only when unset. NB: auth here is a stateless BEARER token in the
    Authorization header, NOT a cookie — so classic CSRF (which rides ambient
    cookies) does not apply; do not add a CSRF-token shim that fights the
    bearer model. Keep tokens out of cookies and CSRF stays a non-issue.
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from api.db import close_db, init_db
from api.routers import auth, items

# Reject any request whose declared body is larger than this (bytes). A real
# JSON API never needs megabytes per call; the cap stops a trivial memory-DoS.
MAX_BODY_BYTES = 2 * 1024 * 1024  # 2 MiB
# Minimum acceptable JWT signing-key length. 16 bytes is the floor below which a
# secret is brute-forceable; the orchestrator provisions a 32-byte secret.
MIN_SECRET_LEN = 16


def validate_runtime_config() -> None:
    """Fail fast on an insecure/incomplete environment. Called at boot so a
    misconfigured deploy is loud and immediate instead of silently insecure."""
    secret = os.environ.get("AUTH_SECRET") or os.environ.get("JWT_SECRET")
    if not secret or len(secret) < MIN_SECRET_LEN:
        raise RuntimeError(
            "AUTH_SECRET missing or too short (need >= "
            f"{MIN_SECRET_LEN} chars) — refusing to start with a weak JWT "
            "signing key. The orchestrator injects this; restart the container."
        )
    if not os.environ.get("DATABASE_URL"):
        raise RuntimeError(
            "DATABASE_URL missing — refusing to start without a database."
        )


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Validate config, then initialise the asyncpg pool on boot, dispose on
    shutdown. Pool init also runs the idempotent schema creator so the first
    request after provision doesn't 500 on a missing relation."""
    validate_runtime_config()
    await init_db()
    yield
    await close_db()


class MaxBodySizeMiddleware(BaseHTTPMiddleware):
    """Reject an over-large request by its declared Content-Length (413). Cheap
    pre-read guard; a body with no Content-Length is left to the route to bound."""

    async def dispatch(self, request: Request, call_next):
        cl = request.headers.get("content-length")
        if cl is not None:
            try:
                if int(cl) > MAX_BODY_BYTES:
                    return JSONResponse(
                        {"detail": "payload too large"}, status_code=413
                    )
            except ValueError:
                return JSONResponse(
                    {"detail": "invalid content-length"}, status_code=400
                )
        return await call_next(request)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Add conservative security response headers to every response."""

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        return response


app = FastAPI(
    title="Omnia API",
    description="Starter FastAPI service. Replace this description per project.",
    version="0.0.1",
    lifespan=lifespan,
)

# Order matters: body-size guard runs first (reject early), then security headers.
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(MaxBodySizeMiddleware)

# CORS — locked to an explicit allowlist. Set ALLOWED_ORIGINS (comma-separated)
# to the frontend origin(s) that call this API; leave unset for same-origin only.
# Never use "*" with credentials — that would let any site read authed responses.
_origins = [
    o.strip() for o in os.environ.get("ALLOWED_ORIGINS", "").split(",") if o.strip()
]
if _origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
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
