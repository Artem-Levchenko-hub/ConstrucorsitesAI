"""Live screenshots of a running app — the picture behind a MAX version card.

One entry point, :func:`capture_live_url_report`: open the app's live URL in a
headless browser (optionally through a signed bootstrap URL first, so the shot
is of the app as an authenticated visitor sees it), wait until it has actually
painted, and take one screenshot per requested viewport width. Failures are
kept, not raised: a width that times out is reported as an issue and the rest
still come back, because a missing thumbnail must never fail a build.

``services/snapshot_preview_capture`` is the only caller — it uploads the PNGs
and persists them against the version.

The deferred RQ job that used to live here (screenshot a static page off disk,
or a legacy dev container over the runtime network) left with the site builder:
a MAX app's thumbnail is captured inside the generation lease, with exact
before/after source checks, so a late background shot could only mislabel it.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit

from playwright.async_api import (
    Page,
    async_playwright,
)
from playwright.async_api import (
    TimeoutError as PlaywrightTimeoutError,
)

from yleum_api.core.config import get_settings

GOTO_TIMEOUT_MS = 15_000
_LIVE_SIGNED_BOOTSTRAP_TIMEOUT_MS = 120_000

# `domcontentloaded` (NOT networkidle): broken images + the Tailwind Play-CDN keep
# the network busy, so networkidle never settles and Page.goto times out at 15s —
# which made the acceptance gate SKIP responsive+vision and ship junk as passed=True.
# domcontentloaded fires reliably; we then settle async for two things:
# (1) the Tailwind Play-CDN JIT compiles utility classes, (2) web-fonts paint.
# We force `reduced_motion="reduce"` on every capture page so the app's
# reveal / scroll-reveal animations — all gated behind
# `@media (prefers-reduced-motion: no-preference)` — render in their FINAL
# visible state instead of their opacity:0 start. Without it the screenshot
# catches an empty / half-built hero (the "съехавшая вёрстка" in the timeline
# thumbnail even when the live page is fine). This settle is the belt to that
# suspenders: fonts + Tailwind JIT have a beat to apply before we shoot.
_RENDER_SETTLE_MS = 600


@dataclass(frozen=True)
class LiveCaptureIssue:
    """One bounded, model-safe reason a live screenshot stage failed."""

    stage: str
    reason: str
    viewport_width: int | None = None

    def to_text(self) -> str:
        prefix = f"{self.viewport_width}px " if self.viewport_width is not None else ""
        return f"{prefix}{self.stage}: {self.reason}"


@dataclass(frozen=True)
class LiveCaptureReport:
    """Live screenshot bytes plus bounded failure notes."""

    screenshots: dict[int, bytes]
    issues: tuple[LiveCaptureIssue, ...] = ()

    def summary(self) -> str:
        if not self.issues:
            return ""
        return "; ".join(issue.to_text() for issue in self.issues)


# Wait budget for remote <img> (MinIO) to paint before screenshotting. Without
# it the shot lands on the gradient placeholders behind data-omnia-gen images →
# the design judge saw gray boxes (the documented reason the prior vision judge
# was useless) and the timeline thumbnail looked empty. Bounded so a slow or
# broken image can never hang the capture.
_IMAGE_WAIT_MS = 3000


async def _await_paint(page: Page) -> None:
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


async def _await_content(page: Page) -> None:
    """Best-effort: hold the shot until the loading skeleton is GONE and real
    content painted, so the vision judge never grades a not-yet-loaded page.

    Signals: our own `loading.tsx` marks itself `data-omnia-skeleton` /
    `aria-busy="true"`; "real content" = meaningful body text OR a structural
    visual (main/img/svg/table/form/article/section). On timeout we fall through
    to the shot anyway (R-10) — a late read still beats hanging the audit."""
    try:
        await page.wait_for_function(
            "() => {"
            "  if (document.querySelector("
            "'[data-omnia-skeleton],[aria-busy=\"true\"]')) return false;"
            "  var t = ((document.body && document.body.innerText) || '').trim().length;"
            "  var visual = !!document.querySelector("
            "'main,[role=\"main\"],img,svg,table,form,article,section');"
            "  return t > 40 || visual;"
            "}",
            timeout=_CONTENT_READY_MS,
        )
    except Exception:
        pass


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


_TEXT_URL_RE = re.compile(r"https?://[^\s'\"<>)]+", re.IGNORECASE)


def _redact_url(raw: str) -> str:
    try:
        parsed = urlsplit(raw)
    except ValueError:
        return "[invalid URL]"
    if not parsed.scheme or not parsed.netloc:
        return raw
    safe = parsed._replace(
        netloc=parsed.netloc.rsplit("@", 1)[-1],
        query="[REDACTED]" if parsed.query else "", fragment="",
    )
    return urlunsplit(safe)


def _redact_text(text: str, *, limit: int = 300) -> str:
    redacted = _TEXT_URL_RE.sub(lambda m: _redact_url(m.group(0)), str(text))
    redacted = re.sub(
        r"(?i)\b(signature|token|session|cookie|authorization)=([^&\s]+)",
        r"\1=[REDACTED]",
        redacted,
    )
    return redacted[:limit]


def _is_timeout_error(exc: Exception) -> bool:
    return isinstance(exc, PlaywrightTimeoutError) or "timeout" in (
        f"{type(exc).__name__} {exc}"
    ).lower()


def _live_capture_issue(
    stage: str,
    exc: Exception,
    *,
    viewport_width: int | None = None,
) -> LiveCaptureIssue:
    if _is_timeout_error(exc):
        if stage == "bootstrap":
            reason = "signed preview bootstrap timed out"
        elif stage == "warmup":
            reason = "preview cold-start timed out"
        else:
            reason = "viewport capture timed out"
    else:
        reason = f"{type(exc).__name__}: {_redact_text(str(exc))}"
    return LiveCaptureIssue(stage=stage, reason=reason, viewport_width=viewport_width)


async def capture_live_url_report(
    url: str,
    widths: Sequence[int] = (1440, 360),
    *,
    height: int = 900,
    settle_container: bool = True,
    full_page: bool = False,
    bootstrap_url: str | None = None,
    require_success_status: bool = False,
) -> LiveCaptureReport:
    """Like ``capture_live_url()``, but keeps bounded failure diagnostics."""

    out: dict[int, bytes] = {}
    issues: list[LiveCaptureIssue] = []
    startup_timeout_ms = (
        max(GOTO_TIMEOUT_MS, _LIVE_SIGNED_BOOTSTRAP_TIMEOUT_MS)
        if bootstrap_url
        else GOTO_TIMEOUT_MS
    )
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        try:
            initial_width = int(widths[0]) if widths else 1280
            page = await browser.new_page(
                viewport={"width": initial_width, "height": height},
                reduced_motion="reduce",
            )
            try:
                await _block_external_fonts(page)
                await _route_media_internal(page)
                if bootstrap_url:
                    try:
                        await page.goto(
                            bootstrap_url,
                            wait_until="domcontentloaded",
                            timeout=startup_timeout_ms,
                        )
                    except Exception as exc:
                        issues.append(_live_capture_issue("bootstrap", exc))
                        return LiveCaptureReport({}, tuple(issues))
                try:
                    response = await page.goto(
                        url,
                        wait_until="domcontentloaded",
                        timeout=startup_timeout_ms,
                    )
                    if require_success_status and (response is None or response.status >= 400):
                        raise ValueError("Unsuccessful preview HTTP status")
                    if settle_container:
                        await _await_container_ready(page)
                    await _await_paint(page)
                    await _await_content(page)
                except Exception as exc:
                    issues.append(_live_capture_issue("warmup", exc))
                    return LiveCaptureReport({}, tuple(issues))
                for width in widths:
                    try:
                        await page.set_viewport_size({"width": int(width), "height": height})
                        response = await page.goto(
                            url,
                            wait_until="domcontentloaded",
                            timeout=GOTO_TIMEOUT_MS,
                        )
                        if require_success_status and (response is None or response.status >= 400):
                            raise ValueError("Unsuccessful preview HTTP status")
                        if settle_container:
                            await _await_container_ready(page)
                        await _await_paint(page)
                        await _await_content(page)
                        out[int(width)] = await page.screenshot(full_page=full_page)
                    except Exception as exc:
                        issues.append(
                            _live_capture_issue("capture", exc, viewport_width=int(width))
                        )
            finally:
                await page.close()
        finally:
            await browser.close()
    return LiveCaptureReport(out, tuple(issues))
