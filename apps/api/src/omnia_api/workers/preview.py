"""RQ job: рендер PNG-превью snapshot'а через Playwright."""

from __future__ import annotations

import asyncio
import json
import tempfile
from pathlib import Path
from uuid import UUID

import redis.asyncio as aioredis
from minio import Minio
from playwright.async_api import async_playwright
from sqlalchemy import update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from omnia_api.core.config import get_settings
from omnia_api.core.redis import project_channel
from omnia_api.models.snapshot import Snapshot
from omnia_api.services import repo as repo_svc

VIEWPORT = {"width": 1280, "height": 800}
GOTO_TIMEOUT_MS = 15_000


async def _render_async(snapshot_id: str) -> None:
    settings = get_settings()
    sid = UUID(snapshot_id)

    engine = create_async_engine(settings.database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            snapshot = await session.get(Snapshot, sid)
            if snapshot is None:
                return
            project_id = snapshot.project_id
            commit_sha = snapshot.commit_sha

        files = await asyncio.to_thread(repo_svc.read_files, project_id, commit_sha)
        if "index.html" not in files:
            return

        preview_key = f"{snapshot_id}.png"
        with tempfile.TemporaryDirectory(prefix=f"omnia-preview-{sid}-") as tmp:
            workdir = Path(tmp)
            for path, content in files.items():
                full = workdir / path
                full.parent.mkdir(parents=True, exist_ok=True)
                full.write_text(content, encoding="utf-8")
            png_path = workdir / "preview.png"

            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                try:
                    page = await browser.new_page(viewport=VIEWPORT)
                    await page.goto(
                        (workdir / "index.html").as_uri(),
                        wait_until="networkidle",
                        timeout=GOTO_TIMEOUT_MS,
                    )
                    await page.screenshot(path=str(png_path), full_page=False)
                finally:
                    await browser.close()

            client = Minio(
                settings.minio_endpoint,
                access_key=settings.minio_access_key,
                secret_key=settings.minio_secret_key.get_secret_value(),
                secure=settings.minio_secure,
            )
            await asyncio.to_thread(
                client.fput_object,
                settings.minio_bucket_previews,
                preview_key,
                str(png_path),
                content_type="image/png",
            )

        async with factory() as session:
            await session.execute(
                update(Snapshot).where(Snapshot.id == sid).values(preview_key=preview_key)
            )
            await session.commit()

        preview_url = (
            f"{settings.minio_public_url.rstrip('/')}/"
            f"{settings.minio_bucket_previews}/{preview_key}"
        )
        r = aioredis.from_url(settings.redis_url, decode_responses=True)
        try:
            payload = json.dumps(
                {
                    "type": "preview.ready",
                    "data": {
                        "snapshot_id": snapshot_id,
                        "preview_url": preview_url,
                    },
                }
            )
            await r.publish(project_channel(project_id), payload)
        finally:
            await r.aclose()
    finally:
        await engine.dispose()


def render_preview(snapshot_id: str) -> None:
    """Sync entrypoint для RQ. Внутри гонит асинхронный pipeline."""
    asyncio.run(_render_async(snapshot_id))
