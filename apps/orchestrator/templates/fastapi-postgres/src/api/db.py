"""Async DB layer — single SQLAlchemy 2 engine + session factory.

AI uses `from api.db import get_session` in route handlers via FastAPI's
`Depends(get_session)`. The engine is process-global; sessions are
per-request. Same pattern the production Omnia api uses.

Schema initialisation: on app startup, `init_db()` runs
`CREATE TABLE IF NOT EXISTS` for every model in `api.models`. This is
"developer-mode migrations" — fast and idempotent, but DOESN'T handle
column drops or renames. For real migration history, AI should
recommend alembic — but ASK before adding the dep.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from api.models import Base

_engine = None
_session_maker = None


def _normalize_dsn(dsn: str) -> str:
    """SQLAlchemy async wants `postgresql+asyncpg://` — orchestrator
    provides plain `postgresql://` per the .env.example. Normalise both."""
    if dsn.startswith("postgresql+asyncpg://"):
        return dsn
    return dsn.replace("postgresql://", "postgresql+asyncpg://", 1)


def _get_engine():
    global _engine, _session_maker
    if _engine is None:
        dsn = os.environ.get("DATABASE_URL")
        if not dsn:
            raise RuntimeError(
                "DATABASE_URL is missing — orchestrator provisions it; if "
                "you see this in dev, restart the container."
            )
        _engine = create_async_engine(
            _normalize_dsn(dsn), pool_pre_ping=True, pool_size=8, max_overflow=4
        )
        _session_maker = async_sessionmaker(_engine, expire_on_commit=False)
    return _engine


async def init_db() -> None:
    """Create tables for every model registered with `Base.metadata`.
    Called from `main.lifespan` on app startup. Idempotent."""
    engine = _get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def close_db() -> None:
    """Tear down the engine on app shutdown (graceful container stop)."""
    global _engine, _session_maker
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _session_maker = None


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency. Use with `Depends(get_session)`. Auto-rollback
    on uncaught exceptions — the dependency context unwinds the session."""
    _get_engine()  # ensure session_maker is initialised
    assert _session_maker is not None
    async with _session_maker() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
