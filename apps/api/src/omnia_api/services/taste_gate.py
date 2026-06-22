"""Objective taste-floor gate — the awwwards PROMISE, machine-checked (V1.6 7/5).

The four gates that landed before this one (``defect_registry``, ``wow_dom_gate``,
``perf_a11y_gate``, ``chip_pixel_gate``) are *correctness / safety floors*: no
dead anchor, no h-scroll, AA contrast, fast, screen-reader-usable, request↔output
fidelity. A bland, perfectly-correct page passes every one of them — a flat
Bootstrap template clears them clean. That is exactly the hole pillar 1 of the
NORTH STAR forbids: "every generated app looks awwwards/enterprise from the FIRST
generation." The retired ``vision`` verdict used to guard taste as a vibe; this
module replaces the vibe with an objective richness floor.

The five richness checks (each worth one point; the gate FAILS below 4/5)
========================================================================
  1. ``font-pairing``   — ≥2 distinct primary font families (a display/body
                          pairing), OR a single CHOSEN webface worked across a
                          real weight range (display weight + body weight) — not
                          one flat system stack everywhere.
  2. ``type-scale``     — ≥3 distinct computed text sizes AND max/min ≥ 2.0
                          (a real typographic scale, not flat body copy).
  3. ``hierarchy``      — the above-the-fold composition has ONE dominant focal
                          element: the largest text is ≥1.8× the above-fold
                          median and is not tied by a crowd of equal headings.
  4. ``layout-variety`` — intentional rhythm: ≥2 distinct INNER content-column
                          widths, OR a full-bleed band (edge-to-edge hero image /
                          colour) alternating with contained content — not one
                          monotone column of cards. (Reads the inner content
                          column, not the outer full-width section rect.)
  5. ``hero-imagery``   — the hero region carries a real image (``<img>`` /
                          ``<svg>`` / ``<video>`` / ``background-image``), not a
                          solid colour plate.

Design — **JS extracts, Python scores** (R-01 deep module)
==========================================================
The injected ``_AUDIT_JS`` does *only* DOM extraction — font families, computed
sizes, element rects, per-section imagery — and returns raw numbers. Every
threshold and verdict lives in pure Python (:func:`evaluate_observation`), so the
whole rubric is unit-testable with a hand-built dict: no browser, no flake. The
async :func:`audit_files` / :func:`audit_url` wrappers are the only
browser-touching code and they fail soft (R-10): a render error yields
``rendered=False`` (the gate ABSTAINS) rather than raising into the caller.

Where the WOW-DOM gate scores a mobile floor at 390px, taste is a *composition*
concern best read where the layout breathes — so this gate's default viewport is
desktop (1440px). The acceptance gauntlet fans it at that width regardless of the
mobile width the correctness gates run at.
"""

from __future__ import annotations

import json
import logging
import statistics
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .render_settle import goto_and_settle
from .surface_class import is_login_surface

if TYPE_CHECKING:
    from playwright.async_api import Page

log = logging.getLogger(__name__)

# Raw DOM observation produced by ``_AUDIT_JS`` and scored by Python. Heterogeneous
# by nature (numbers, nested lists, strings) — the shape is pinned by the JS
# extractor and exercised by the unit tests, not the type system.
Obs = dict[str, Any]

# Taste is a desktop-composition gate: type scale, multi-width rhythm and hero
# imagery read best where the layout is not collapsed to one mobile column.
GATE_WIDTH = 1440
GATE_HEIGHT = 900

# A page must clear at least this many of the five checks to pass.
MIN_SCORE = 4

# ── thresholds (the testable knobs) ───────────────────────────────────────────
# A real type scale spans at least this many distinct computed sizes …
_MIN_DISTINCT_SIZES = 3
# … and the largest is at least this multiple of the smallest.
_TYPE_SCALE_RATIO = 2.0
# The above-fold focal element must tower over the body median by this much.
_HIERARCHY_DOMINANCE = 1.8
# Elements within this fraction of the max size count as "the top tier"; more
# than a couple of them means a crowd of equal headings, not one focal point.
_TOP_TIER_FRACTION = 0.92
_MAX_TOP_TIER = 2
# Section content widths are bucketed to this granularity before counting
# distinct widths — sub-pixel and minor responsive jitter shouldn't read as
# "variety", but a contained column vs a full-bleed band should.
_WIDTH_BUCKET_PX = 64
_MIN_DISTINCT_WIDTHS = 2
# A full-bleed band only reads as *rhythm* when it sits among other sections —
# a lone hero is not an alternation. Require at least this many major sections
# before a full-bleed band can satisfy layout-variety on its own.
_MIN_BANDED_SECTIONS = 2
# A section is "major" (a candidate hero / layout band) only when it is at least
# this tall — skips slivers, nav bars and dividers.
_MAJOR_SECTION_MIN_H = 160
# Round sizes to this precision when counting the type scale (computed px are
# floats; 15.999 and 16.0 are the same step).
_SIZE_ROUND = 0
# A single typeface still reads as a deliberate type *system* — not a default
# stack dropped in everywhere — when it is (a) a CHOSEN webface (not an OS/system
# default family) AND (b) worked across a real weight range: a heavy display
# weight over a regular body weight. The modern single-variable-face enterprise
# look (Onest 700 over Onest 400, Inter 800 over Inter 400) earns the
# font-pairing point this way; a flat system stack with default bold does not.
# Calibrated for the entity hot-path (V1.6 16/5b): real entity landings often
# ship one chosen face, and rejecting them floods false-positives.
_SINGLE_FAMILY_MIN_WEIGHT_SPREAD = 300
# Generic / OS-default family tokens — the "no type choice was made" tells. A
# single one of these never satisfies font-pairing on its own, even with bold.
_SYSTEM_FAMILIES = frozenset(
    {
        "system-ui",
        "-apple-system",
        "blinkmacsystemfont",
        "segoe ui",
        "sans-serif",
        "serif",
        "monospace",
        "ui-sans-serif",
        "ui-serif",
        "ui-monospace",
        "arial",
        "helvetica",
        "helvetica neue",
        "times",
        "times new roman",
        "courier",
        "courier new",
    }
)
# Framework dev-tooling fonts are not part of the app design: the Next.js dev
# overlay (toasts, error dialog, build indicator) paints in ``__nextjs-Geist``.
# Drop families with this prefix before counting, so a LIVE dev container is
# scored on its real typeface, not the devtools chrome (V1.6 16/5b). The prefix
# is deliberately narrow — ``next/font`` exposes real family names in prod
# builds (measured: "manrope", "dm sans"), never ``__nextjs`` hashes.
_FRAMEWORK_FONT_PREFIXES = ("__nextjs",)


# Check ids — the vocabulary of the subscore.
FONT_PAIRING = "font-pairing"
TYPE_SCALE = "type-scale"
HIERARCHY = "hierarchy"
LAYOUT_VARIETY = "layout-variety"
HERO_IMAGERY = "hero-imagery"

CHECKS: tuple[str, ...] = (FONT_PAIRING, TYPE_SCALE, HIERARCHY, LAYOUT_VARIETY, HERO_IMAGERY)


# ── public result types ───────────────────────────────────────────────────────


@dataclass(frozen=True)
class TasteFinding:
    """One richness check the page failed."""

    check: str
    detail: str


@dataclass(frozen=True)
class TasteReport:
    """Verdict + JSON subscore of one live-DOM taste audit.

    Shares the rendered-gate interface (``passed`` / ``rendered`` / ``classes`` /
    ``summary`` / ``subscore``) so the acceptance gauntlet folds it in through the
    same adapter as the WOW-DOM / perf / chip gates.
    """

    findings: tuple[TasteFinding, ...]
    score: int
    viewport_width: int
    fonts: tuple[str, ...]
    rendered: bool
    detail: dict[str, Any] = field(default_factory=dict)
    #: ``"content"`` for a normal landing / dashboard; ``"login"`` when the page
    #: is a sparse auth surface, in which case the landing richness rubric is
    #: WAIVED (the wrong surface to score — V1.6 16/5d) rather than false-failed.
    surface: str = "content"

    @property
    def passed(self) -> bool:
        """A gate that never rendered ABSTAINS (not pass) — it has no evidence.

        A ``login`` surface PASSES as waived: the landing richness rubric does
        not apply to a centred auth card (V1.6 16/5d).
        """
        if not self.rendered:
            return False
        if self.surface == "login":
            return True
        return self.score >= MIN_SCORE

    @property
    def classes(self) -> tuple[str, ...]:
        """The failed checks, in canonical order (what the gauntlet table shows)."""
        hit = {f.check for f in self.findings}
        return tuple(c for c in CHECKS if c in hit)

    def subscore(self) -> dict[str, Any]:
        """Machine-readable subscore — emitted into the gauntlet's JSON."""
        return {
            "gate": "taste",
            "rendered": self.rendered,
            "passed": self.passed,
            "score": self.score,
            "max_score": len(CHECKS),
            "viewport_width": self.viewport_width,
            "surface": self.surface,
            "checks": {c: c not in self.classes for c in CHECKS},
            "fonts": list(self.fonts),
            "detail": self.detail,
        }

    def summary(self) -> str:
        if not self.rendered:
            return "taste: ABSTAIN (render harness did not run)"
        if self.surface == "login":
            return (
                "taste: WAIVED (login surface — landing richness rubric N/A) "
                f"@ {self.viewport_width}px"
            )
        if self.passed:
            return f"taste: {self.score}/{len(CHECKS)} richness checks @ {self.viewport_width}px"
        lines = [
            f"taste: {self.score}/{len(CHECKS)} richness (< {MIN_SCORE} floor) "
            f"@ {self.viewport_width}px — {len(self.findings)} miss(es):"
        ]
        for f in self.findings:
            lines.append(f"  [{f.check}] {f.detail}")
        return "\n".join(lines)


# ── helpers (pure) ─────────────────────────────────────────────────────────────


def normalize_family(family: str | None) -> str:
    """First token of a ``font-family`` stack, lowercased and unquoted.

    ``'"Playfair Display", Georgia, serif'`` → ``playfair display``. The first
    token is the one actually painted when the font loads; comparing first tokens
    is how we tell a display/body pairing from one stack used everywhere.
    """
    if not family:
        return ""
    first = family.split(",")[0].strip().strip("\"'").lower()
    return first


def _is_framework_family(fam: str) -> bool:
    """A framework dev-tooling font (e.g. the Next.js dev overlay's Geist) — not
    part of the app's own type choice, so it must not count toward pairing."""
    return any(fam.startswith(p) for p in _FRAMEWORK_FONT_PREFIXES)


def _distinct_families(obs: Obs) -> list[str]:
    fams: list[str] = []
    for t in obs.get("texts", ()):
        fam = normalize_family(t.get("family"))
        if fam and not _is_framework_family(fam) and fam not in fams:
            fams.append(fam)
    return fams


def _family_weights(obs: Obs, family: str) -> set[int]:
    """Distinct font-weights painted in ``family`` across visible text — a single
    face worked across a heavy + regular weight is a deliberate type system."""
    out: set[int] = set()
    for t in obs.get("texts", ()):
        if normalize_family(t.get("family")) == family:
            out.add(int(t.get("weight") or 400))
    return out


def _text_sizes(obs: Obs) -> list[float]:
    sizes = (float(t.get("size") or 0) for t in obs.get("texts", ()))
    return [s for s in sizes if s > 0]


def _above_fold_sizes(obs: Obs) -> list[float]:
    vh = float(obs.get("viewportHeight") or GATE_HEIGHT)
    out: list[float] = []
    for t in obs.get("texts", ()):
        top = float(t.get("top", 0))
        size = float(t.get("size") or 0)
        if size > 0 and -size <= top < vh:
            out.append(size)
    return out


def _bucket(width: float) -> int:
    return round(float(width) / _WIDTH_BUCKET_PX)


def _major_sections(obs: Obs) -> list[dict[str, Any]]:
    return [
        s
        for s in obs.get("sections", ())
        if float(s.get("height") or 0) >= _MAJOR_SECTION_MIN_H and float(s.get("width") or 0) > 0
    ]


def _content_width(section: dict[str, Any]) -> float:
    """Width of a section's INNER content column, not its outer rect.

    Modern layouts wrap a full-width ``<section>`` around a centred max-width
    container, so every section *rect* is the viewport width and the real rhythm
    lives one level in. ``contentWidth`` is emitted by the extractor; fall back
    to the section width for hand-built observations that omit it.
    """
    return float(section.get("contentWidth") or section.get("width") or 0)


# ── the five richness checks (each returns a finding list — empty == passed) ───


def _score_font_pairing(obs: Obs) -> list[TasteFinding]:
    fams = _distinct_families(obs)
    if len(fams) >= 2:
        return []
    # A single CHOSEN webface, worked across a real weight range (a heavy display
    # weight over a regular body weight), still reads as a deliberate type system
    # — the modern single-variable-face enterprise look. A system default stack,
    # or a single flat weight, does not earn the point.
    if len(fams) == 1 and fams[0] not in _SYSTEM_FAMILIES:
        weights = _family_weights(obs, fams[0])
        if weights and max(weights) - min(weights) >= _SINGLE_FAMILY_MIN_WEIGHT_SPREAD:
            return []
    shown = fams[0] if fams else "(none)"
    return [
        TasteFinding(
            FONT_PAIRING,
            f"only {len(fams)} flat font family ({shown}) — pair a display + body "
            f"face, or work one chosen face across a real weight range",
        )
    ]


def _score_type_scale(obs: Obs) -> list[TasteFinding]:
    sizes = _text_sizes(obs)
    distinct = sorted({round(s, _SIZE_ROUND) for s in sizes})
    if len(distinct) < _MIN_DISTINCT_SIZES:
        return [
            TasteFinding(
                TYPE_SCALE,
                f"only {len(distinct)} distinct text size(s) — flat type, no scale",
            )
        ]
    ratio = distinct[-1] / distinct[0] if distinct[0] else 0.0
    if ratio < _TYPE_SCALE_RATIO:
        return [
            TasteFinding(
                TYPE_SCALE,
                f"type scale max/min {ratio:.1f}× < {_TYPE_SCALE_RATIO:.0f}× "
                f"({distinct[0]:.0f}→{distinct[-1]:.0f}px) — too even",
            )
        ]
    return []


def _score_hierarchy(obs: Obs) -> list[TasteFinding]:
    sizes = _above_fold_sizes(obs)
    if len(sizes) < 2:
        return [
            TasteFinding(
                HIERARCHY,
                "no measurable above-the-fold type hierarchy",
            )
        ]
    mx = max(sizes)
    median = statistics.median(sizes)
    dominance = mx / median if median else 0.0
    top_tier = sum(1 for s in sizes if s >= mx * _TOP_TIER_FRACTION)
    if dominance < _HIERARCHY_DOMINANCE:
        return [
            TasteFinding(
                HIERARCHY,
                f"largest text only {dominance:.1f}× the median "
                f"(< {_HIERARCHY_DOMINANCE:.1f}×) — no dominant focal element",
            )
        ]
    if top_tier > _MAX_TOP_TIER:
        return [
            TasteFinding(
                HIERARCHY,
                f"{top_tier} equally-large headings compete above the fold "
                f"(> {_MAX_TOP_TIER}) — no single focal point",
            )
        ]
    return []


def _score_layout_variety(obs: Obs) -> list[TasteFinding]:
    sections = _major_sections(obs)
    # Rhythm reads two ways, either of which is an intentional layout: distinct
    # INNER content-column widths (a constrained text block next to a wide hero),
    # or a full-bleed band (edge-to-edge hero image / colour) alternating with
    # contained content. Measuring the outer section rect alone false-fails the
    # ubiquitous full-width-section + centred-container pattern, where every rect
    # is the viewport width (the 16/5b calibration: real entity apps scored 1
    # rect-width and were wrongly flagged monotone).
    buckets = {_bucket(_content_width(s)) for s in sections}
    if len(buckets) >= _MIN_DISTINCT_WIDTHS:
        return []
    full_bleed = sum(1 for s in sections if s.get("fullBleed"))
    if full_bleed >= 1 and len(sections) >= _MIN_BANDED_SECTIONS:
        return []
    return [
        TasteFinding(
            LAYOUT_VARIETY,
            f"{len(sections)} major section(s), {len(buckets)} content width(s), "
            f"{full_bleed} full-bleed band(s) — monotone single-column layout",
        )
    ]


def _score_hero_imagery(obs: Obs) -> list[TasteFinding]:
    sections = _major_sections(obs)
    if not sections:
        return [TasteFinding(HERO_IMAGERY, "no hero section detected")]
    hero = min(sections, key=lambda s: float(s.get("top", 0)))
    if hero.get("hasImage"):
        return []
    return [
        TasteFinding(
            HERO_IMAGERY,
            "hero is a solid plate — no image / illustration / video",
        )
    ]


def evaluate_observation(obs: Obs, *, rendered: bool = True) -> TasteReport:
    """Score a raw DOM observation dict → :class:`TasteReport`.

    This is the whole rubric, browser-free. ``obs`` is exactly what ``_AUDIT_JS``
    returns; passing a hand-built dict is how the gate is unit-tested.
    """
    vw = int(obs.get("viewportWidth") or GATE_WIDTH)
    fonts = tuple(_distinct_families(obs))
    if not rendered:
        return TasteReport((), 0, vw, fonts, rendered=False)

    # A sparse, password-bearing auth surface is the WRONG surface for the
    # landing richness rubric (no hero, no multi-width rhythm, sparse type).
    # Waive it rather than false-fail a good centred login (V1.6 16/5d); the
    # dashboard — the real WOW surface — sits behind auth, unreachable here.
    if is_login_surface(obs):
        return TasteReport(
            (), len(CHECKS), vw, fonts, rendered=True, surface="login",
            detail={"surface": "login", "above_fold_texts": len(_above_fold_sizes(obs))},
        )

    findings: list[TasteFinding] = []
    findings += _score_font_pairing(obs)
    findings += _score_type_scale(obs)
    findings += _score_hierarchy(obs)
    findings += _score_layout_variety(obs)
    findings += _score_hero_imagery(obs)

    failed = {f.check for f in findings}
    score = sum(1 for c in CHECKS if c not in failed)
    detail = {
        "above_fold_texts": len(_above_fold_sizes(obs)),
        "major_sections": len(_major_sections(obs)),
        "distinct_sizes": len({round(s, _SIZE_ROUND) for s in _text_sizes(obs)}),
    }
    return TasteReport(tuple(findings), score, vw, fonts, rendered=True, detail=detail)


# ── the DOM extractor (data only — all scoring is in Python above) ────────────

# Returns the raw observation object scored by evaluate_observation(). It reads;
# it never judges. Text nodes carry their painted font family / size / position;
# sections carry their rect plus whether they contain a real image (so Python can
# pick the hero and ask whether it is a solid plate).
_AUDIT_JS = r"""
() => {
  const px = (v) => parseFloat(v) || 0;
  const visible = (el) => {
    const cs = getComputedStyle(el);
    if (cs.display === 'none' || cs.visibility === 'hidden' || px(cs.opacity) === 0) return false;
    const r = el.getBoundingClientRect();
    return r.width > 1 && r.height > 1;
  };
  const hasRealImage = (root) => {
    if (root.querySelector('img[src], svg, video, picture, canvas')) return true;
    const els = [root, ...root.querySelectorAll('*')];
    for (let i = 0; i < els.length && i < 400; i++) {
      const bi = getComputedStyle(els[i]).backgroundImage;
      if (bi && bi !== 'none' && /url\(|image-set\(/.test(bi)) return true;
    }
    return false;
  };
  // Next.js dev tooling renders an overlay (toasts, build indicator, error
  // dialog) into the live DEV container DOM. It is devtools chrome, not the app,
  // so skip any node inside a known dev-overlay container before measuring —
  // production builds carry none of this (V1.6 16/5b strip).
  const DEV_OVERLAY_SEL = 'nextjs-portal, [data-nextjs-toast], [data-nextjs-dialog], ' +
    '[data-nextjs-dialog-overlay], #__next-build-watcher, #__next-prerender-indicator';
  const inDevOverlay = (el) => !!(el && el.closest && el.closest(DEV_OVERLAY_SEL));
  // A painted (non-transparent) colour — alpha 0 / 'transparent' reads as none.
  const isOpaque = (c) => !!c && c !== 'transparent' && !/,\s*0\s*\)/.test(c);
  const bodyBg = getComputedStyle(document.body).backgroundColor;
  const vw = document.documentElement.clientWidth;
  // Width of a section's inner content column — the widest visible descendant
  // meaningfully narrower than the band itself (the centred max-width wrapper).
  const innerContentWidth = (el, bandW) => {
    let inner = 0;
    const kids = el.querySelectorAll('*');
    for (let i = 0; i < kids.length && i < 300; i++) {
      const kr = kids[i].getBoundingClientRect();
      if (kr.height > 40 && kr.width < bandW * 0.95 && kr.width > inner) inner = kr.width;
    }
    return inner > bandW * 0.2 ? inner : bandW;
  };

  // text nodes — painted family / size / weight / vertical position
  const texts = [];
  const tw = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  let tn;
  while ((tn = tw.nextNode())) {
    const s = tn.nodeValue.replace(/\s+/g, ' ').trim();
    if (!s) continue;
    const el = tn.parentElement;
    if (!el || !visible(el) || inDevOverlay(el)) continue;
    const cs = getComputedStyle(el);
    const r = el.getBoundingClientRect();
    texts.push({
      family: cs.fontFamily || '',
      size: px(cs.fontSize),
      weight: parseInt(cs.fontWeight) || 400,
      top: r.top,
      sample: s.slice(0, 40),
    });
    if (texts.length >= 500) break;
  }

  // candidate layout sections — semantic landmarks plus tall top-level blocks
  const seen = new Set();
  const sections = [];
  const sel = 'section, header, footer, main, article, ' +
              '[class*="hero" i], [class*="section" i], main > div, body > div';
  document.querySelectorAll(sel).forEach((el) => {
    if (seen.has(el) || !visible(el) || inDevOverlay(el)) return;
    seen.add(el);
    const r = el.getBoundingClientRect();
    const img = hasRealImage(el);
    const bandBg = getComputedStyle(el).backgroundColor;
    // full-bleed: spans the viewport edge-to-edge AND paints something distinct
    // (a hero image, or a background colour ≠ the page background).
    const fullBleed = r.width >= vw * 0.98 &&
      (img || (isOpaque(bandBg) && bandBg !== bodyBg));
    sections.push({
      width: r.width,
      height: r.height,
      top: r.top,
      hasImage: img,
      contentWidth: innerContentWidth(el, r.width),
      fullBleed,
    });
  });

  return {
    viewportWidth: document.documentElement.clientWidth,
    viewportHeight: document.documentElement.clientHeight,
    // A real password input is the intentional-auth tell the surface classifier
    // reads to waive a sparse login page instead of false-failing it (16/5d).
    hasPassword: !!document.querySelector('input[type="password"]'),
    texts, sections,
  };
}
"""


# ── async render harnesses (the only browser-touching code; fail soft) ────────


async def _audit_page(page: Page) -> TasteReport:
    obs = await page.evaluate(_AUDIT_JS)
    return evaluate_observation(obs)


async def audit_url(
    url: str,
    *,
    width: int = GATE_WIDTH,
    timeout_ms: int = 15_000,
    storage_state: dict | None = None,
) -> TasteReport:
    """Audit a LIVE url (a running container app / prod ``/p/<slug>``) at ``width``.

    Pass ``storage_state`` (a Playwright storage-state dict) to render an
    authenticated cabinet — the context then carries the session cookie. With
    ``storage_state=None`` the context is anonymous and byte-identical to the
    previous plain ``new_page`` path, so the default audit is unchanged.

    Fail-soft: any render/navigation error → an ABSTAIN report (``rendered=False``)
    rather than a raise, so a flaky container never hard-fails the gauntlet (R-10).
    """
    try:
        from playwright.async_api import async_playwright

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            try:
                context = await browser.new_context(
                    viewport={"width": int(width), "height": GATE_HEIGHT},
                    reduced_motion="reduce",
                    storage_state=storage_state,
                )
                page = await context.new_page()
                try:
                    await goto_and_settle(page, url, timeout_ms=timeout_ms)
                    return await _audit_page(page)
                finally:
                    await context.close()
            finally:
                await browser.close()
    except Exception as exc:
        log.warning("taste_gate: url audit failed (abstain): %r", exc)
        return TasteReport((), 0, int(width), (), rendered=False)


async def audit_files(
    files: dict[str, str], *, width: int = GATE_WIDTH, timeout_ms: int = 15_000
) -> TasteReport:
    """Audit a static ``{path: html}`` page set at ``width`` (needs index.html)."""
    if "index.html" not in files:
        return TasteReport((), 0, int(width), (), rendered=False)
    try:
        from playwright.async_api import async_playwright

        with tempfile.TemporaryDirectory(prefix="omnia-taste-") as tmp:
            workdir = Path(tmp)
            for path, content in files.items():
                full = workdir / path
                full.parent.mkdir(parents=True, exist_ok=True)
                full.write_text(content, encoding="utf-8")
            index_uri = (workdir / "index.html").as_uri()

            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                try:
                    page = await browser.new_page(
                        viewport={"width": int(width), "height": GATE_HEIGHT},
                        reduced_motion="reduce",
                    )
                    try:
                        await goto_and_settle(page, index_uri, timeout_ms=timeout_ms)
                        return await _audit_page(page)
                    finally:
                        await page.close()
                finally:
                    await browser.close()
    except Exception as exc:
        log.warning("taste_gate: files audit failed (abstain): %r", exc)
        return TasteReport((), 0, int(width), (), rendered=False)


def _main(argv: list[str]) -> int:  # pragma: no cover — thin CLI wrapper
    import asyncio

    if len(argv) < 2:
        print("usage: python -m omnia_api.services.taste_gate <url|index.html-dir>")
        return 2
    target = argv[1]
    if target.startswith(("http://", "https://")):
        report = asyncio.run(audit_url(target))
    else:
        root = Path(target)
        files = {
            str(p.relative_to(root)): p.read_text(encoding="utf-8")
            for p in root.rglob("*.html")
        }
        report = asyncio.run(audit_files(files))
    print(report.summary())
    print(json.dumps(report.subscore(), ensure_ascii=False, indent=2))
    return 0 if report.passed else 1


if __name__ == "__main__":  # pragma: no cover
    import sys

    raise SystemExit(_main(sys.argv))


__all__ = [
    "CHECKS",
    "GATE_WIDTH",
    "MIN_SCORE",
    "TasteFinding",
    "TasteReport",
    "audit_files",
    "audit_url",
    "evaluate_observation",
    "normalize_family",
]
