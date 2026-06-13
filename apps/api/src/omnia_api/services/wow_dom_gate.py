"""Machine WOW-DOM gate — the objective half of the WOW rubric (V1.6 slice 2/5).

A Playwright gate that measures a *live render* (not an HTML string) and emits a
deterministic JSON subscore. Where ``ui_audit`` reads source HTML and
``defect_registry`` scans authored files, this module asks the browser what the
page *actually computes to* at 390px, the one thing neither static pass can see:

  1. ``h_scroll``      — no horizontal scroll at 390px (``scrollWidth`` ≤ viewport).
  2. ``dead_control``  — no anchor pointing at a dead href (``#`` / empty /
                         ``javascript:`` — an affordance that goes nowhere).
  3. ``low_contrast``  — every visible text node clears WCAG AA against its
                         effective background (≥4.5, or ≥3.0 for large text).
  4. ``small_target``  — every non-inline interactive control is ≥44px (the
                         touch-target floor; inline text links are WCAG-exempt).
  5. ``accent_family`` — exactly one saturated accent colour family across the
                         filled CTAs (one semantic accent, rubric point 2).

Design — **JS extracts, Python scores** (R-01 deep module)
==========================================================
The injected ``_AUDIT_JS`` does *only* DOM extraction: it reads computed colours,
rects, hrefs and font metrics and returns raw numbers. Every threshold, blend and
verdict lives in pure Python (:func:`evaluate_observation` and its helpers), so
the entire rubric is unit-testable with a dict — no browser, no flake. The async
:func:`audit_files` / :func:`audit_url` wrappers are the only browser-touching
code, and they fail soft (R-10): a render error yields ``rendered=False`` (the
gate abstains) rather than raising into the caller.

Scope notes
===========
* **Button click-handlers are out of scope here.** In a hydrated React build,
  listeners are delegated at the document root, so ``el.onclick`` is null even on
  a working button — DOM inspection cannot tell a live button from a dead one.
  Dead *anchors* (inert ``href``) are reliably observable and covered; dead
  button *handlers* / misrouted imports stay the static ``defect_registry``'s job
  (R-04 — one owner per fact).
* **Backgrounds behind an image/gradient are skipped** for contrast, not guessed
  — a false "low contrast" on a hero photo would be worse than a miss.
"""

from __future__ import annotations

import json
import logging
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from playwright.async_api import Page

log = logging.getLogger(__name__)

# Raw DOM observation produced by ``_AUDIT_JS`` and scored by Python. Heterogeneous
# by nature (numbers, nested lists, strings), so ``Any`` values — the shape is
# pinned by the JS extractor and exercised by the unit tests, not the type system.
Obs = dict[str, Any]

# The single 390px viewport the rubric pins (iPhone 12/13/14 logical width). The
# gate is a mobile-first floor; desktop overflow is a softer concern handled by
# the acceptance responsive layer.
GATE_WIDTH = 390
GATE_HEIGHT = 844

# scrollWidth can exceed the viewport by ~1px from sub-pixel rounding on a
# perfectly-fitting page (same tolerance as workers/preview.capture).
_OVERFLOW_TOLERANCE_PX = 2
# Touch-target floor with a half-pixel slack so a 44px control rounded to 43.6
# by layout isn't flagged.
_TOUCH_FLOOR_PX = 43.5
# WCAG AA contrast floors.
_CONTRAST_NORMAL = 4.5
_CONTRAST_LARGE = 3.0
# "Large text" per WCAG: ≥24px, or ≥18.66px when bold (≥700).
_LARGE_SIZE_PX = 24.0
_LARGE_BOLD_SIZE_PX = 18.66
_BOLD_WEIGHT = 700
# Accent-family clustering: a fill counts as a semantic accent only when it is
# clearly saturated and mid-luminance (not a near-black/white/grey tint).
_ACCENT_MIN_SATURATION = 0.35
_ACCENT_MIN_LIGHTNESS = 0.12
_ACCENT_MAX_LIGHTNESS = 0.92
_ACCENT_MIN_ALPHA = 0.5
# Two accent fills are the SAME family when their hues sit within this circular
# distance — wide enough that two shades of one brand colour (e.g. indigo 239°
# and 243°, which straddle a fixed-bucket boundary) read as one accent, narrow
# enough that a second brand hue (emerald ~160°) reads as a competing accent.
_HUE_MERGE_DEG = 45
# Cap how many findings of one kind we surface — the gate is pass/fail, the list
# is for the human; an unbounded dump helps no one.
_MAX_PER_CHECK = 12

# Check ids — the vocabulary of the subscore.
H_SCROLL = "h-scroll"
DEAD_CONTROL = "dead-control"
LOW_CONTRAST = "low-contrast"
SMALL_TARGET = "small-target"
ACCENT_FAMILY = "accent-family"

CHECKS: tuple[str, ...] = (H_SCROLL, DEAD_CONTROL, LOW_CONTRAST, SMALL_TARGET, ACCENT_FAMILY)


# ── public result types ──────────────────────────────────────────────────────


@dataclass(frozen=True)
class WowDomFinding:
    """One rubric violation found in the live DOM."""

    check: str
    detail: str


@dataclass(frozen=True)
class WowDomReport:
    """Verdict + JSON subscore of one live-DOM audit."""

    findings: tuple[WowDomFinding, ...]
    viewport_width: int
    scroll_width: int
    accent_colors: tuple[str, ...]
    rendered: bool
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
            "gate": "wow-dom",
            "rendered": self.rendered,
            "passed": self.passed,
            "viewport_width": self.viewport_width,
            "scroll_width": self.scroll_width,
            "h_scroll": H_SCROLL in self.classes,
            "counts": {c: self.counts.get(c, 0) for c in CHECKS},
            "accent_colors": list(self.accent_colors),
        }

    def summary(self) -> str:
        if not self.rendered:
            return "wow-dom: ABSTAIN (render harness did not run)"
        if self.passed:
            return f"wow-dom: clean @ {self.viewport_width}px (0 findings)"
        lines = [
            f"wow-dom: {len(self.findings)} finding(s) @ {self.viewport_width}px "
            f"across {len(self.classes)} check(s):"
        ]
        for f in self.findings:
            lines.append(f"  [{f.check}] {f.detail}")
        return "\n".join(lines)


# ── colour / typography maths (pure, the testable core) ───────────────────────

Rgb = tuple[float, float, float]
Rgba = tuple[float, float, float, float]


def _srgb_to_linear(c: float) -> float:
    c = c / 255.0
    return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4


def relative_luminance(rgb: Rgb) -> float:
    """WCAG relative luminance of an opaque sRGB colour (0..1)."""
    r, g, b = (_srgb_to_linear(x) for x in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast_ratio(a: Rgb, b: Rgb) -> float:
    """WCAG contrast ratio between two opaque colours (1..21)."""
    la, lb = relative_luminance(a), relative_luminance(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def composite(fg: Rgba, bg: Rgb) -> Rgb:
    """Alpha-composite a possibly-translucent foreground over an opaque bg."""
    r, g, b, a = fg
    a = max(0.0, min(1.0, a))
    return (
        r * a + bg[0] * (1 - a),
        g * a + bg[1] * (1 - a),
        b * a + bg[2] * (1 - a),
    )


def _flatten_bg(bg: Rgba) -> Rgb:
    """An effective background may itself be translucent (rgba over the page).
    Composite it over white — the page's default canvas — so contrast has two
    opaque colours to compare. Conservative: white maximises the odds of *not*
    false-flagging dark text."""
    r, g, b, a = bg
    if a >= 0.999:
        return (r, g, b)
    return composite((r, g, b, a), (255.0, 255.0, 255.0))


def rgb_to_hsl(rgb: Rgb) -> tuple[float, float, float]:
    """(hue 0..360, saturation 0..1, lightness 0..1)."""
    r, g, b = (x / 255.0 for x in rgb)
    mx, mn = max(r, g, b), min(r, g, b)
    light = (mx + mn) / 2
    if mx == mn:
        return (0.0, 0.0, light)
    delta = mx - mn
    sat = delta / (2 - mx - mn) if light > 0.5 else delta / (mx + mn)
    if mx == r:
        hue = ((g - b) / delta) % 6
    elif mx == g:
        hue = (b - r) / delta + 2
    else:
        hue = (r - g) / delta + 4
    return (hue * 60, sat, light)


def is_large_text(size_px: float, weight: float) -> bool:
    """WCAG "large text": ≥24px, or ≥18.66px when bold."""
    if size_px >= _LARGE_SIZE_PX:
        return True
    return size_px >= _LARGE_BOLD_SIZE_PX and weight >= _BOLD_WEIGHT


_DEAD_HREFS = {"", "#", "#!", "#none"}


def is_dead_href(href: str | None) -> bool:
    """An anchor that goes nowhere: missing/empty href, a bare fragment, or a
    ``javascript:`` no-op. A real route (``/``, ``/signin``, ``https://…``,
    ``#section`` anchor) is NOT dead — ``/`` is the home route on a live build,
    not a dead self-link (that nuance is the registry's auth-link detector)."""
    if href is None:
        return True
    h = href.strip().lower()
    if h in _DEAD_HREFS:
        return True
    return h.startswith("javascript:")


# ── observation scoring (pure) ────────────────────────────────────────────────


def _score_h_scroll(obs: Obs) -> list[WowDomFinding]:
    vw = int(obs.get("viewportWidth") or GATE_WIDTH)
    sw = int(obs.get("scrollWidth") or vw)
    if sw > vw + _OVERFLOW_TOLERANCE_PX:
        return [
            WowDomFinding(
                H_SCROLL,
                f"horizontal scroll at {vw}px: content is {sw}px wide ({sw - vw}px spill)",
            )
        ]
    return []


def _score_dead_controls(obs: Obs) -> list[WowDomFinding]:
    out: list[WowDomFinding] = []
    for c in obs.get("controls", ()):
        if c.get("tag") != "a" or not c.get("visible"):
            continue
        href = c.get("href")
        if is_dead_href(href):
            text = (c.get("text") or "").strip()[:40] or "(no text)"
            shown = "(none)" if href is None else (href or "(empty)")
            out.append(WowDomFinding(DEAD_CONTROL, f"anchor «{text}» → dead href {shown}"))
    return out[:_MAX_PER_CHECK]


def _score_low_contrast(obs: Obs) -> list[WowDomFinding]:
    out: list[WowDomFinding] = []
    seen: set[tuple[Any, ...]] = set()
    for t in obs.get("texts", ()):
        bg = t.get("bg")
        if bg is None:  # background is an image/gradient/unresolved → can't judge.
            continue
        fg_rgba = tuple(t["color"])
        bg_rgb = _flatten_bg(tuple(bg))
        fg_rgb = composite(fg_rgba, bg_rgb)
        ratio = contrast_ratio(fg_rgb, bg_rgb)
        large = is_large_text(t.get("size", 0), t.get("weight", 400))
        floor = _CONTRAST_LARGE if large else _CONTRAST_NORMAL
        if ratio + 0.05 < floor:
            sample = (t.get("sample") or "").strip()[:32]
            key = (sample, round(ratio, 1))
            if key in seen:
                continue
            seen.add(key)
            out.append(
                WowDomFinding(
                    LOW_CONTRAST,
                    f"text «{sample}» contrast {ratio:.1f}:1 < {floor:.1f}:1 (near-invisible)",
                )
            )
    return out[:_MAX_PER_CHECK]


def _score_small_targets(obs: Obs) -> list[WowDomFinding]:
    out: list[WowDomFinding] = []
    seen: set[tuple[Any, ...]] = set()
    for c in obs.get("controls", ()):
        if not c.get("visible") or c.get("disabled"):
            continue
        # Inline text links inside running copy are WCAG 2.5.5 exempt — they
        # inherit the line-box, not a tap target.
        if c.get("tag") == "a" and c.get("displayInline"):
            continue
        w = float(c.get("rectW") or 0)
        h = float(c.get("rectH") or 0)
        if w <= 0 or h <= 0:
            continue
        if w < _TOUCH_FLOOR_PX or h < _TOUCH_FLOOR_PX:
            text = (c.get("text") or "").strip()[:28] or c.get("tag", "?")
            key = (text, round(w), round(h))
            if key in seen:
                continue
            seen.add(key)
            floor = int(_TOUCH_FLOOR_PX + 0.5)
            out.append(
                WowDomFinding(
                    SMALL_TARGET,
                    f"control «{text}» is {w:.0f}×{h:.0f}px (< {floor}px touch target)",
                )
            )
    return out[:_MAX_PER_CHECK]


def _hue_distance(a: float, b: float) -> float:
    """Circular distance between two hues on the 0..360 wheel."""
    d = abs(a - b) % 360
    return min(d, 360 - d)


def _accent_families(obs: Obs) -> list[str]:
    """Cluster the saturated CTA fills into accent families by hue proximity.

    Greedy single-pass clustering on the hue wheel (a fill joins the first family
    whose representative hue is within ``_HUE_MERGE_DEG``, else opens a new one),
    so two shades of one brand colour count once while a genuinely different hue
    opens a second family. Returns one representative hex per family.
    """
    candidates: list[tuple[float, str]] = []
    for f in obs.get("fills", ()):
        bg = f.get("bg")
        if not bg:
            continue
        r, g, b, a = bg
        if a < _ACCENT_MIN_ALPHA:
            continue
        hue, sat, light = rgb_to_hsl((r, g, b))
        if sat < _ACCENT_MIN_SATURATION:
            continue
        if not (_ACCENT_MIN_LIGHTNESS < light < _ACCENT_MAX_LIGHTNESS):
            continue
        candidates.append((hue, _hex((r, g, b))))

    # Sort by hue so clustering is deterministic and order-independent.
    candidates.sort(key=lambda c: c[0])
    families: list[tuple[float, str]] = []  # (representative hue, hex)
    for hue, hexc in candidates:
        if any(_hue_distance(hue, fh) <= _HUE_MERGE_DEG for fh, _ in families):
            continue
        families.append((hue, hexc))
    return [hexc for _, hexc in families]


def _score_accent(obs: Obs) -> tuple[list[WowDomFinding], list[str]]:
    reps = _accent_families(obs)
    if len(reps) > 1:
        return [
            WowDomFinding(
                ACCENT_FAMILY,
                f"{len(reps)} competing accent colours ({', '.join(reps)}) — keep exactly one",
            )
        ], reps
    return [], reps


def _hex(rgb: Rgb) -> str:
    return "#" + "".join(f"{round(max(0.0, min(255.0, c))):02x}" for c in rgb)


def evaluate_observation(obs: Obs, *, rendered: bool = True) -> WowDomReport:
    """Score a raw DOM observation dict → :class:`WowDomReport`.

    This is the whole rubric, browser-free. ``obs`` is exactly what ``_AUDIT_JS``
    returns; passing a hand-built dict is how the gate is unit-tested.
    """
    if not rendered:
        return WowDomReport(
            findings=(),
            viewport_width=int(obs.get("viewportWidth") or GATE_WIDTH),
            scroll_width=int(obs.get("scrollWidth") or 0),
            accent_colors=(),
            rendered=False,
        )

    findings: list[WowDomFinding] = []
    findings += _score_h_scroll(obs)
    findings += _score_dead_controls(obs)
    findings += _score_low_contrast(obs)
    findings += _score_small_targets(obs)
    accent_findings, accent_colors = _score_accent(obs)
    findings += accent_findings

    counts = {c: 0 for c in CHECKS}
    for f in findings:
        counts[f.check] += 1

    return WowDomReport(
        findings=tuple(findings),
        viewport_width=int(obs.get("viewportWidth") or GATE_WIDTH),
        scroll_width=int(obs.get("scrollWidth") or 0),
        accent_colors=tuple(accent_colors),
        rendered=True,
        counts=counts,
    )


# ── the DOM extractor (data only — all scoring is in Python above) ────────────

# Returns the raw observation object scored by evaluate_observation(). It reads;
# it never judges. parseColor → [r,g,b,a]; the effective background walks
# ancestors until a non-transparent solid colour, bailing to null on any
# background-image (gradient/photo) so Python skips that text rather than guess.
_AUDIT_JS = r"""
() => {
  const px = (v) => parseFloat(v) || 0;
  const parseColor = (s) => {
    if (!s) return null;
    const m = s.match(/rgba?\(([^)]+)\)/);
    if (!m) return null;
    const p = m[1].split(',').map((x) => parseFloat(x));
    return [p[0] || 0, p[1] || 0, p[2] || 0, p.length > 3 ? p[3] : 1];
  };
  const visible = (el) => {
    const cs = getComputedStyle(el);
    if (cs.display === 'none' || cs.visibility === 'hidden' || px(cs.opacity) === 0) return false;
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0;
  };
  const effBg = (el) => {
    let n = el;
    while (n && n.nodeType === 1) {
      const cs = getComputedStyle(n);
      if (cs.backgroundImage && cs.backgroundImage !== 'none') return null; // photo/gradient → skip
      const c = parseColor(cs.backgroundColor);
      if (c && c[3] > 0) return c;
      n = n.parentElement;
    }
    return [255, 255, 255, 1]; // default page canvas
  };

  // text nodes
  const texts = [];
  const tw = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  let tn;
  while ((tn = tw.nextNode())) {
    const s = tn.nodeValue.replace(/\s+/g, ' ').trim();
    if (!s) continue;
    const el = tn.parentElement;
    if (!el || !visible(el)) continue;
    const cs = getComputedStyle(el);
    const color = parseColor(cs.color);
    if (!color) continue;
    const bg = effBg(el);
    texts.push({
      color, bg,
      size: px(cs.fontSize),
      weight: parseInt(cs.fontWeight) || 400,
      sample: s.slice(0, 40),
    });
    if (texts.length >= 400) break;
  }

  // interactive controls
  const controls = [];
  const sel = 'a, button, input[type=button], input[type=submit], input[type=reset], [role=button]';
  document.querySelectorAll(sel).forEach((el) => {
    const cs = getComputedStyle(el);
    const r = el.getBoundingClientRect();
    const tag = el.tagName.toLowerCase();
    controls.push({
      tag,
      href: el.getAttribute ? el.getAttribute('href') : null,
      rectW: r.width, rectH: r.height,
      displayInline: cs.display === 'inline',
      disabled: !!el.disabled || el.getAttribute('aria-disabled') === 'true',
      type: el.getAttribute ? (el.getAttribute('type') || '') : '',
      inForm: !!el.closest('form'),
      role: el.getAttribute ? (el.getAttribute('role') || '') : '',
      visible: visible(el),
      text: (el.innerText || el.value || '').replace(/\s+/g, ' ').trim().slice(0, 40),
    });
  });

  // accent-fill candidates: filled CTAs
  const fills = [];
  document.querySelectorAll('button, [role=button], a').forEach((el) => {
    if (!visible(el)) return;
    const cs = getComputedStyle(el);
    if (cs.backgroundImage && cs.backgroundImage !== 'none') return;
    const c = parseColor(cs.backgroundColor);
    if (!c || c[3] < 0.5) return;
    const r = el.getBoundingClientRect();
    fills.push({ bg: c, tag: el.tagName.toLowerCase(), area: r.width * r.height });
  });

  return {
    viewportWidth: document.documentElement.clientWidth,
    scrollWidth: document.documentElement.scrollWidth,
    texts, controls, fills,
  };
}
"""


# ── async render harnesses (the only browser-touching code; fail soft) ────────


async def _audit_page(page: Page) -> WowDomReport:
    obs = await page.evaluate(_AUDIT_JS)
    return evaluate_observation(obs)


async def audit_url(url: str, *, width: int = GATE_WIDTH, timeout_ms: int = 15_000) -> WowDomReport:
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
        log.warning("wow_dom_gate: url audit failed (abstain): %r", exc)
        return WowDomReport((), int(width), 0, (), rendered=False)


async def audit_files(
    files: dict[str, str], *, width: int = GATE_WIDTH, timeout_ms: int = 15_000
) -> WowDomReport:
    """Audit a static ``{path: html}`` page set at ``width`` (needs index.html)."""
    if "index.html" not in files:
        return WowDomReport((), int(width), 0, (), rendered=False)
    try:
        from playwright.async_api import async_playwright

        with tempfile.TemporaryDirectory(prefix="omnia-wowdom-") as tmp:
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
        log.warning("wow_dom_gate: files audit failed (abstain): %r", exc)
        return WowDomReport((), int(width), 0, (), rendered=False)


async def _settle(page: Page) -> None:
    """Best-effort: let a client-rendered app actually paint before we read it.

    The generated apps' public ``/p/<slug>`` page is a Next.js client render — its
    accent CTAs, sections and colour land *after* ``load``, so reading at
    ``domcontentloaded`` sees an empty shell and reports a hollow PASS that silently
    un-gates beauty on the hot path (the recurring ``domcontentloaded`` class —
    V1.6 12/5). Wait for the network to quiesce, fonts to load, then a
    Tailwind-JIT/paint beat. Mirrors ``hierarchy_gate`` exactly. Every step is
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


def _main(argv: list[str]) -> int:  # pragma: no cover — thin CLI wrapper
    import asyncio

    if len(argv) < 2:
        print("usage: python -m omnia_api.services.wow_dom_gate <url|index.html-dir>")
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
    "WowDomFinding",
    "WowDomReport",
    "audit_files",
    "audit_url",
    "contrast_ratio",
    "evaluate_observation",
    "is_dead_href",
    "is_large_text",
    "relative_luminance",
    "rgb_to_hsl",
]
