"""asyncpg pool for the bot.

Uses the per-project Postgres schema provisioned by the orchestrator
(`omnia-postgres-users` → `proj_<id>` schema, role `proj_<id>_user`).
Same DSN-shape as the Next template — DATABASE_URL is set in env at
container start.

Pattern AI must use:

```python
from bot.db import db_pool

async def list_users():
    async with db_pool().acquire() as conn:
        rows = await conn.fetch("SELECT id, name FROM bot_users")
        return rows
```

A single pool is fine for typical bot loads (1k-10k users). Acquire +
release per query — don't hold a connection across handler suspensions.
"""

from __future__ import annotations

import os
from typing import Optional

import asyncpg

_pool: Optional[asyncpg.Pool] = None


async def get_pool() -> asyncpg.Pool:
    """Lazily create the singleton pool. Safe to call from any coroutine."""
    global _pool
    if _pool is None or _pool.is_closing():
        dsn = os.environ.get("DATABASE_URL")
        if not dsn:
            raise RuntimeError(
                "DATABASE_URL is missing — bot needs Postgres for state. "
                "Orchestrator provisions it automatically; if you see this "
                "in dev, restart the container."
            )
        # asyncpg doesn't accept the `postgresql+asyncpg://` SQLAlchemy
        # dialect — strip it. Same normalisation as orchestrator's
        # postgres_admin module.
        dsn = dsn.replace("postgresql+asyncpg://", "postgresql://", 1)
        _pool = await asyncpg.create_pool(
            dsn=dsn, min_size=1, max_size=8, command_timeout=10
        )
    return _pool


def db_pool() -> asyncpg.Pool:
    """Sync accessor — convenient in handlers via `await db_pool().acquire()`
    once the pool is initialised. Raises if called before first
    `get_pool()` await."""
    if _pool is None:
        raise RuntimeError("pool not initialised — await get_pool() first")
    return _pool
