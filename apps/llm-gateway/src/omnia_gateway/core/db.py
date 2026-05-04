"""Postgres pool — single shared asyncpg pool for `usage` writes.

The gateway is read-tiny / write-tiny against the shared DB owned by Agent B
(see docs/02-data-model.md). One pool, max 10 connections, is enough.
"""

from __future__ import annotations

import asyncpg

from omnia_gateway.core.config import get_settings

_pool: asyncpg.Pool | None = None


def _normalize_dsn(dsn: str) -> str:
    """Strip SQLAlchemy driver suffix so asyncpg can parse the DSN.

    `apps/api` (SQLAlchemy) uses `postgresql+asyncpg://...`; asyncpg.connect
    requires the bare `postgresql://` form.
    """
    if dsn.startswith("postgresql+asyncpg://"):
        return "postgresql://" + dsn.removeprefix("postgresql+asyncpg://")
    return dsn


async def init_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        dsn = _normalize_dsn(get_settings().database_url)
        _pool = await asyncpg.create_pool(dsn, min_size=1, max_size=10)
    return _pool


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


def get_pool() -> asyncpg.Pool:
    if _pool is None:
        raise RuntimeError("DB pool not initialized — call init_pool() in lifespan")
    return _pool
