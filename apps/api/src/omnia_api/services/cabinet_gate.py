"""Authenticated-cabinet states gate — a fresh cabinet must onboard, never strand
(Area C, DARK, ADVISORY).

Once the composition gate can log in (``auth_session`` + the seeded operator), it
finally sees the real CABINET (``/dashboard`` + CRUD) instead of the public
storefront ``/``. The single worst first impression an authenticated cabinet can
make is a **dead screen**: a brand-new account opens ``/dashboard`` and gets a
blank surface with no empty-state and no onboarding checklist (nothing tells the
operator what to do next), or a **skeleton that never resolves** (a screen stuck
loading forever). Either quietly kills pillar 1 (WOW from the first generation):
the operator logged in and the product looks broken.

What it reads — the kit's own state markers (model-independent)
==============================================================
The three cabinet-state kit primitives carry stable ``data-omnia-*`` markers the
gate keys on (added to the template alongside this gate):

  * ``[data-omnia-empty]``     — an ``EmptyState`` rendered ("you have no records
    yet" — the legitimate brand-new-cabinet surface).
  * ``[data-omnia-checklist]`` — a ``SetupChecklist`` rendered (onboarding steps —
    the other legitimate brand-new-cabinet surface).
  * ``[data-omnia-skeleton]``  — a ``DashboardSkeleton`` still in the DOM **after
    settle** (a permanent skeleton = a screen that never resolved).

Keying on the kit-emitted markers — not on heuristic text scraping — makes the
gate deterministic and false-positive-free. A real, populated list legitimately
has no empty-state, so the gate WAIVES when a populated collection is present
(same ``data-omnia-collection`` / ``data-omnia-rows`` marker the data gate keys
on; floor reused from :data:`data_gate.MIN_ROWS`, R-04).

The two checks (the cabinet FAILS if either fires)
==================================================
  1. ``no-empty-state``  — the dashboard has data to show *neither* records (no
     populated collection) *nor* an empty-state *nor* an onboarding checklist: a
     brand-new cabinet that just shows a dead screen.
  2. ``stuck-skeleton``  — a ``[data-omnia-skeleton]`` is still mounted after the
     page settled: the screen never resolved its loading state.

Design — **JS extracts, Python scores** (R-01 deep module), mirroring
:mod:`first_paint_gate` / :mod:`data_gate`. The injected ``_AUDIT_JS`` only reads
the DOM (presence of the three markers + max collection row count) and returns raw
signals; every rule lives in pure Python (:func:`evaluate_observation`),
unit-testable with a hand-built dict — no browser, no flake. The async
:func:`audit_url` wrapper is the only browser-touching code and it fails soft
(R-10): a render error yields ``rendered=False`` (ABSTAIN) rather than raising.

ABSTAIN is also the verdict on every UNAUTHENTICATED render: without a session the
surface is the login wall, not the cabinet — the gate has no evidence and must not
judge it. The caller signals authentication by passing ``rendered`` accordingly
(an unauthenticated harness yields ``rendered=False``).

Single-source reuse (R-04): the populated-collection floor is
:data:`data_gate.MIN_ROWS` — not re-declared here. The cabinet is a desktop
surface, so unlike the mobile-floor correctness gates this one reads at
:data:`GATE_WIDTH` = 1440.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from . import data_gate
from .auth_session import preview_resolver_args
from .render_settle import goto_and_settle

if TYPE_CHECKING:
    from playwright.async_api import Page

log = logging.getLogger(__name__)

# Raw DOM observation produced by ``_AUDIT_JS`` and scored by Python.
Obs = dict[str, Any]

# The cabinet is an authenticated DESKTOP surface (the operator works at a desk,
# not on a phone), so it reads at desktop width — unlike the mobile correctness
# floors.
GATE_WIDTH = 1440
GATE_HEIGHT = 900

# Check ids — the vocabulary of the subscore / gauntlet classes.
NO_EMPTY_STATE = "no-empty-state"
STUCK_SKELETON = "stuck-skeleton"

CHECKS: tuple[str, ...] = (NO_EMPTY_STATE, STUCK_SKELETON)


# ── public result types ───────────────────────────────────────────────────────


@dataclass(frozen=True)
class CabinetFinding:
    """One way the authenticated cabinet failed its first-screen hygiene."""

    check: str
    detail: str


@dataclass(frozen=True)
class CabinetReport:
    """Verdict + JSON subscore of one authenticated-cabinet states audit.

    Shares the rendered-gate interface (``passed`` / ``rendered`` / ``classes`` /
    ``summary`` / ``subscore``) so the acceptance gauntlet folds it in through the
    same adapter as the first_paint / data / wow / perf gates.
    """

    findings: tuple[CabinetFinding, ...]
    rendered: bool
    detail: dict[str, Any] = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        """A gate that never rendered ABSTAINS (not pass) — it has no evidence.

        Unauthenticated renders set ``rendered=False`` so the login wall is never
        judged; a populated cabinet WAIVES (no findings) for the same reason a real
        list has no empty-state.
        """
        return self.rendered and not self.findings

    @property
    def classes(self) -> tuple[str, ...]:
        """The failed checks, in canonical order (what the gauntlet table shows)."""
        hit = {f.check for f in self.findings}
        return tuple(c for c in CHECKS if c in hit)

    def subscore(self) -> dict[str, Any]:
        return {
            "gate": "cabinet",
            "rendered": self.rendered,
            "passed": self.passed,
            "min_rows": data_gate.MIN_ROWS,
            "classes": list(self.classes),
            "detail": self.detail,
        }

    def summary(self) -> str:
        if not self.rendered:
            return "cabinet: ABSTAIN (unauthenticated — login wall, not the cabinet)"
        if self.passed:
            rows = self.detail.get("rows")
            if isinstance(rows, int) and rows >= data_gate.MIN_ROWS:
                return (
                    f"cabinet: populated list ({rows} rows ≥ {data_gate.MIN_ROWS}) "
                    "— empty-state legitimately absent (WAIVE)"
                )
            return "cabinet: empty-state / onboarding present, no stranded skeleton"
        lines = ["cabinet: authenticated cabinet showed a dead/stranded screen:"]
        for f in self.findings:
            lines.append(f"  [{f.check}] {f.detail}")
        return "\n".join(lines)


# ── the rubric (pure) ──────────────────────────────────────────────────────────


def evaluate_observation(obs: Obs, *, rendered: bool = True) -> CabinetReport:
    """Score a raw DOM observation dict → :class:`CabinetReport`.

    ``obs`` is exactly what ``_AUDIT_JS`` returns; passing a hand-built dict is how
    the gate is unit-tested. ``rendered=False`` (the unauthenticated harness, or a
    render error) ABSTAINS without judging — the gate never scores a login wall.
    """
    if not rendered:
        return CabinetReport((), rendered=False)

    try:
        rows = int(obs.get("rows", 0))
    except (TypeError, ValueError):
        rows = 0
    has_empty = bool(obs.get("has_empty"))
    has_checklist = bool(obs.get("has_checklist"))
    has_skeleton = bool(obs.get("has_skeleton"))

    detail = {
        "rows": rows,
        "has_empty": has_empty,
        "has_checklist": has_checklist,
        "has_skeleton": has_skeleton,
        "populated": rows >= data_gate.MIN_ROWS,
    }

    # WAIVE — a real list with data legitimately has no empty-state. A populated
    # collection is proof the cabinet is alive; the onboarding markers are not
    # required (and a stranded skeleton next to real rows is not a dead screen).
    if rows >= data_gate.MIN_ROWS:
        return CabinetReport((), rendered=True, detail=detail)

    findings: list[CabinetFinding] = []

    # (1) no-empty-state — a brand-new cabinet (no records) must onboard, not show
    # a dead screen: it needs an empty-state OR an onboarding checklist.
    if not has_empty and not has_checklist:
        findings.append(
            CabinetFinding(
                NO_EMPTY_STATE,
                f"the authenticated dashboard has no records (< {data_gate.MIN_ROWS} "
                "rows) and shows neither an empty-state ([data-omnia-empty]) nor an "
                "onboarding checklist ([data-omnia-checklist]) — a dead first screen",
            )
        )

    # (2) stuck-skeleton — a skeleton still mounted after settle never resolved.
    if has_skeleton:
        findings.append(
            CabinetFinding(
                STUCK_SKELETON,
                "a loading skeleton ([data-omnia-skeleton]) is still in the DOM "
                "after the page settled — a screen that never resolves",
            )
        )

    return CabinetReport(tuple(findings), rendered=True, detail=detail)


# ── the DOM extractor (data only — all scoring is in Python above) ────────────

# Reads the presence of the three cabinet-state markers + the max visible
# collection row count (kit marker, same as data_gate). It reads; it never judges.
_AUDIT_JS = r"""
() => {
  const visible = (el) => {
    if (!el) return false;
    const cs = getComputedStyle(el);
    if (cs.display === 'none' || cs.visibility === 'hidden' || parseFloat(cs.opacity) === 0)
      return false;
    const r = el.getBoundingClientRect();
    return r.width > 1 && r.height > 1;
  };

  // max seeded-collection row count (kit marker, same as data_gate)
  let rows = 0;
  document.querySelectorAll('[data-omnia-collection][data-omnia-rows]').forEach((el) => {
    if (!visible(el)) return;
    const n = parseInt(el.getAttribute('data-omnia-rows'), 10);
    if (!isNaN(n) && n > rows) rows = n;
  });

  return {
    has_empty: !!document.querySelector('[data-omnia-empty]'),
    has_checklist: !!document.querySelector('[data-omnia-checklist]'),
    has_skeleton: !!document.querySelector('[data-omnia-skeleton]'),
    rows,
  };
}
"""


# ── async render harness (the only browser-touching code; fail soft) ──────────


async def _audit_page(page: Page) -> CabinetReport:
    obs = await page.evaluate(_AUDIT_JS)
    return evaluate_observation(obs)


async def audit_url(
    url: str,
    *,
    width: int = GATE_WIDTH,
    storage_state: dict | None = None,
    timeout_ms: int = 15_000,
) -> CabinetReport:
    """Audit a LIVE authenticated cabinet (``/dashboard``) at desktop ``width``.

    ``storage_state`` carries the real Auth.js session cookie captured by
    :func:`auth_session.establish_session`, so middleware AND every page's
    ``requireUser()`` accept the request and the genuine cabinet renders. When it
    is ``None`` the context is anonymous and the surface is the login wall — the
    gate ABSTAINS rather than judging it.

    Fail-soft: any render/navigation error → an ABSTAIN report (``rendered=False``)
    rather than a raise, so a flaky container never hard-fails the gauntlet (R-10).
    """
    if storage_state is None:
        # No session → the cabinet is unreachable (login wall). Nothing to judge.
        return CabinetReport((), rendered=False)
    try:
        from playwright.async_api import async_playwright

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True, args=preview_resolver_args())
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
        log.warning("cabinet_gate: url audit failed (abstain): %r", exc)
        return CabinetReport((), rendered=False)


def _main(argv: list[str]) -> int:  # pragma: no cover — thin CLI wrapper
    import asyncio

    if len(argv) < 2:
        print("usage: python -m omnia_api.services.cabinet_gate <url>")
        return 2
    # NB: a CLI run carries no session, so it ABSTAINS — this wrapper is for
    # shape-debugging the report only.
    report = asyncio.run(audit_url(argv[1], storage_state={"cookies": []}))
    print(report.summary())
    print(json.dumps(report.subscore(), ensure_ascii=False, indent=2))
    return 0 if report.passed else 1


if __name__ == "__main__":  # pragma: no cover
    import sys

    raise SystemExit(_main(sys.argv))


__all__ = [
    "CHECKS",
    "GATE_WIDTH",
    "NO_EMPTY_STATE",
    "STUCK_SKELETON",
    "CabinetFinding",
    "CabinetReport",
    "audit_url",
    "evaluate_observation",
]
