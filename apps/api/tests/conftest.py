import io
import tarfile
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from uuid import UUID

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import insert, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from omnia_api.core.config import get_settings
from omnia_api.core.db import get_session
from omnia_api.models.base import Base
from omnia_api.models.billing import DEFAULT_BILLING_PLANS, BillingPlan


@pytest.fixture(autouse=True)
def isolated_rate_limit_buckets() -> Iterator[None]:
    """Prevent one API test's in-memory limits from throttling later tests."""
    from omnia_api.core import ratelimit

    ratelimit._storage.reset()
    yield
    ratelimit._storage.reset()


@pytest.fixture(autouse=True)
def isolated_project_repo_storage(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep repository-object tests isolated from every real MinIO endpoint."""
    from omnia_api.services import repo as repo_service

    objects: dict[str, bytes] = {}

    def upload(project_id: UUID, source: Path) -> None:
        payload = io.BytesIO()
        with tarfile.open(fileobj=payload, mode="w:gz") as archive:
            archive.add(source, arcname=".")
        objects[str(project_id)] = payload.getvalue()

    def try_download(project_id: UUID, destination: Path) -> bool:
        payload = objects.get(str(project_id))
        if payload is None:
            return False
        with tarfile.open(fileobj=io.BytesIO(payload), mode="r:gz") as archive:
            archive.extractall(destination)
        return True

    def duplicate(source_id: UUID, destination_id: UUID) -> None:
        try:
            objects[str(destination_id)] = objects[str(source_id)]
        except KeyError as exc:
            raise RuntimeError(f"repo for source project {source_id} not found in MinIO") from exc

    def delete(project_id: UUID) -> None:
        objects.pop(str(project_id), None)

    monkeypatch.setattr(repo_service, "_upload", upload)
    monkeypatch.setattr(repo_service, "_try_download", try_download)
    monkeypatch.setattr(repo_service, "duplicate_repo", duplicate)
    monkeypatch.setattr(repo_service, "delete_repo", delete)


@pytest.fixture(autouse=True)
def isolated_background_jobs(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep API unit tests isolated from real Redis queues and pub/sub."""
    from omnia_api.routers import hero_media, messages, projects, rollback, style_patch, uploads

    def discard_background_job(*_args: object, **_kwargs: object) -> None:
        return None

    async def discard_event(*_args: object, **_kwargs: object) -> None:
        return None

    for module, attribute in (
        (projects, "enqueue_build_exe"),
        (projects, "enqueue_preview"),
        (style_patch, "enqueue_preview"),
        (rollback, "enqueue_preview"),
        (uploads, "enqueue_preview"),
        (hero_media, "enqueue_hero_media_render"),
        (hero_media, "enqueue_preview"),
        (messages, "enqueue_entity_gate"),
        (messages, "enqueue_preview"),
    ):
        monkeypatch.setattr(module, attribute, discard_background_job)

    for module in (projects, style_patch, rollback, uploads, hero_media, messages):
        monkeypatch.setattr(module, "publish_event", discard_event)


def _resolve_test_database_url() -> str:
    settings = get_settings()
    if settings.database_test_url:
        return settings.database_test_url
    base = settings.database_url.rsplit("/", 1)[0]
    return f"{base}/omnia_test"


SET_UPDATED_AT_FN = """
CREATE OR REPLACE FUNCTION set_updated_at() RETURNS TRIGGER AS $$
BEGIN NEW.updated_at = NOW(); RETURN NEW; END;
$$ LANGUAGE plpgsql;
"""


@pytest_asyncio.fixture
async def test_engine():
    # Function-scoped: the engine (and its asyncpg pool) is created and used on
    # the same per-function event loop, and every test gets a freshly
    # drop_all+create_all'd schema → full isolation, no cross-test leakage.
    test_url = _resolve_test_database_url()

    base_url, db_name = test_url.rsplit("/", 1)
    admin_url = f"{base_url}/postgres"
    admin_engine = create_async_engine(admin_url, isolation_level="AUTOCOMMIT")
    async with admin_engine.connect() as conn:
        exists = (
            await conn.execute(
                text("SELECT 1 FROM pg_database WHERE datname = :n"),
                {"n": db_name},
            )
        ).scalar()
        if not exists:
            await conn.execute(text(f'CREATE DATABASE "{db_name}"'))
    await admin_engine.dispose()

    engine = create_async_engine(test_url)
    async with engine.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS citext"))
        await conn.execute(text('CREATE EXTENSION IF NOT EXISTS "uuid-ossp"'))
        await conn.execute(text(SET_UPDATED_AT_FN))
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
        await conn.execute(insert(BillingPlan), list(DEFAULT_BILLING_PLANS))

    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def db_session(test_engine) -> AsyncIterator[AsyncSession]:
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    async with factory() as session:
        yield session

    async with test_engine.begin() as conn:
        for table in reversed(Base.metadata.sorted_tables):
            await conn.execute(text(f'TRUNCATE TABLE "{table.name}" RESTART IDENTITY CASCADE'))


@pytest_asyncio.fixture
async def client(db_session: AsyncSession) -> AsyncIterator[httpx.AsyncClient]:
    from omnia_api.main import app

    async def _override_session() -> AsyncIterator[AsyncSession]:
        yield db_session

    app.dependency_overrides[get_session] = _override_session
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as c:
        yield c
    app.dependency_overrides.clear()
