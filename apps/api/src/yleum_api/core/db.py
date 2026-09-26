from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from yleum_api.core.config import get_settings

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None

DATABASE_POOL_SIZE = 10
DATABASE_POOL_MAX_OVERFLOW = 10
DATABASE_POOL_CAPACITY = DATABASE_POOL_SIZE + DATABASE_POOL_MAX_OVERFLOW
# Generation ownership pins one connection for the whole run. Keep room for
# dispatch scans, cancellation, monitors and the work itself on the same pool.
GENERATION_QUERY_CONNECTION_RESERVE = 4
# Budget both the lifetime ownership checkout and one work transaction per run.
# Additional nested queries and control-plane queries share the reserve above.
GENERATION_CONNECTIONS_PER_RUN = 2


def get_engine() -> AsyncEngine:
    global _engine, _session_factory
    if _engine is None:
        settings = get_settings()
        _engine = create_async_engine(
            settings.database_url,
            pool_pre_ping=True,
            pool_size=DATABASE_POOL_SIZE,
            max_overflow=DATABASE_POOL_MAX_OVERFLOW,
            pool_recycle=1800,
            connect_args={"timeout": 5.0, "command_timeout": 10.0},
        )
        _session_factory = async_sessionmaker(
            _engine,
            expire_on_commit=False,
            class_=AsyncSession,
        )
    return _engine


async def dispose_engine() -> None:
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _session_factory = None


async def get_session() -> AsyncIterator[AsyncSession]:
    if _session_factory is None:
        get_engine()
    assert _session_factory is not None
    async with _session_factory() as session:
        yield session
