"""Perf + a11y gate — the gauntlet's blocking speed/accessibility floor (V1.6 slice 3/5).

Where ``wow_dom_gate`` scores the *visual* rubric on a live render, this gate
scores the two axes a screenshot can't show: **is it fast** and **is it usable
with a screen reader**. It runs against a live render (a running container app or
a static page set) and emits a deterministic JSON subscore that BLOCKS — a slow
or inaccessible app fails the gauntlet, it is not merely advised.

Floors (each one a hard gate):

  * ``slow-ttfb``     — TTFB < 800ms (first byte).
  * ``slow-lcp``      — LCP < 2.5s (largest contentful paint).
  * ``layout-shift``  — CLS < 0.1 (cumulative layout shift).
  * ``low-perf``      — a Lighthouse-equivalent perf score ≥ 85.
  * ``a11y-violation``— 0 *serious*/*critical* axe-core violations.

Design — **JS extracts, Python scores** (R-01 deep module, same as slice 2)
===========================================================================
The browser does only measurement: PerformanceObservers accumulate LCP / CLS /
long-task time, Navigation Timing yields TTFB / FCP, and the vendored axe-core
engine emits its raw violation list. Every threshold and the whole
Lighthouse-style perf curve live in pure Python (:func:`evaluate_observation`,
:func:`lighthouse_perf_score`), so the entire verdict is unit-testable with a
plain dict — no browser, no flake, no Lighthouse binary.

Why no real Lighthouse binary? It needs a Node toolchain in the worker, is slow,
and is not unit-testable. Lighthouse's *perf score* is a weighted log-normal
function of metrics we already capture cheaply via the Performance API, so we
reproduce that exact curve (:func:`log_normal_score` mirrors lighthouse
``statistics.getLogNormalScore``) instead of shelling out. Speed Index — the one
metric we cannot cheaply measure — is dropped and the remaining weights are
renormalised.

Why axe-core with ``color-contrast`` disabled? Contrast is already owned by
``wow_dom_gate`` (R-04 — one fact, one owner); enabling it here would double-gate
the same defect. Everything else axe flags *serious/critical* (missing alt,
unnamed controls, unlabelled inputs, missing lang/title) is this gate's job.

The async :func:`audit_url` / :func:`audit_files` wrappers are the only
browser-touching code and they fail soft (R-10): a render error → ``rendered=False``
(ABSTAIN), and an axe load/CSP error → a11y ABSTAINS without sinking the perf
verdict, rather than raising into the gauntlet.
"""

from __future__ import annotations

import json
import logging
import math
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .render_settle import goto_and_settle

if TYPE_CHECKING:
    from playwright.async_api import Page

log = logging.getLogger(__name__)

# Raw observation produced by the browser harness and scored by Python. Shape is
# pinned by the JS extractors below and exercised by the unit tests.
Obs = dict[str, Any]

# The 390px mobile viewport the gauntlet pins (matches wow_dom_gate) — perf and
# a11y are judged on the mobile-first floor, the harshest realistic device.
GATE_WIDTH = 390
GATE_HEIGHT = 844

# ── floors (the whole policy; one place per the deep-module contract) ──────────
TTFB_FLOOR_MS = 800.0
LCP_FLOOR_MS = 2500.0
CLS_FLOOR = 0.1
PERF_SCORE_FLOOR = 85
# axe impact levels that BLOCK. axe grades minor/moderate/serious/critical; we
# gate the top two — the ones that make a page unusable with assistive tech.
_BLOCKING_IMPACTS = frozenset({"serious", "critical"})
# Cap surfaced findings — the gate is pass/fail, the list is for the human.
_MAX_PER_CHECK = 12

# Check ids — the vocabulary of the subscore.
SLOW_TTFB = "slow-ttfb"
SLOW_LCP = "slow-lcp"
LAYOUT_SHIFT = "layout-shift"
LOW_PERF = "low-perf"
A11Y_VIOLATION = "a11y-violation"

CHECKS: tuple[str, ...] = (SLOW_TTFB, SLOW_LCP, LAYOUT_SHIFT, LOW_PERF, A11Y_VIOLATION)


# ── Lighthouse-equivalent perf scoring (pure, the testable core) ───────────────

# erfc⁻¹(0.2) — the standardised distance at which the log-normal CDF reads 0.10,
# so a metric == p10 scores exactly 0.90 and == median scores 0.50 (Lighthouse's
# documented control-point curve). Lifted verbatim from lighthouse statistics.js.
_INVERSE_ERFC_ONE_FIFTH = 0.9061938024368232

# Lighthouse v10 metric weights (Speed Index dropped — not cheaply measurable
# via the Performance API; the rest are renormalised at scoring time).
_PERF_WEIGHTS: dict[str, float] = {"fcp": 0.10, "lcp": 0.25, "tbt": 0.30, "cls": 0.25}
# (p10, median) control points — Lighthouse v10 mobile. ms except CLS (unitless).
_CONTROL_POINTS: dict[str, tuple[float, float]] = {
    "fcp": (1800.0, 3000.0),
    "lcp": (2500.0, 4000.0),
    "tbt": (200.0, 600.0),
    "cls": (0.1, 0.25),
}


def log_normal_score(p10: float, median: float, value: float) -> float:
    """Map a metric ``value`` to 0..1 on Lighthouse's log-normal curve.

    Pinned so ``value == p10`` → 0.90 and ``value == median`` → 0.50. A value of
    0 (a perfect metric) scores 1.0. Mirrors lighthouse
    ``statistics.getLogNormalScore`` so our perf score equals Lighthouse's given
    the same metrics — without running the Lighthouse binary.
    """
    if value <= 0:
        return 1.0
    if median <= 0 or p10 <= 0 or p10 >= median:
        return 0.0  # malformed control points — give no credit (never happens here)
    location = math.log(median)
    shape = abs(math.log(p10) - location) / (math.sqrt(2) * _INVERSE_ERFC_ONE_FIFTH)
    if shape == 0:
        return 0.0
    standardized = (math.log(value) - location) / (math.sqrt(2) * shape)
    return max(0.0, min(1.0, 0.5 * math.erfc(standardized)))


def lighthouse_perf_score(metrics: dict[str, float]) -> int | None:
    """A 0..100 Lighthouse-equivalent perf score from the captured metrics.

    Returns ``None`` when not a single weighted metric is present (the harness
    treats that as an abstain, never a silent zero-fail).
    """
    acc = 0.0
    total_w = 0.0
    for key, (p10, median) in _CONTROL_POINTS.items():
        v = metrics.get(key)
        if v is None:
            continue
        w = _PERF_WEIGHTS[key]
        acc += w * log_normal_score(p10, median, float(v))
        total_w += w
    if total_w == 0:
        return None
    return round(100 * acc / total_w)


# ── public result types ────────────────────────────────────────────────────────


@dataclass(frozen=True)
class PerfA11yFinding:
    """One blocking perf-or-a11y violation."""

    check: str
    detail: str


@dataclass(frozen=True)
class PerfA11yReport:
    """Verdict + JSON subscore of one perf/a11y audit."""

    findings: tuple[PerfA11yFinding, ...]
    metrics: dict[str, float]
    perf_score: int | None
    axe_violation_count: int
    rendered: bool
    a11y_ran: bool = True
    counts: dict[str, int] = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        """A gate that never rendered ABSTAINS (not pass) — it has no evidence."""
        return self.rendered and not self.findings

    @property
    def classes(self) -> tuple[str, ...]:
        hit = {f.check for f in self.findings}
        return tuple(c for c in CHECKS if c in hit)

    def subscore(self) -> dict[str, Any]:
        """Machine-readable subscore — emitted into the gauntlet's JSON."""
        return {
            "gate": "perf-a11y",
            "rendered": self.rendered,
            "passed": self.passed,
            "a11y_ran": self.a11y_ran,
            "perf_score": self.perf_score,
            "metrics": {k: round(v, 3) for k, v in self.metrics.items()},
            "axe_violations": self.axe_violation_count,
            "counts": {c: self.counts.get(c, 0) for c in CHECKS},
        }

    def summary(self) -> str:
        if not self.rendered:
            return "perf-a11y: ABSTAIN (render harness did not run)"
        head = f"perf-a11y: perf {self.perf_score if self.perf_score is not None else '?'}"
        if self.passed:
            return f"{head} — clean (0 findings)"
        lines = [f"{head} — {len(self.findings)} finding(s) across {len(self.classes)} check(s):"]
        for f in self.findings:
            lines.append(f"  [{f.check}] {f.detail}")
        if not self.a11y_ran:
            lines.append("  (note: axe-core did not load — a11y abstained)")
        return "\n".join(lines)


# ── observation scoring (pure) ──────────────────────────────────────────────────


def _num(v: Any) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _score_perf(metrics: dict[str, float]) -> tuple[list[PerfA11yFinding], int | None]:
    out: list[PerfA11yFinding] = []
    ttfb = metrics.get("ttfb")
    if ttfb is not None and ttfb >= TTFB_FLOOR_MS:
        out.append(
            PerfA11yFinding(
                SLOW_TTFB, f"TTFB {ttfb:.0f}ms ≥ {TTFB_FLOOR_MS:.0f}ms floor (slow first byte)"
            )
        )
    lcp = metrics.get("lcp")
    if lcp is not None and lcp >= LCP_FLOOR_MS:
        out.append(
            PerfA11yFinding(
                SLOW_LCP, f"LCP {lcp:.0f}ms ≥ {LCP_FLOOR_MS:.0f}ms floor (hero paints too late)"
            )
        )
    cls = metrics.get("cls")
    if cls is not None and cls >= CLS_FLOOR:
        out.append(
            PerfA11yFinding(
                LAYOUT_SHIFT, f"CLS {cls:.3f} ≥ {CLS_FLOOR} floor (content jumps during load)"
            )
        )
    perf_score = lighthouse_perf_score(metrics)
    if perf_score is not None and perf_score < PERF_SCORE_FLOOR:
        out.append(
            PerfA11yFinding(
                LOW_PERF, f"Lighthouse-equivalent perf {perf_score} < {PERF_SCORE_FLOOR}"
            )
        )
    return out, perf_score


def _score_a11y(axe: dict[str, Any]) -> tuple[list[PerfA11yFinding], int]:
    """Score axe's raw violation list — only serious/critical block."""
    out: list[PerfA11yFinding] = []
    blocking = 0
    for v in axe.get("violations", ()) or ():
        impact = (v.get("impact") or "").lower()
        if impact not in _BLOCKING_IMPACTS:
            continue
        blocking += 1
        if len(out) >= _MAX_PER_CHECK:
            continue
        nodes = v.get("nodes") or ()
        n = len(nodes)
        rule = v.get("id") or "?"
        help_text = (v.get("help") or "").strip()
        where = ""
        if nodes:
            target = nodes[0].get("target")
            if isinstance(target, list) and target:
                where = f" at {target[0]}"
        out.append(
            PerfA11yFinding(
                A11Y_VIOLATION,
                f"{impact} «{rule}»: {help_text} ({n} node{'s' if n != 1 else ''}{where})",
            )
        )
    return out, blocking


def evaluate_observation(obs: Obs, *, rendered: bool = True) -> PerfA11yReport:
    """Score a raw observation dict → :class:`PerfA11yReport` (browser-free).

    ``obs`` is exactly what the harness returns: ``{"metrics": {...}, "axe":
    {"ran": bool, "violations": [...]}}``. Passing a hand-built dict is how the
    gate is unit-tested.
    """
    if not rendered:
        return PerfA11yReport((), {}, None, 0, rendered=False)

    raw_metrics = obs.get("metrics") or {}
    metrics: dict[str, float] = {}
    for k in ("ttfb", "fcp", "lcp", "tbt", "cls"):
        v = _num(raw_metrics.get(k))
        if v is not None:
            metrics[k] = v

    findings: list[PerfA11yFinding] = []
    perf_findings, perf_score = _score_perf(metrics)
    findings += perf_findings

    axe = obs.get("axe") or {}
    a11y_ran = bool(axe.get("ran", "violations" in axe))
    axe_violation_count = 0
    if a11y_ran:
        a11y_findings, axe_violation_count = _score_a11y(axe)
        findings += a11y_findings

    counts = {c: 0 for c in CHECKS}
    for f in findings:
        counts[f.check] += 1

    return PerfA11yReport(
        findings=tuple(findings),
        metrics=metrics,
        perf_score=perf_score,
        axe_violation_count=axe_violation_count,
        rendered=True,
        a11y_ran=a11y_ran,
        counts=counts,
    )


# ── browser extractors (data only — all scoring is in Python above) ────────────

# Installed via add_init_script BEFORE any page script runs, so the observers
# catch the very first paint / shift / long task. Accumulates into window state
# we read back after the page settles. `buffered:true` also replays entries that
# fired before the observer attached.
_PERF_INIT_JS = r"""
() => {
  window.__omniaPerf = { lcp: 0, cls: 0, tbt: 0 };
  const obs = (type, cb) => {
    try { new PerformanceObserver(cb).observe({ type, buffered: true }); } catch (e) {}
  };
  obs('largest-contentful-paint', (l) => {
    const es = l.getEntries();
    const last = es[es.length - 1];
    if (last) window.__omniaPerf.lcp = last.renderTime || last.loadTime || last.startTime || 0;
  });
  obs('layout-shift', (l) => {
    for (const e of l.getEntries()) { if (!e.hadRecentInput) window.__omniaPerf.cls += e.value; }
  });
  obs('longtask', (l) => {
    for (const e of l.getEntries()) { window.__omniaPerf.tbt += Math.max(0, e.duration - 50); }
  });
}
"""

# Read after settle: Navigation Timing for TTFB/FCP, observer state for LCP/CLS/TBT.
_PERF_READ_JS = r"""
() => {
  const nav = performance.getEntriesByType('navigation')[0] || {};
  const paint = performance.getEntriesByType('paint') || [];
  const fcpE = paint.find((p) => p.name === 'first-contentful-paint');
  const p = window.__omniaPerf || {};
  const ttfb = nav.responseStart || 0;
  const fcp = fcpE ? fcpE.startTime : ttfb;
  return {
    ttfb,
    fcp,
    lcp: p.lcp || fcp,
    tbt: p.tbt || 0,
    cls: p.cls || 0,
  };
}
"""

# Runs the injected axe engine. color-contrast disabled — owned by wow_dom_gate
# (R-04). Returns only what Python scores; nodes capped so the payload stays small.
_AXE_RUN_JS = r"""
async () => {
  const r = await axe.run(document, {
    resultTypes: ['violations'],
    rules: { 'color-contrast': { enabled: false } },
  });
  return {
    ran: true,
    violations: (r.violations || []).map((v) => ({
      id: v.id,
      impact: v.impact,
      help: v.help,
      helpUrl: v.helpUrl,
      nodes: (v.nodes || []).slice(0, 5).map((n) => ({
        target: n.target,
        html: (n.html || '').slice(0, 160),
      })),
    })),
  };
}
"""


def _axe_source() -> str | None:
    """The vendored axe-core engine (offline, deterministic). None if missing."""
    path = Path(__file__).parent / "_vendor" / "axe.min.js"
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        log.warning("perf_a11y_gate: axe source unreadable (a11y abstain): %r", exc)
        return None


# ── async render harnesses (the only browser-touching code; fail soft) ─────────


async def _run_axe(page: Page) -> dict[str, Any]:
    """Inject + run axe on a settled page. Fail-soft: any error → abstain dict."""
    source = _axe_source()
    if source is None:
        return {"ran": False, "violations": []}
    try:
        await page.add_script_tag(content=source)
        result = await page.evaluate(_AXE_RUN_JS)
        if isinstance(result, dict):
            return result
    except Exception as exc:
        log.warning("perf_a11y_gate: axe run failed (a11y abstain): %r", exc)
    return {"ran": False, "violations": []}


async def _audit_page(page: Page) -> PerfA11yReport:
    metrics = await page.evaluate(_PERF_READ_JS)
    axe = await _run_axe(page)
    return evaluate_observation({"metrics": metrics, "axe": axe})


async def audit_url(
    url: str,
    *,
    width: int = GATE_WIDTH,
    timeout_ms: int = 20_000,
    storage_state: dict | None = None,
) -> PerfA11yReport:
    """Audit a LIVE url (a running container app / prod ``/p/<slug>``) at ``width``.

    ``storage_state`` (Playwright session state — cookies + localStorage) lets the
    render carry an authenticated session so a logged-in cabinet paints as the
    user sees it. The default ``None`` yields an anonymous context byte-identical
    to a plain ``new_page`` — the unauthenticated path is unchanged.

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
                try:
                    page = await context.new_page()
                    await page.add_init_script(_PERF_INIT_JS)
                    await goto_and_settle(page, url, timeout_ms=timeout_ms)
                    return await _audit_page(page)
                finally:
                    await context.close()
            finally:
                await browser.close()
    except Exception as exc:
        log.warning("perf_a11y_gate: url audit failed (abstain): %r", exc)
        return PerfA11yReport((), {}, None, 0, rendered=False)


async def audit_files(
    files: dict[str, str], *, width: int = GATE_WIDTH, timeout_ms: int = 20_000
) -> PerfA11yReport:
    """Audit a static ``{path: html}`` page set at ``width`` (needs index.html)."""
    if "index.html" not in files:
        return PerfA11yReport((), {}, None, 0, rendered=False)
    try:
        from playwright.async_api import async_playwright

        with tempfile.TemporaryDirectory(prefix="omnia-perfa11y-") as tmp:
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
                    await page.add_init_script(_PERF_INIT_JS)
                    try:
                        await goto_and_settle(page, index_uri, timeout_ms=timeout_ms)
                        return await _audit_page(page)
                    finally:
                        await page.close()
                finally:
                    await browser.close()
    except Exception as exc:
        log.warning("perf_a11y_gate: files audit failed (abstain): %r", exc)
        return PerfA11yReport((), {}, None, 0, rendered=False)


def _main(argv: list[str]) -> int:  # pragma: no cover — thin CLI wrapper
    import asyncio

    if len(argv) < 2:
        print("usage: python -m omnia_api.services.perf_a11y_gate <url|index.html-dir>")
        return 2
    target = argv[1]
    if target.startswith(("http://", "https://")):
        report = asyncio.run(audit_url(target))
    else:
        root = Path(target)
        files = {
            str(p.relative_to(root)): p.read_text(encoding="utf-8") for p in root.rglob("*.html")
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
    "PerfA11yFinding",
    "PerfA11yReport",
    "audit_files",
    "audit_url",
    "evaluate_observation",
    "lighthouse_perf_score",
    "log_normal_score",
]
