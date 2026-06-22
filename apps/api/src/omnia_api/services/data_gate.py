"""Non-empty-data gate — the seeded catalog must never render empty (V1.6 5/5).

The single worst first impression a generated app can make is an **empty
catalog**: one prompt, a polished dashboard, the primary list screen opens — and
it's a blank empty-state. That kills pillar 1 (WOW from the first generation) and
pillar 4 (nothing worth sharing). V1.8's ``demo_seeder`` fills every public entity
with 6–12 deterministic demo rows at provision time; **this gate is the assert
that proves the seeding actually reached the screen** (R-04: the seeder writes the
data, the gauntlet verifies it — one fact, two halves of one contract).

What it reads — the kit's own collection marker (model-independent)
==================================================================
The data-table kit primitive (``DataTable`` → every ``CrudResource`` catalog)
tags its container with two stable attributes the gate keys on:

  * ``data-omnia-collection`` — "I am a record collection" (a data surface, not a
    marketing row of feature cards).
  * ``data-omnia-rows="N"`` — the **raw** record count (filter/pagination
    independent), so an active search box hiding rows never reads as empty.

Keying on the kit-emitted marker — not on heuristic card-counting or
Russian empty-state text — makes the gate deterministic and false-positive-free:
a marketing landing with zero collections simply has no data surface and PASSES
(this gate is not its concern); a notification "no items" placeholder on a
dashboard is not a ``data-omnia-collection`` and is ignored. Only a real catalog
that rendered too few rows fails.

The two checks (the page FAILS if either fires on any visible collection)
========================================================================
  1. ``empty-collection`` — a collection rendered **0** rows (the empty-state is
     showing where seeded data was expected).
  2. ``thin-collection``  — a collection rendered 1 … ``MIN_ROWS``-1 rows: the
     seeder floor (``demo_seeder.MIN_ROWS`` = 6) was not met, so the catalog
     looks under-filled rather than alive.

Design — **JS extracts, Python scores** (R-01 deep module)
==========================================================
The injected ``_AUDIT_JS`` only reads the DOM (every visible
``[data-omnia-collection]`` and its row count) and returns raw numbers; every
threshold lives in pure Python (:func:`evaluate_observation`), unit-testable with
a hand-built dict — no browser, no flake. The async :func:`audit_files` /
:func:`audit_url` wrappers are the only browser-touching code and they fail soft
(R-10): a render error yields ``rendered=False`` (the gate ABSTAINS) rather than
raising into the gauntlet.

Row count is a DOM fact independent of viewport width, so unlike the composition
gates this one carries no special width — the gauntlet fans it at the same mobile
floor as the correctness gates.
"""

from __future__ import annotations

import json
import logging
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .render_settle import goto_and_settle

if TYPE_CHECKING:
    from playwright.async_api import Page

log = logging.getLogger(__name__)

# Raw DOM observation produced by ``_AUDIT_JS`` and scored by Python.
Obs = dict[str, Any]

# The correctness floor — runs at the same mobile width as the other floors.
GATE_WIDTH = 390
GATE_HEIGHT = 844

# The minimum rows a seeded catalog must render. MUST track
# ``demo_seeder.MIN_ROWS`` (the orchestrator-side generator that does the
# seeding) — they live in separate service packages that cannot import each
# other, so this is a documented cross-service contract: the seeder writes ≥ this
# many rows, this gate asserts ≥ this many landed. Change both together.
MIN_ROWS = 6

# Check ids — the vocabulary of the subscore / gauntlet classes.
EMPTY_COLLECTION = "empty-collection"
THIN_COLLECTION = "thin-collection"

CHECKS: tuple[str, ...] = (EMPTY_COLLECTION, THIN_COLLECTION)


# ── public result types ───────────────────────────────────────────────────────


@dataclass(frozen=True)
class DataFinding:
    """One under-filled collection the page rendered."""

    check: str
    detail: str


@dataclass(frozen=True)
class DataReport:
    """Verdict + JSON subscore of one live-DOM data audit.

    Shares the rendered-gate interface (``passed`` / ``rendered`` / ``classes`` /
    ``summary`` / ``subscore``) so the acceptance gauntlet folds it in through the
    same adapter as the WOW-DOM / perf / chip / taste / hierarchy gates.
    """

    findings: tuple[DataFinding, ...]
    collections: int
    rendered: bool
    detail: dict[str, Any] = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        """A gate that never rendered ABSTAINS (not pass) — it has no evidence.

        A render with no data collection at all PASSES: this gate only judges
        catalogs that exist, never penalising a marketing page for having none.
        """
        return self.rendered and not self.findings

    @property
    def classes(self) -> tuple[str, ...]:
        """The failed checks, in canonical order (what the gauntlet table shows)."""
        hit = {f.check for f in self.findings}
        return tuple(c for c in CHECKS if c in hit)

    def subscore(self) -> dict[str, Any]:
        return {
            "gate": "data",
            "rendered": self.rendered,
            "passed": self.passed,
            "collections": self.collections,
            "min_rows": MIN_ROWS,
            "classes": list(self.classes),
            "detail": self.detail,
        }

    def summary(self) -> str:
        if not self.rendered:
            return "data: ABSTAIN (render harness did not run)"
        if self.collections == 0:
            return "data: no record collection on the page (nothing to seed)"
        if self.passed:
            return f"data: {self.collections} collection(s), all ≥ {MIN_ROWS} rows"
        lines = [
            f"data: {len(self.findings)} under-filled collection(s) "
            f"(seeder floor {MIN_ROWS}):"
        ]
        for f in self.findings:
            lines.append(f"  [{f.check}] {f.detail}")
        return "\n".join(lines)


# ── the rubric (pure) ──────────────────────────────────────────────────────────


def _row_counts(obs: Obs) -> list[int]:
    """Raw row counts of every visible collection, in DOM order."""
    out: list[int] = []
    for c in obs.get("collections", ()):
        if not c.get("visible", True):
            continue
        try:
            out.append(int(c.get("rows")))
        except (TypeError, ValueError):
            # A collection with an unparseable count reads as empty — better to
            # flag it than to silently pass a broken catalog.
            out.append(0)
    return out


def evaluate_observation(obs: Obs, *, rendered: bool = True) -> DataReport:
    """Score a raw DOM observation dict → :class:`DataReport`.

    ``obs`` is exactly what ``_AUDIT_JS`` returns; passing a hand-built dict is how
    the gate is unit-tested.
    """
    if not rendered:
        return DataReport((), 0, rendered=False)

    counts = _row_counts(obs)
    findings: list[DataFinding] = []
    for n in counts:
        if n <= 0:
            findings.append(
                DataFinding(
                    EMPTY_COLLECTION,
                    "a catalog rendered 0 rows — empty-state where seeded "
                    "data was expected",
                )
            )
        elif n < MIN_ROWS:
            findings.append(
                DataFinding(
                    THIN_COLLECTION,
                    f"a catalog rendered only {n} row(s) (< {MIN_ROWS} floor) "
                    "— under-seeded",
                )
            )
    detail = {
        "row_counts": counts,
        "min_row_count": min(counts) if counts else None,
    }
    return DataReport(tuple(findings), len(counts), rendered=True, detail=detail)


# ── the DOM extractor (data only — all scoring is in Python above) ────────────

# Returns every visible record collection's raw row count. It reads; it never
# judges. A collection is any element the data-table kit tagged with
# ``data-omnia-collection``; ``data-omnia-rows`` carries the filter-independent
# record count the component holds.
_AUDIT_JS = r"""
() => {
  const visible = (el) => {
    const cs = getComputedStyle(el);
    if (cs.display === 'none' || cs.visibility === 'hidden' || parseFloat(cs.opacity) === 0)
      return false;
    const r = el.getBoundingClientRect();
    return r.width > 1 && r.height > 1;
  };
  const collections = [];
  const els = document.querySelectorAll('[data-omnia-collection]');
  for (let i = 0; i < els.length && i < 200; i++) {
    const el = els[i];
    const raw = el.getAttribute('data-omnia-rows');
    collections.push({ rows: raw, visible: visible(el) });
  }
  return { collections };
}
"""


# ── async render harnesses (the only browser-touching code; fail soft) ────────


async def _audit_page(page: Page) -> DataReport:
    obs = await page.evaluate(_AUDIT_JS)
    return evaluate_observation(obs)


async def audit_url(
    url: str,
    *,
    width: int = GATE_WIDTH,
    timeout_ms: int = 15_000,
    storage_state: dict | None = None,
) -> DataReport:
    """Audit a LIVE url (a running container app / prod ``/p/<slug>``) at ``width``.

    ``storage_state`` (optional) is a Playwright storage-state dict (cookies +
    localStorage) used to render an **authenticated** cabinet — the rendering
    context then carries the session cookie. When ``None`` (the default) the
    context is anonymous and byte-identical to the previous ``new_page`` path, so
    the unauthenticated audit is unchanged.

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
                    await goto_and_settle(page, url, timeout_ms=timeout_ms)
                    return await _audit_page(page)
                finally:
                    await context.close()
            finally:
                await browser.close()
    except Exception as exc:
        log.warning("data_gate: url audit failed (abstain): %r", exc)
        return DataReport((), 0, rendered=False)


async def audit_files(
    files: dict[str, str], *, width: int = GATE_WIDTH, timeout_ms: int = 15_000
) -> DataReport:
    """Audit a static ``{path: html}`` page set at ``width`` (needs index.html)."""
    if "index.html" not in files:
        return DataReport((), 0, rendered=False)
    try:
        from playwright.async_api import async_playwright

        with tempfile.TemporaryDirectory(prefix="omnia-data-") as tmp:
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
        log.warning("data_gate: files audit failed (abstain): %r", exc)
        return DataReport((), 0, rendered=False)


def _main(argv: list[str]) -> int:  # pragma: no cover — thin CLI wrapper
    import asyncio

    if len(argv) < 2:
        print("usage: python -m omnia_api.services.data_gate <url|index.html-dir>")
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
    "MIN_ROWS",
    "DataFinding",
    "DataReport",
    "audit_files",
    "audit_url",
    "evaluate_observation",
]
