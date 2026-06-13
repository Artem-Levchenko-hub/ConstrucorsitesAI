"""Hierarchy-richness gate — composition teeth stricter than the count-checks (V1.6 9/5).

The taste gate (7/5) *counts* richness signals: distinct font families, distinct
text sizes, a multi-width rhythm, a hero image. Counting is a weak proxy for
beauty — a competent-but-forgettable page clears every count and still looks like
"generic AI": uniform body type, no single focal element, three identical cards
in a row. This gate reads the LIVE composition and asks whether the above-fold
arrangement carries an intentional focal hierarchy, not just the right ingredient
counts.

The three richness checks (the gate FAILS below 2/3)
====================================================
  1. ``type-dominance``  — the largest visible text is ≥ 2.2× the *whole-page*
                           median text size. Stricter than taste's above-fold
                           ``hierarchy`` (which reads the above-fold median at
                           1.8×): a page whose hero is fine but whose body is flat
                           type everywhere fails here where taste passes.
  2. ``focal-dominance`` — exactly one *visual* element (``<img>`` / ``<svg>`` /
                           ``<video>`` / ``url()`` ``background-image``) covers
                           ≥ 25% of the above-the-fold area. Taste only asks
                           whether the hero *contains* imagery (a 48px icon
                           counts); this asks for a substantial dominant visual
                           and rejects two equally-large visuals competing for
                           the eye. Layout wrappers are skipped by excluding any
                           visual that contains another visual (a full-bleed
                           ``background-image`` hero counts as its own focal; a
                           ``<section>`` that merely wraps the hero ``<img>`` does
                           not).
  3. ``asymmetry``       — no generic equal-width N-card row: ≥ 3 sibling cards of
                           the same width, height and baseline laid out in a row.
                           This deterministically catches the "generic-AI 3-card
                           grid" and enforces ``AWWWARDS_PRINCIPLES`` rule 4
                           (intentional asymmetry), which is otherwise prose with
                           zero gate.

Why a 2/3 floor (not all-three) — calibration safety
====================================================
A 3-equal-card row is *not* a sin on its own: good enterprise pages use them for
pricing tiers, feature highlights and catalog grids. Banning them outright would
false-fail good pages (the same trap the WOW-DOM 44px small-target hit — see
plan §7). So ``asymmetry`` is a tie-breaker, never decisive alone: a page with a
real focal hierarchy passes ``type-dominance`` + ``focal-dominance`` (2/3) and
ships regardless of its card grids. Only a page *also* weak on focal hierarchy —
flat type or no dominant visual *and* a uniform card grid — drops below the
floor. That is precisely the "competent-but-forgettable / generic-AI" page this
gate is meant to reject without certifying-by-accident a denser dashboard.

Design — **JS extracts, Python scores** (R-01 deep module)
==========================================================
The injected ``_AUDIT_JS`` does *only* DOM extraction — text sizes, per-visual
above-fold area fractions, sibling-child rects — and returns raw numbers. Every
threshold and verdict lives in pure Python (:func:`evaluate_observation`), so the
whole rubric is unit-testable with a hand-built dict: no browser, no flake. The
async :func:`audit_files` / :func:`audit_url` wrappers are the only
browser-touching code and they fail soft (R-10): a render error yields
``rendered=False`` (the gate ABSTAINS) rather than raising into the caller.

Like taste, this is a *composition* gate read at desktop width (1440px) where the
layout breathes; the acceptance gauntlet fans it at that width regardless of the
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

if TYPE_CHECKING:
    from playwright.async_api import Page

log = logging.getLogger(__name__)

# Raw DOM observation produced by ``_AUDIT_JS`` and scored by Python. Heterogeneous
# by nature (numbers, nested lists) — the shape is pinned by the JS extractor and
# exercised by the unit tests, not the type system.
Obs = dict[str, Any]

# Composition reads best where the layout is not collapsed to one mobile column.
GATE_WIDTH = 1440
GATE_HEIGHT = 900

# A page must clear at least this many of the three checks to pass. A 2/3 floor
# makes ``asymmetry`` a tie-breaker (see module docstring) rather than an
# over-strict ban on every card row.
MIN_SCORE = 2

# ── thresholds (the testable knobs) ───────────────────────────────────────────
# The largest visible text must tower over the whole-page median by this much.
_TYPE_DOMINANCE_RATIO = 2.2
# A visual is a "focal dominant" only when it covers at least this fraction of the
# above-the-fold viewport area.
_FOCAL_MIN_FRAC = 0.25
# Exactly this many focal dominants is the single-focal sweet spot.
_FOCAL_EXACT = 1
# Card-row detector — what counts as a "generic equal-width N-card row".
_CARD_MIN_COUNT = 3
# Cards shorter than this are buttons / chips / nav links, not content cards.
_CARD_MIN_H = 60.0
# Each card's width as a fraction of the viewport must sit in this band: wider
# than a chip, narrower than a full-bleed stacked block.
_CARD_MIN_WFRAC = 0.12
_CARD_MAX_WFRAC = 0.42
# Children whose tops fall within this many px share a row baseline.
_CARD_TOP_BUCKET = 24.0
# "Equal" width / height tolerances (max/min ratio) — sub-pixel and minor
# responsive jitter shouldn't break the match, but a deliberate size step should.
_CARD_WIDTH_RATIO = 1.12
_CARD_HEIGHT_RATIO = 1.30


# Check ids — the vocabulary of the subscore.
TYPE_DOMINANCE = "type-dominance"
FOCAL_DOMINANCE = "focal-dominance"
ASYMMETRY = "asymmetry"

CHECKS: tuple[str, ...] = (TYPE_DOMINANCE, FOCAL_DOMINANCE, ASYMMETRY)


# ── public result types ───────────────────────────────────────────────────────


@dataclass(frozen=True)
class HierarchyFinding:
    """One richness check the page failed."""

    check: str
    detail: str


@dataclass(frozen=True)
class HierarchyReport:
    """Verdict + JSON subscore of one live-DOM hierarchy audit.

    Shares the rendered-gate interface (``passed`` / ``rendered`` / ``classes`` /
    ``summary`` / ``subscore``) so the acceptance gauntlet folds it in through the
    same adapter as the WOW-DOM / perf / chip / taste gates.
    """

    findings: tuple[HierarchyFinding, ...]
    score: int
    viewport_width: int
    rendered: bool
    detail: dict[str, Any] = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        """A gate that never rendered ABSTAINS (not pass) — it has no evidence."""
        return self.rendered and self.score >= MIN_SCORE

    @property
    def classes(self) -> tuple[str, ...]:
        """The failed checks, in canonical order (what the gauntlet table shows)."""
        hit = {f.check for f in self.findings}
        return tuple(c for c in CHECKS if c in hit)

    def subscore(self) -> dict[str, Any]:
        """Machine-readable subscore — emitted into the gauntlet's JSON."""
        return {
            "gate": "hierarchy",
            "rendered": self.rendered,
            "passed": self.passed,
            "score": self.score,
            "max_score": len(CHECKS),
            "viewport_width": self.viewport_width,
            "checks": {c: c not in self.classes for c in CHECKS},
            "detail": self.detail,
        }

    def summary(self) -> str:
        if not self.rendered:
            return "hierarchy: ABSTAIN (render harness did not run)"
        if self.passed:
            return (
                f"hierarchy: {self.score}/{len(CHECKS)} richness checks "
                f"@ {self.viewport_width}px"
            )
        lines = [
            f"hierarchy: {self.score}/{len(CHECKS)} richness (< {MIN_SCORE} floor) "
            f"@ {self.viewport_width}px — {len(self.findings)} miss(es):"
        ]
        for f in self.findings:
            lines.append(f"  [{f.check}] {f.detail}")
        return "\n".join(lines)


# ── helpers (pure) ─────────────────────────────────────────────────────────────


def _text_sizes(obs: Obs) -> list[float]:
    sizes = (float(t.get("size") or 0) for t in obs.get("texts", ()))
    return [s for s in sizes if s > 0]


def _focal_dominants(obs: Obs) -> list[dict[str, Any]]:
    """Visual elements that are their own focal block (not a wrapper) and cover at
    least ``_FOCAL_MIN_FRAC`` of the above-the-fold area."""
    out: list[dict[str, Any]] = []
    for v in obs.get("visuals", ()):
        if v.get("containsVisual"):
            continue  # a layout wrapper around a more specific visual — skip it
        if float(v.get("frac") or 0) >= _FOCAL_MIN_FRAC:
            out.append(v)
    return out


def _card_row(children: list[dict[str, Any]], vw: float) -> list[dict[str, Any]] | None:
    """Return the largest equal-width card row among one parent's children, or None.

    A "card row" is ≥ ``_CARD_MIN_COUNT`` siblings sharing a baseline (top), each
    tall enough to be a content card and sized within the card-width band, whose
    widths and heights are equal within tolerance. ``children`` are raw child rects
    ``{l, w, h, top}``; ``vw`` is the viewport width for the width-fraction band.
    """
    if vw <= 0:
        return None
    cards = [
        c
        for c in children
        if float(c.get("h") or 0) >= _CARD_MIN_H
        and _CARD_MIN_WFRAC <= float(c.get("w") or 0) / vw <= _CARD_MAX_WFRAC
    ]
    if len(cards) < _CARD_MIN_COUNT:
        return None
    # Bucket by baseline top; the largest same-baseline run is the candidate row.
    buckets: dict[int, list[dict[str, Any]]] = {}
    for c in cards:
        key = round(float(c.get("top") or 0) / _CARD_TOP_BUCKET)
        buckets.setdefault(key, []).append(c)
    row = max(buckets.values(), key=len)
    if len(row) < _CARD_MIN_COUNT:
        return None
    widths = [float(c["w"]) for c in row]
    heights = [float(c["h"]) for c in row]
    if min(widths) <= 0 or min(heights) <= 0:
        return None
    if max(widths) / min(widths) > _CARD_WIDTH_RATIO:
        return None
    if max(heights) / min(heights) > _CARD_HEIGHT_RATIO:
        return None
    return row


def _find_card_row(obs: Obs) -> tuple[int, float] | None:
    """First generic card row anywhere on the page → ``(count, ~width_px)``."""
    vw = float(obs.get("viewportWidth") or GATE_WIDTH)
    for group in obs.get("groups", ()):
        row = _card_row(list(group), vw)
        if row:
            avg_w = statistics.mean(float(c["w"]) for c in row)
            return len(row), avg_w
    return None


# ── the three richness checks (each returns a finding list — empty == passed) ──


def _score_type_dominance(obs: Obs) -> list[HierarchyFinding]:
    sizes = _text_sizes(obs)
    if len(sizes) < 2:
        return [
            HierarchyFinding(
                TYPE_DOMINANCE,
                "no measurable type hierarchy (fewer than 2 text elements)",
            )
        ]
    mx = max(sizes)
    median = statistics.median(sizes)
    ratio = mx / median if median else 0.0
    if ratio < _TYPE_DOMINANCE_RATIO:
        return [
            HierarchyFinding(
                TYPE_DOMINANCE,
                f"largest text only {ratio:.1f}× the page median "
                f"(< {_TYPE_DOMINANCE_RATIO:.1f}×; {median:.0f}→{mx:.0f}px) — flat type",
            )
        ]
    return []


def _score_focal_dominance(obs: Obs) -> list[HierarchyFinding]:
    dominants = _focal_dominants(obs)
    n = len(dominants)
    if n == _FOCAL_EXACT:
        return []
    if n == 0:
        return [
            HierarchyFinding(
                FOCAL_DOMINANCE,
                f"no visual covers ≥ {_FOCAL_MIN_FRAC:.0%} of the above-fold area "
                "— no dominant focal element",
            )
        ]
    return [
        HierarchyFinding(
            FOCAL_DOMINANCE,
            f"{n} visuals each cover ≥ {_FOCAL_MIN_FRAC:.0%} of the above-fold area "
            "— competing focal points, no single anchor",
        )
    ]


def _score_asymmetry(obs: Obs) -> list[HierarchyFinding]:
    hit = _find_card_row(obs)
    if hit is None:
        return []
    count, width = hit
    return [
        HierarchyFinding(
            ASYMMETRY,
            f"generic {count}-card row (~{width:.0f}px each, equal width & height) "
            "— no intentional asymmetry",
        )
    ]


def evaluate_observation(obs: Obs, *, rendered: bool = True) -> HierarchyReport:
    """Score a raw DOM observation dict → :class:`HierarchyReport`.

    This is the whole rubric, browser-free. ``obs`` is exactly what ``_AUDIT_JS``
    returns; passing a hand-built dict is how the gate is unit-tested.
    """
    vw = int(obs.get("viewportWidth") or GATE_WIDTH)
    if not rendered:
        return HierarchyReport((), 0, vw, rendered=False)

    findings: list[HierarchyFinding] = []
    findings += _score_type_dominance(obs)
    findings += _score_focal_dominance(obs)
    findings += _score_asymmetry(obs)

    failed = {f.check for f in findings}
    score = sum(1 for c in CHECKS if c not in failed)
    detail = {
        "text_count": len(_text_sizes(obs)),
        "focal_dominants": len(_focal_dominants(obs)),
        "card_row": _find_card_row(obs) is not None,
    }
    return HierarchyReport(tuple(findings), score, vw, rendered=True, detail=detail)


# ── the DOM extractor (data only — all scoring is in Python above) ────────────

# Returns the raw observation object scored by evaluate_observation(). It reads;
# it never judges. ``texts`` carry painted font sizes; ``visuals`` carry each
# image/background-image element's above-fold area fraction plus whether it wraps
# another visual (so Python can pick the leaf focal); ``groups`` carry the child
# rects of every small sibling set (so Python can find a generic card row).
_AUDIT_JS = r"""
() => {
  const px = (v) => parseFloat(v) || 0;
  const visible = (el) => {
    const cs = getComputedStyle(el);
    if (cs.display === 'none' || cs.visibility === 'hidden' || px(cs.opacity) === 0) return false;
    const r = el.getBoundingClientRect();
    return r.width > 1 && r.height > 1;
  };
  const vw = document.documentElement.clientWidth;
  const vh = document.documentElement.clientHeight;
  const foldArea = Math.max(1, vw * vh);
  // Fraction of the first viewport an element's painted box covers.
  const foldFrac = (r) => {
    const w = Math.max(0, Math.min(r.right, vw) - Math.max(r.left, 0));
    const h = Math.max(0, Math.min(r.bottom, vh) - Math.max(r.top, 0));
    return (w * h) / foldArea;
  };
  const isVisualEl = (el) => {
    const tag = el.tagName;
    if (tag === 'IMG' || tag === 'SVG' || tag === 'VIDEO' || tag === 'PICTURE' ||
        tag === 'CANVAS') return true;
    const bi = getComputedStyle(el).backgroundImage;
    return !!bi && bi !== 'none' && /url\(|image-set\(/.test(bi);
  };

  // text nodes — painted size + position
  const texts = [];
  const tw = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  let tn;
  while ((tn = tw.nextNode())) {
    const s = tn.nodeValue.replace(/\s+/g, ' ').trim();
    if (!s) continue;
    const el = tn.parentElement;
    if (!el || !visible(el)) continue;
    const cs = getComputedStyle(el);
    const r = el.getBoundingClientRect();
    texts.push({ size: px(cs.fontSize), top: r.top });
    if (texts.length >= 600) break;
  }

  // visual elements — above-fold area fraction + wrapper flag
  const visualEls = [];
  const all = document.body.querySelectorAll('*');
  for (let i = 0; i < all.length && i < 4000; i++) {
    const el = all[i];
    if (!visible(el) || !isVisualEl(el)) continue;
    visualEls.push(el);
    if (visualEls.length >= 200) break;
  }
  const visuals = visualEls.map((el) => {
    const r = el.getBoundingClientRect();
    const containsVisual = visualEls.some((o) => o !== el && el.contains(o));
    return { frac: foldFrac(r), top: r.top, containsVisual };
  });

  // sibling card-row candidates — small sets of element children
  const groups = [];
  const parents = [document.body, ...document.body.querySelectorAll('*')];
  for (let i = 0; i < parents.length && i < 4000 && groups.length < 120; i++) {
    const p = parents[i];
    const kids = [...p.children].filter(visible);
    if (kids.length < 3 || kids.length > 12) continue;
    groups.push(kids.map((k) => {
      const r = k.getBoundingClientRect();
      return { l: r.left, w: r.width, h: r.height, top: r.top };
    }));
  }

  return { viewportWidth: vw, viewportHeight: vh, texts, visuals, groups };
}
"""


# ── async render harnesses (the only browser-touching code; fail soft) ────────


async def _audit_page(page: Page) -> HierarchyReport:
    obs = await page.evaluate(_AUDIT_JS)
    return evaluate_observation(obs)


async def _settle(page: Page) -> None:
    """Best-effort: let a client-rendered app actually paint before we read it.

    The generated apps' public ``/p/<slug>`` page is a Next.js client render —
    its content (hero, imagery, cards) lands *after* ``load``, so reading at
    ``domcontentloaded`` sees an empty shell and false-fails every check. Wait for
    the network to quiesce, fonts to load, then a paint beat. Every step is
    best-effort and never blocks the read (R-10)."""
    try:
        await page.wait_for_load_state("networkidle", timeout=8_000)
    except Exception:
        pass
    try:
        await page.evaluate("() => document.fonts.ready")
    except Exception:
        pass
    try:
        await page.wait_for_timeout(900)
    except Exception:
        pass


async def audit_url(
    url: str, *, width: int = GATE_WIDTH, timeout_ms: int = 15_000
) -> HierarchyReport:
    """Audit a LIVE url (a running container app / prod ``/p/<slug>``) at ``width``.

    Fail-soft: any render/navigation error → an ABSTAIN report (``rendered=False``)
    rather than a raise, so a flaky container never hard-fails the gauntlet (R-10).
    """
    try:
        from playwright.async_api import async_playwright

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            try:
                page = await browser.new_page(
                    viewport={"width": int(width), "height": GATE_HEIGHT},
                    reduced_motion="reduce",
                )
                try:
                    await page.goto(url, wait_until="load", timeout=timeout_ms)
                    await _settle(page)
                    return await _audit_page(page)
                finally:
                    await page.close()
            finally:
                await browser.close()
    except Exception as exc:
        log.warning("hierarchy_gate: url audit failed (abstain): %r", exc)
        return HierarchyReport((), 0, int(width), rendered=False)


async def audit_files(
    files: dict[str, str], *, width: int = GATE_WIDTH, timeout_ms: int = 15_000
) -> HierarchyReport:
    """Audit a static ``{path: html}`` page set at ``width`` (needs index.html)."""
    if "index.html" not in files:
        return HierarchyReport((), 0, int(width), rendered=False)
    try:
        from playwright.async_api import async_playwright

        with tempfile.TemporaryDirectory(prefix="omnia-hier-") as tmp:
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
                        await page.goto(
                            index_uri, wait_until="load", timeout=timeout_ms
                        )
                        await _settle(page)
                        return await _audit_page(page)
                    finally:
                        await page.close()
                finally:
                    await browser.close()
    except Exception as exc:
        log.warning("hierarchy_gate: files audit failed (abstain): %r", exc)
        return HierarchyReport((), 0, int(width), rendered=False)


def _main(argv: list[str]) -> int:  # pragma: no cover — thin CLI wrapper
    import asyncio

    if len(argv) < 2:
        print("usage: python -m omnia_api.services.hierarchy_gate <url|index.html-dir>")
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
    "HierarchyFinding",
    "HierarchyReport",
    "audit_files",
    "audit_url",
    "evaluate_observation",
]
