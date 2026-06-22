"""Stranger-link first-paint gate — a share link must land fast & alive (V4.0b).

NORTH STAR pillar 4 (виральная шарабельность): "поделился → коллега открыл → за
**секунды** … пользуется продуктом". Every downstream viral task (fork, claim,
return-edge, the synthetic loop V4.7) silently assumes the *very first thing* —
the share link itself — lands a cold stranger on a live, content-rich surface in
seconds. Nothing gated that assumption. ``public.py`` can serve a branded
``_preview_shell`` placeholder, or a scale-from-zero dev container can hand the
first stranger a hibernating empty oblochka — either quietly kills k-factor
before the fork button is ever seen.

This is the cheapest pure-machine viral assert: zero dependency on fork/anon code,
it runs against the *existing* share/deploy surface. Three falsifiable checks on a
cold **incognito** (0-cookie) load of a known-good deployed ``/p/<slug>``:

  1. ``auth-wall``       — the link bounced the stranger to a sign-in / login
                           route (an auth redirect = the colleague hits a wall,
                           not the product).
  2. ``empty-shell``     — the surface rendered too little real content: a
                           stub/shell page with ``text_count`` ≤ ``SHELL_TEXT_FLOOR``,
                           or a page with neither a seeded catalog (≥
                           ``data_gate.MIN_ROWS`` rows) **nor** a visible hero+CTA.
  3. ``slow-first-paint`` — first-contentful-paint exceeded
                           ``render_settle.FIRST_PAINT_BUDGET_MS`` (the share-link
                           latency a stranger actually feels).

Design — **JS extracts, Python scores** (R-01 deep module), mirroring
:mod:`data_gate` / :mod:`wow_dom_gate`. The injected ``_AUDIT_JS`` only reads the
DOM + the browser's own ``first-contentful-paint`` performance entry and returns
raw numbers; every threshold lives in pure Python (:func:`evaluate_observation`),
unit-testable with a hand-built dict — no browser, no flake. The async
:func:`audit_url` / :func:`audit_files` wrappers are the only browser-touching
code and they fail soft (R-10): a render error yields ``rendered=False`` (ABSTAIN)
rather than raising.

Single-source reuse (R-04): the first-paint budget is
:data:`render_settle.FIRST_PAINT_BUDGET_MS` (the shared paint-timing module) and
the seeded-row floor is :data:`data_gate.MIN_ROWS` — neither is re-declared here.
This gate folds into the viral defect-registry (V4.6) and the synthetic viral-loop
(V4.7) as the first-paint segment; for now it stands alone as a falsifiable script.
"""

from __future__ import annotations

import json
import logging
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from . import data_gate
from .render_settle import FIRST_PAINT_BUDGET_MS, goto_and_settle

if TYPE_CHECKING:
    from playwright.async_api import Page

log = logging.getLogger(__name__)

# Raw DOM/timing observation produced by ``_AUDIT_JS`` and scored by Python.
Obs = dict[str, Any]

# The cold-stranger surface is judged at the same mobile floor as the other
# correctness gates (a phone is the worst-case share-open device).
GATE_WIDTH = 390
GATE_HEIGHT = 844

# A page whose entire visible body holds this few text fragments is a stub /
# placeholder shell, not the product (the branded "запускается" oblochka or a
# scale-from-zero holding page). Matches the V4.0b spec: text_count ≤ 3 ⇒ FAIL.
SHELL_TEXT_FLOOR = 3

# URL fragments that mean the share link bounced the stranger to an auth wall
# instead of the product. Checked case-insensitively against the *landed* url.
_AUTH_PATH_MARKERS: tuple[str, ...] = (
    "/signin",
    "/sign-in",
    "/login",
    "/log-in",
    "/auth/",
    "/register",
)

# Check ids — the vocabulary of the subscore / gauntlet classes (V4.6 registry).
AUTH_WALL = "auth-wall"
EMPTY_SHELL = "empty-shell"
SLOW_FIRST_PAINT = "slow-first-paint"

CHECKS: tuple[str, ...] = (AUTH_WALL, EMPTY_SHELL, SLOW_FIRST_PAINT)


# ── public result types ───────────────────────────────────────────────────────


@dataclass(frozen=True)
class FirstPaintFinding:
    """One way the share link failed a cold stranger."""

    check: str
    detail: str


@dataclass(frozen=True)
class FirstPaintReport:
    """Verdict + JSON subscore of one cold-stranger share-link audit.

    Shares the rendered-gate interface (``passed`` / ``rendered`` / ``classes`` /
    ``summary`` / ``subscore``) so the viral registry (V4.6) and the gauntlet fold
    it in through the same adapter as the WOW-DOM / data / perf gates.
    """

    findings: tuple[FirstPaintFinding, ...]
    rendered: bool
    detail: dict[str, Any] = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        """A gate that never rendered ABSTAINS (not pass) — it has no evidence."""
        return self.rendered and not self.findings

    @property
    def classes(self) -> tuple[str, ...]:
        """The failed checks, in canonical order (what the registry table shows)."""
        hit = {f.check for f in self.findings}
        return tuple(c for c in CHECKS if c in hit)

    def subscore(self) -> dict[str, Any]:
        return {
            "gate": "first_paint",
            "rendered": self.rendered,
            "passed": self.passed,
            "budget_ms": FIRST_PAINT_BUDGET_MS,
            "min_rows": data_gate.MIN_ROWS,
            "classes": list(self.classes),
            "detail": self.detail,
        }

    def summary(self) -> str:
        if not self.rendered:
            return "first_paint: ABSTAIN (render harness did not run)"
        if self.passed:
            fcp = self.detail.get("first_paint_ms")
            fcp_txt = f"{fcp:.0f}ms" if isinstance(fcp, (int, float)) else "n/a"
            return (
                f"first_paint: stranger landed alive — FCP {fcp_txt} "
                f"< {FIRST_PAINT_BUDGET_MS}ms, content present"
            )
        lines = ["first_paint: cold stranger hit a dead/slow/empty surface:"]
        for f in self.findings:
            lines.append(f"  [{f.check}] {f.detail}")
        return "\n".join(lines)


# ── the rubric (pure) ──────────────────────────────────────────────────────────


def _is_auth_url(url: str) -> bool:
    """True if the landed url is a sign-in / login / register route."""
    low = (url or "").lower()
    return any(marker in low for marker in _AUTH_PATH_MARKERS)


def evaluate_observation(obs: Obs, *, rendered: bool = True) -> FirstPaintReport:
    """Score a raw observation dict → :class:`FirstPaintReport`.

    ``obs`` is exactly what ``_AUDIT_JS`` returns (plus the authoritative landed
    ``url`` the Python harness stamps in); passing a hand-built dict is how the
    gate is unit-tested.
    """
    if not rendered:
        return FirstPaintReport((), rendered=False)

    url = str(obs.get("url", ""))
    try:
        text_count = int(obs.get("text_count", 0))
    except (TypeError, ValueError):
        text_count = 0
    try:
        rows = int(obs.get("rows", 0))
    except (TypeError, ValueError):
        rows = 0
    hero = bool(obs.get("hero_visible"))
    cta = bool(obs.get("cta_visible"))
    fcp_raw = obs.get("first_paint_ms")
    fcp = fcp_raw if isinstance(fcp_raw, (int, float)) else None

    findings: list[FirstPaintFinding] = []

    # (1) auth wall — the colleague never reaches the product.
    if _is_auth_url(url):
        findings.append(
            FirstPaintFinding(
                AUTH_WALL,
                f"share link bounced the stranger to an auth route ({url!r}) "
                "— a wall, not the product",
            )
        )

    # (2) empty shell — a stub page, or real content absent.
    content_ok = rows >= data_gate.MIN_ROWS or (hero and cta)
    if text_count <= SHELL_TEXT_FLOOR:
        findings.append(
            FirstPaintFinding(
                EMPTY_SHELL,
                f"only {text_count} visible text fragment(s) (≤ {SHELL_TEXT_FLOOR}) "
                "— a placeholder/holding shell, not the rendered product",
            )
        )
    elif not content_ok:
        findings.append(
            FirstPaintFinding(
                EMPTY_SHELL,
                f"no seeded catalog (≥ {data_gate.MIN_ROWS} rows; saw {rows}) and "
                f"no visible hero+CTA (hero={hero}, cta={cta}) — nothing worth "
                "sharing landed",
            )
        )

    # (3) slow first paint — the latency the stranger feels.
    if fcp is not None and fcp > FIRST_PAINT_BUDGET_MS:
        findings.append(
            FirstPaintFinding(
                SLOW_FIRST_PAINT,
                f"first-contentful-paint {fcp:.0f}ms > {FIRST_PAINT_BUDGET_MS}ms "
                "budget — the stranger waits, k-factor bleeds",
            )
        )

    detail = {
        "url": url,
        "first_paint_ms": fcp,
        "text_count": text_count,
        "rows": rows,
        "hero_visible": hero,
        "cta_visible": cta,
        "content_ok": content_ok,
    }
    return FirstPaintReport(tuple(findings), rendered=True, detail=detail)


# ── the DOM/timing extractor (data only — all scoring is in Python above) ─────

# Reads the browser's own first-contentful-paint entry + a coarse content signal
# (visible text fragments, max seeded-collection row count, hero + CTA presence).
# It reads; it never judges.
_AUDIT_JS = r"""
() => {
  const px = (v) => parseFloat(v) || 0;
  const visible = (el) => {
    if (!el) return false;
    const cs = getComputedStyle(el);
    if (cs.display === 'none' || cs.visibility === 'hidden' || px(cs.opacity) === 0)
      return false;
    const r = el.getBoundingClientRect();
    return r.width > 1 && r.height > 1;
  };

  // first-contentful-paint (ms since navigation start) — the browser's own metric.
  let fcp = null;
  try {
    const paints = performance.getEntriesByType('paint');
    const e = paints.find((p) => p.name === 'first-contentful-paint');
    if (e) fcp = e.startTime;
  } catch (_) { /* no paint timing → null, Python treats as not-measured */ }

  // visible text fragments
  let textCount = 0;
  const tw = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  let tn;
  while ((tn = tw.nextNode())) {
    const s = (tn.nodeValue || '').replace(/\s+/g, ' ').trim();
    if (!s) continue;
    if (!visible(tn.parentElement)) continue;
    textCount++;
    if (textCount >= 400) break;
  }

  // max seeded-collection row count (kit marker, same as data_gate)
  let rows = 0;
  document.querySelectorAll('[data-omnia-collection]').forEach((el) => {
    if (!visible(el)) return;
    const n = parseInt(el.getAttribute('data-omnia-rows'), 10);
    if (!isNaN(n) && n > rows) rows = n;
  });

  // hero — a visible large heading with real text
  let heroVisible = false;
  document.querySelectorAll('h1, h2, [role=heading]').forEach((el) => {
    if (heroVisible || !visible(el)) return;
    const size = px(getComputedStyle(el).fontSize);
    const txt = (el.innerText || '').replace(/\s+/g, ' ').trim();
    if (size >= 24 && txt.length >= 3) heroVisible = true;
  });

  // CTA — a visible, reasonably-sized interactive control
  let ctaVisible = false;
  document
    .querySelectorAll('a, button, [role=button], input[type=submit], input[type=button]')
    .forEach((el) => {
      if (ctaVisible || !visible(el)) return;
      const r = el.getBoundingClientRect();
      if (r.width >= 40 && r.height >= 20) ctaVisible = true;
    });

  return {
    url: location.href,
    first_paint_ms: fcp,
    text_count: textCount,
    rows,
    hero_visible: heroVisible,
    cta_visible: ctaVisible,
  };
}
"""


# ── async render harnesses (the only browser-touching code; fail soft) ────────


async def _audit_page(page: Page) -> FirstPaintReport:
    obs = await page.evaluate(_AUDIT_JS)
    # page.url is the authoritative landed url (it reflects every redirect hop),
    # so it overrides the JS-reported location for the auth-wall check.
    obs["url"] = page.url
    return evaluate_observation(obs)


async def audit_url(
    url: str,
    *,
    width: int = GATE_WIDTH,
    timeout_ms: int = 15_000,
    storage_state: dict | None = None,
) -> FirstPaintReport:
    """Audit a LIVE share link as a cold incognito stranger (0 cookies) at ``width``.

    A fresh ``browser.new_context()`` carries no cookies / storage, so this is an
    honest "colleague opens the link for the first time" load. Fail-soft: any
    render/navigation error → an ABSTAIN report (``rendered=False``) rather than a
    raise (R-10).

    ``storage_state`` is optional and defaults to ``None`` — the default path is the
    unchanged 0-cookie incognito load. When a Playwright storage-state dict is passed
    the context carries that session cookie, so the same checks run against an
    authenticated cabinet render instead of the anonymous stranger surface.
    """
    try:
        from playwright.async_api import async_playwright

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            try:
                # storage_state=None ⇒ clean incognito context: no cookies, no
                # stored session (the default). A passed storage_state carries the
                # session cookie for an authenticated-cabinet render.
                context = await browser.new_context(
                    viewport={"width": int(width), "height": GATE_HEIGHT},
                    reduced_motion="reduce",
                    storage_state=storage_state,
                )
                try:
                    page = await context.new_page()
                    await goto_and_settle(page, url, timeout_ms=timeout_ms)
                    return await _audit_page(page)
                finally:
                    await context.close()
            finally:
                await browser.close()
    except Exception as exc:
        log.warning("first_paint_gate: url audit failed (abstain): %r", exc)
        return FirstPaintReport((), rendered=False)


async def audit_files(
    files: dict[str, str], *, width: int = GATE_WIDTH, timeout_ms: int = 15_000
) -> FirstPaintReport:
    """Audit a static ``{path: html}`` page set at ``width`` (needs index.html).

    Used by the adversarial fixtures: a shell page (``text_count`` ≤ floor) MUST
    fail, a content-rich page MUST pass — proving the gate has teeth.
    """
    if "index.html" not in files:
        return FirstPaintReport((), rendered=False)
    try:
        from playwright.async_api import async_playwright

        with tempfile.TemporaryDirectory(prefix="omnia-firstpaint-") as tmp:
            workdir = Path(tmp)
            for path, content in files.items():
                full = workdir / path
                full.parent.mkdir(parents=True, exist_ok=True)
                full.write_text(content, encoding="utf-8")
            index_uri = (workdir / "index.html").as_uri()

            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                try:
                    context = await browser.new_context(
                        viewport={"width": int(width), "height": GATE_HEIGHT},
                        reduced_motion="reduce",
                    )
                    try:
                        page = await context.new_page()
                        await goto_and_settle(page, index_uri, timeout_ms=timeout_ms)
                        return await _audit_page(page)
                    finally:
                        await context.close()
                finally:
                    await browser.close()
    except Exception as exc:
        log.warning("first_paint_gate: files audit failed (abstain): %r", exc)
        return FirstPaintReport((), rendered=False)


def _main(argv: list[str]) -> int:  # pragma: no cover — thin CLI wrapper
    import asyncio

    if len(argv) < 2:
        print("usage: python -m omnia_api.services.first_paint_gate <url>")
        return 2
    report = asyncio.run(audit_url(argv[1]))
    print(report.summary())
    print(json.dumps(report.subscore(), ensure_ascii=False, indent=2))
    return 0 if report.passed else 1


if __name__ == "__main__":  # pragma: no cover
    import sys

    raise SystemExit(_main(sys.argv))


__all__ = [
    "CHECKS",
    "GATE_WIDTH",
    "SHELL_TEXT_FLOOR",
    "FirstPaintFinding",
    "FirstPaintReport",
    "audit_files",
    "audit_url",
    "evaluate_observation",
]
