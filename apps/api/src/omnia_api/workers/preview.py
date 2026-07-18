"""RQ job: рендер PNG-превью snapshot'а через Playwright."""

from __future__ import annotations

import asyncio
import json
import re
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

import redis.asyncio as aioredis
from minio import Minio
from playwright.async_api import Page, async_playwright
from sqlalchemy import update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from omnia_api.core import minio as minio_core
from omnia_api.core.config import get_settings
from omnia_api.core.redis import project_channel
from omnia_api.models.project import Project
from omnia_api.models.snapshot import Snapshot
from omnia_api.services import dev_container
from omnia_api.services import repo as repo_svc

VIEWPORT = {"width": 1280, "height": 800}
GOTO_TIMEOUT_MS = 15_000

# Container-backed templates render from a live dev container, not from repo
# files — their git repo only tracks AI-generated files, no root `index.html`.
# (Canonical list lives in routers/messages.py CONTAINER_NEXT; kept in sync.)
# `spa` (Vite + React, Phase 7.2) renders from its dev container too.
CONTAINER_NEXT = ("fullstack", "nextjs_entities", "spa", "realtime")
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
DEFAULT_CAPTURE_WIDTHS: tuple[int, ...] = (360, 768, 1440)
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


# Max wait for the app to actually PAINT REAL CONTENT (not a loading skeleton)
# before the shot. networkidle can fire while `loading.tsx` still shows its
# skeleton (the fetch finished but React hasn't swapped, or the skeleton itself
# makes no request) — so the vision judge critiques a "загрузка…" page and calls
# a fine app ugly/empty. Bounded so a genuinely-blank page never hangs.
_CONTENT_READY_MS = 6000


async def _await_content(page) -> None:
    """Best-effort: hold the shot until the loading skeleton is GONE and real
    content painted, so the vision judge never grades a not-yet-loaded page.

    Signals: our own `loading.tsx` marks itself `data-omnia-skeleton` /
    `aria-busy="true"`; "real content" = meaningful body text OR a structural
    visual (main/img/svg/table/form/article/section). On timeout we fall through
    to the shot anyway (R-10) — a late read still beats hanging the audit."""
    try:
        await page.wait_for_function(
            "() => {"
            "  if (document.querySelector('[data-omnia-skeleton],[aria-busy=\"true\"]')) return false;"
            "  var t = ((document.body && document.body.innerText) || '').trim().length;"
            "  var visual = !!document.querySelector('main,[role=\"main\"],img,svg,table,form,article,section');"
            "  return t > 40 || visual;"
            "}",
            timeout=_CONTENT_READY_MS,
        )
    except Exception:
        pass


def _rewrite_minio_to_internal(content: str) -> str:
    """Repoint resolved ``<img src>`` from the PUBLIC MinIO URL to the INTERNAL
    endpoint for the duration of a render.

    The image-resolver bakes ``data-omnia-gen`` photos as absolute public URLs
    (``{minio_public_url}/<bucket>/<key>``) so real browsers load them. But the
    preview worker renders the page from inside its container, where the public
    host (``constructor.lead-generator.ru`` → the host's own public IP) is
    **unreachable** — the container can't hairpin-NAT back to the host, so every
    such ``<img>`` hangs (``net::ERR`` / connect timeout) and the screenshot
    lands on an empty hero. ``_await_paint``'s 3s budget can't fix an
    unreachable URL — it just bounds the inevitable failure, so the timeline
    thumbnail and the design-judge both saw an image-less (``выглядит пусто``)
    page even though the deployed site is fine.

    Internal ``http://minio:9000/<bucket>/<key>`` answers in <10ms from the
    worker, so we swap the base **only in the in-memory copy fed to chromium**.
    The committed/served files are untouched — public URLs still ship to users.
    Idempotent and a no-op for pages with no MinIO images (plain str.replace).
    """
    settings = get_settings()
    public = settings.minio_public_url.rstrip("/") + "/"
    scheme = "https" if settings.minio_secure else "http"
    internal = f"{scheme}://{settings.minio_endpoint}/"
    if public == internal:  # already internal (e.g. local dev) — nothing to do
        return content
    return content.replace(public, internal)


# Bounded best-effort wait for a CONTAINER app's client-side data fetches to
# settle before the shot. A generated dashboard renders its shell on first paint
# but loads its lists / StatCards via a client fetch right after hydration — so
# the fixed `_RENDER_SETTLE_MS` beat alone catches the empty Suspense skeleton,
# not the real data, and the timeline thumbnail looks blank even though the live
# app is fine. Container Next.js apps ship compiled Tailwind v4 and finite data
# fetches, so `networkidle` actually fires once those finish. (Static freeform
# pages must NOT use this — capture() loads the Tailwind *Play-CDN*, a perpetual
# connection that keeps the network busy forever so networkidle never settles;
# that is exactly why the static path sticks to `domcontentloaded`.) Bounded so
# an app that long-polls client-side can't hang the capture — on timeout we just
# shoot the current frame, i.e. the prior behaviour (R-10 fail-soft).
_CONTAINER_NETWORKIDLE_MS = 3500


async def _await_container_ready(page: Page) -> None:
    """Let a live container app's post-hydration data fetches finish before the
    screenshot. Best-effort: a timeout (or any error) falls through to the shot
    instead of blocking it — never worse than the old skeleton-catching beat."""
    try:
        await page.wait_for_load_state("networkidle", timeout=_CONTAINER_NETWORKIDLE_MS)
    except Exception:
        pass


# External web-font requests the worker can NEVER reach — no public egress, so a
# `<link>`/`@font-face` to Google Fonts just hangs. `page.screenshot()` blocks on
# `document.fonts.ready`, which never resolves while a font is stuck 'loading' →
# the shot times out at 30s and the thumbnail lands BLANK WHITE even though the
# app rendered fine (owner report 2026-07-18: every spa live-container thumbnail
# was white; React WAS mounted — `document.fonts.status` stayed 'loading').
_FONT_ABORT_RE = re.compile(
    r"fonts\.g(oogleapis|static)\.com|\.(woff2?|ttf|otf|eot)(\?|$)", re.IGNORECASE
)


async def _block_external_fonts(page: Page) -> None:
    """Abort unreachable web-font requests so ``document.fonts.ready`` resolves
    fast (fonts error → system fallback) instead of hanging the screenshot. The
    page still renders its real content — just in fallback fonts, which for a
    thumbnail is invisible next to a blank-white miss. Best-effort (R-10)."""
    async def _abort(route: object) -> None:
        try:
            await route.abort()  # type: ignore[attr-defined]
        except Exception:
            pass

    try:
        await page.route(_FONT_ABORT_RE, _abort)
    except Exception:
        pass


async def _route_media_internal(page: Page) -> None:
    """Serve the live app's PUBLIC MinIO assets (generated images + video) from
    the INTERNAL endpoint during a screenshot.

    The dev container's HTML carries absolute public URLs
    (``{minio_public_url}/<bucket>/<key>``), but the worker has no public egress
    and can't hairpin-NAT back to the host — so a video-hero / image-rich page
    screenshots with a black/empty hero. We can't ``continue_(url=)`` across the
    https→http protocol change, so we fetch the reachable internal URL and fulfill
    the request with those bytes. This is what makes a CINEMATIC (scroll-scrub
    video) site's thumbnail show its real hero instead of a blank frame (owner:
    "всегда всё прогружалось, даже если кинематографичный эффект"). Best-effort:
    any failure aborts the one asset, never the whole shot."""
    settings = get_settings()
    public = settings.minio_public_url.rstrip("/") + "/"
    scheme = "https" if settings.minio_secure else "http"
    internal = f"{scheme}://{settings.minio_endpoint}/"
    if public == internal:  # already internal (local dev) — nothing to reroute
        return

    async def _reroute(route: object) -> None:
        try:
            req_url = route.request.url  # type: ignore[attr-defined]
            internal_url = req_url.replace(public, internal)
            resp = await page.request.get(internal_url, timeout=8000)
            await route.fulfill(response=resp)  # type: ignore[attr-defined]
        except Exception:
            try:
                await route.abort()  # type: ignore[attr-defined]
            except Exception:
                pass

    try:
        await page.route(f"{public}**", _reroute)
    except Exception:
        pass


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
            full.write_text(_rewrite_minio_to_internal(content), encoding="utf-8")
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


# Container-to-container URL of a running dev preview now lives in the shared
# `dev_container` service (R-04 single source — the entity composition gate uses
# the same builder). Kept as a module-level alias so existing call sites and
# tests (`preview._resolve_live_url`) are unchanged.
_resolve_live_url = dev_container.resolve_live_url


async def capture_live_url(
    url: str,
    widths: Sequence[int] = (1440, 360),
    *,
    height: int = 900,
    settle_container: bool = True,
    full_page: bool = False,
) -> dict[int, bytes]:
    """Screenshot a LIVE running URL (the dev container's preview) at each width.

    The agent's `see` tool uses this to LOOK at the page it just built; the
    bytes pipe straight into ``vision_audit.audit_screenshots``. Mirrors the
    live-container branch of ``_render_async`` (await client-side data + paint
    before the shot) but returns raw PNG bytes per width like ``capture()``.

    Fail-soft per viewport: a width that times out / crashes is skipped (not
    raised), so one flaky viewport can't blind the whole audit. Returns an empty
    dict if every width failed — the caller treats that as "couldn't see".
    """
    out: dict[int, bytes] = {}
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        try:
            for w in widths:
                page = await browser.new_page(
                    viewport={"width": int(w), "height": height},
                    reduced_motion="reduce",
                )
                try:
                    await _block_external_fonts(page)
                    await _route_media_internal(page)
                    await page.goto(
                        url, wait_until="domcontentloaded", timeout=GOTO_TIMEOUT_MS
                    )
                    if settle_container:
                        await _await_container_ready(page)
                    await _await_paint(page)
                    # Never grade a loading skeleton: hold until real content painted.
                    await _await_content(page)
                    out[int(w)] = await page.screenshot(full_page=full_page)
                except Exception:
                    # One bad viewport must never blind the whole audit (or raise
                    # into the agent loop). Skip it; other widths still land.
                    pass
                finally:
                    await page.close()
        finally:
            await browser.close()
    return out


async def capture_diagnostics(
    url: str, *, timeout_ms: int = GOTO_TIMEOUT_MS
) -> dict[str, list[str]]:
    """Load a live URL once and collect BROWSER-side signals a screenshot can't
    show: console errors/warnings, uncaught page errors, and failed (>=400) network
    requests. The agent's `see` tool appends these to its observation so a JS error
    or a broken fetch on load is visible, not just "the page looks off".

    Fail-soft: any error returns whatever was collected so far (never raises)."""
    console_errors: list[str] = []
    failed: list[str] = []

    def _on_console(msg: object) -> None:
        try:
            if getattr(msg, "type", "") in ("error", "warning"):
                console_errors.append(f"{msg.type}: {msg.text}"[:300])  # type: ignore[attr-defined]
        except Exception:
            pass

    def _on_response(resp: object) -> None:
        try:
            if resp.status >= 400:  # type: ignore[attr-defined]
                failed.append(f"{resp.status} {resp.request.method} {resp.url}"[:300])  # type: ignore[attr-defined]
        except Exception:
            pass

    def _on_pageerror(err: object) -> None:
        try:
            console_errors.append(f"pageerror: {err}"[:300])
        except Exception:
            pass

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        try:
            page = await browser.new_page(viewport={"width": 1280, "height": 900})
            page.on("console", _on_console)
            page.on("response", _on_response)
            page.on("pageerror", _on_pageerror)
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
                await _await_container_ready(page)
                await _await_paint(page)
            except Exception:
                pass  # return whatever signals were captured before the failure
            finally:
                await page.close()
        finally:
            await browser.close()

    return {
        "console_errors": list(dict.fromkeys(console_errors))[:12],
        "failed_requests": list(dict.fromkeys(failed))[:12],
    }


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
            project = await session.get(Project, project_id)
            template = project.template if project is not None else None

        files = await asyncio.to_thread(repo_svc.read_files, project_id, commit_sha)

        # Two render sources: a static template screenshots its repo `index.html`
        # off disk; a container template screenshots the LIVE dev container over
        # the runtime network. The source is decided by the TEMPLATE, not by
        # whether an index.html happens to be in the repo: a Vite `spa` DOES ship
        # an index.html in its repo, but that file is a bare `<div id=root>` +
        # `<script src="/src/main.tsx">` shell — screenshotting it off-disk via
        # file:// can't run the dev server, so /src/main.tsx never loads and the
        # thumbnail comes out BLANK WHITE (owner report 2026-07-18). Container
        # templates therefore ALWAYS render from the live container.
        is_container = template in CONTAINER_NEXT
        has_index = "index.html" in files
        if not has_index and not is_container:
            return

        live_url: str | None = None
        if is_container:
            live_url = await _resolve_live_url(project_id)
            if live_url is None:
                return  # container not running / unreachable — no thumbnail now

        preview_key = f"{snapshot_id}.png"
        with tempfile.TemporaryDirectory(prefix=f"omnia-preview-{sid}-") as tmp:
            workdir = Path(tmp)
            png_path = workdir / "preview.png"

            if live_url is not None:
                target_url = live_url
            else:
                for path, content in files.items():
                    full = workdir / path
                    full.parent.mkdir(parents=True, exist_ok=True)
                    full.write_text(
                        _rewrite_minio_to_internal(content), encoding="utf-8"
                    )
                target_url = (workdir / "index.html").as_uri()

            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                try:
                    page = await browser.new_page(
                        viewport=VIEWPORT, reduced_motion="reduce"
                    )
                    # Abort unreachable web fonts so the screenshot's font-wait
                    # can't hang → blank white thumbnail (2026-07-18).
                    await _block_external_fonts(page)
                    # Reroute public MinIO assets to the internal endpoint so a
                    # live container app's images + video actually paint (worker
                    # has no public egress) — "always load everything, even the
                    # cinematic video effect".
                    if live_url is not None:
                        await _route_media_internal(page)
                    await page.goto(
                        target_url,
                        wait_until="domcontentloaded",
                        timeout=GOTO_TIMEOUT_MS,
                    )
                    # A live container app paints its shell first, then fetches its
                    # data client-side — wait for that to settle so the thumbnail
                    # shows real content, not the empty skeleton. Static pages
                    # render off disk with no such fetch (and load the Tailwind
                    # Play-CDN, where networkidle never fires) → they skip it.
                    if live_url is not None:
                        await _await_container_ready(page)
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
            # Self-heal the previews bucket's public-read policy on every render
            # so a private/mis-provisioned bucket never leaves thumbnails 403 in
            # the browser (owner report 2026-07-18 — the project-card photos and
            # snapshot preview all 403'd because `previews` was never made public).
            await asyncio.to_thread(
                minio_core.ensure_public_bucket,
                client,
                settings.minio_bucket_previews,
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
