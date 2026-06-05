"""RQ job: рендер PNG-превью snapshot'а через Playwright."""

from __future__ import annotations

import asyncio
import json
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
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
# `domcontentloaded` (NOT networkidle): broken images + the Tailwind Play-CDN keep
# the network busy, so networkidle never settles and Page.goto times out at 15s —
# which made the acceptance gate SKIP responsive+vision and ship junk as passed=True.
# domcontentloaded fires reliably; we then settle async for two things:
# (1) the Tailwind Play-CDN JIT compiles utility classes, (2) web-fonts paint.
# We ALSO force `reduced_motion="reduce"` on every capture page so omnia-kit's
# reveal / scroll-reveal animations — all gated behind
# `@media (prefers-reduced-motion: no-preference)` — render in their FINAL
# visible state instead of their opacity:0 start. Without it the screenshot
# catches an empty / half-built hero (the "съехавшая вёрстка" in the timeline
# thumbnail even when the live page is fine). This settle is the belt to that
# suspenders: fonts + Tailwind JIT have a beat to apply before we shoot.
_RENDER_SETTLE_MS = 600

# Acceptance-gate render harness (Phase 11, Sprint 1.2).
DEFAULT_CAPTURE_WIDTHS: tuple[int, ...] = (375, 768, 1440)
# Sub-pixel rounding means scrollWidth can exceed the viewport by ~1px even on
# a perfectly-fitting page; only flag real horizontal overflow above this.
_OVERFLOW_TOLERANCE_PX = 2


@dataclass(frozen=True)
class CaptureResult:
    """One rendered viewport: the PNG plus its overflow measurement."""

    png: bytes
    viewport_width: int
    scroll_width: int
    has_overflow: bool


# Wait budget for remote <img> (MinIO) to paint before screenshotting. Without
# it the shot lands on the gradient placeholders behind data-omnia-gen images →
# the design judge saw gray boxes (the documented reason the prior vision judge
# was useless) and the timeline thumbnail looked empty. Bounded so a slow or
# broken image can never hang the capture.
_IMAGE_WAIT_MS = 3000


async def _await_paint(page) -> None:
    """Settle a freshly-loaded page before the screenshot: web-fonts ready,
    remote images painted (bounded by ``_IMAGE_WAIT_MS``), then a short
    Tailwind-JIT / paint beat. Every step is best-effort — a failure (or a slow
    image) never blocks the shot, it just falls through to the timeout."""
    try:
        await page.evaluate("() => document.fonts.ready")
    except Exception:
        pass
    try:
        await page.evaluate(
            "(ms) => Promise.race(["
            "  Promise.all(Array.from(document.images).map(function (i) {"
            "    return i.complete ? 1 : new Promise(function (r) {"
            "      i.addEventListener('load', r, { once: true });"
            "      i.addEventListener('error', r, { once: true });"
            "    });"
            "  })),"
            "  new Promise(function (r) { setTimeout(r, ms); })"
            "])",
            _IMAGE_WAIT_MS,
        )
    except Exception:
        pass
    await page.wait_for_timeout(_RENDER_SETTLE_MS)


async def capture(
    files: dict[str, str],
    widths: Sequence[int] = DEFAULT_CAPTURE_WIDTHS,
    *,
    height: int = 900,
    full_page: bool = False,
) -> dict[int, CaptureResult]:
    """Render ``files`` at each width and return PNG bytes + overflow flag.

    The acceptance gate uses this to (1) screenshot a freeform page for the
    vision audit and (2) detect horizontal scroll / broken responsiveness
    (``scroll_width > viewport`` means content spills sideways). One browser,
    one page per width. Screenshot returns **bytes** (no `path=`) so callers
    can pipe it straight into a vision message or MinIO.

    Raises ``ValueError`` if there is no root ``index.html`` to load.
    """
    if "index.html" not in files:
        raise ValueError("capture() requires an index.html at the repo root")

    out: dict[int, CaptureResult] = {}
    with tempfile.TemporaryDirectory(prefix="omnia-capture-") as tmp:
        workdir = Path(tmp)
        for path, content in files.items():
            full = workdir / path
            full.parent.mkdir(parents=True, exist_ok=True)
            full.write_text(content, encoding="utf-8")
        index_uri = (workdir / "index.html").as_uri()

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            try:
                for w in widths:
                    page = await browser.new_page(
                        viewport={"width": int(w), "height": height},
                        reduced_motion="reduce",
                    )
                    try:
                        await page.goto(
                            index_uri,
                            wait_until="domcontentloaded",
                            timeout=GOTO_TIMEOUT_MS,
                        )
                        # Web-fonts ready + remote images painted + Tailwind-JIT
                        # beat before measuring overflow / screenshotting.
                        await _await_paint(page)
                        scroll_width = await page.evaluate(
                            "() => document.documentElement.scrollWidth"
                        )
                        png = await page.screenshot(full_page=full_page)
                    finally:
                        await page.close()
                    sw = int(scroll_width or w)
                    out[int(w)] = CaptureResult(
                        png=png,
                        viewport_width=int(w),
                        scroll_width=sw,
                        has_overflow=sw > int(w) + _OVERFLOW_TOLERANCE_PX,
                    )
            finally:
                await browser.close()
    return out


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
                    page = await browser.new_page(
                        viewport=VIEWPORT, reduced_motion="reduce"
                    )
                    await page.goto(
                        (workdir / "index.html").as_uri(),
                        wait_until="domcontentloaded",
                        timeout=GOTO_TIMEOUT_MS,
                    )
                    # Same settle as capture(): fonts + images painted + JIT beat,
                    # and reduced_motion so reveal-animated content isn't opacity:0.
                    await _await_paint(page)
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
