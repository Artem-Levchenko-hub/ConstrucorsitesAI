"""Acceptance gauntlet — the one aggregator that fans every landed quality gate.

V1.6 keystone (slice 0/5). Before this module the four gates built in slices
1–4 — ``defect_registry`` (deterministic known-defect ratchet), ``wow_dom_gate``
(objective WOW rubric on the live DOM), ``perf_a11y_gate`` (speed + screen-reader
floors) and ``chip_pixel_gate`` (request↔output fidelity) — were *orphaned*: each
had a CLI and unit tests but **zero non-test importers**, so the ratchet they
promise was never actually wired into any ship decision. ``acceptance.evaluate``
still shipped on a subjective vision verdict.

This module is the missing wiring:

  * ``run(files | url)`` fans **all** landed gates, sequentially (one headless
    browser at a time — RESOURCE-GUARD), and returns one ``GauntletVerdict``
    with a per-gate table and a machine-readable subscore.
  * the CLI exits ``0`` iff every applicable gate passed, ``1`` if any gate has
    findings **or** abstains where a render was expected (no evidence ≠ pass).
  * ``acceptance.evaluate`` imports and calls it as the ship decision — the
    vision verdict is demoted to advisory.

Canon: R-01 (one deep gate hides the rich fan-out), R-04 (each defect's truth
lives in exactly one gate, not re-proven here), R-10 (fail fast / fail soft — a
flaky render abstains, it never raises through the gauntlet).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from omnia_api.services import (
    chip_pixel_gate,
    data_gate,
    defect_registry,
    hierarchy_gate,
    perf_a11y_gate,
    reference_corpus,
    taste_gate,
    wow_dom_gate,
)
from omnia_api.services.chip_pixel_gate import FidelitySpec

log = logging.getLogger(__name__)

#: Default audit width — the mobile viewport the correctness gates score at.
GATE_WIDTH = wow_dom_gate.GATE_WIDTH
#: Desktop width for the composition gates (taste + hierarchy) — richness (type
#: scale, focal dominance, multi-width rhythm, hero imagery) reads where the
#: layout breathes, not collapsed to one column.
TASTE_WIDTH = taste_gate.GATE_WIDTH
HIERARCHY_WIDTH = hierarchy_gate.GATE_WIDTH
#: Default viewport the composition legs render at (desktop). Callers that want
#: the MOBILE dimension — a page rich on desktop but collapsed to a monotone
#: column at 390px — pass ``composition_width=390`` (V1.6 15/5); niche_batch's
#: dual-width loop drives exactly that, so its @390 pass is a real second render
#: rather than a silent duplicate of the desktop score.
COMPOSITION_WIDTH = TASTE_WIDTH

# Gate identifiers, in the order they appear in the verdict table.
DEFECT_REGISTRY = "defect-registry"
WOW_DOM = "wow-dom"
PERF_A11Y = "perf-a11y"
CHIP_PIXEL = "chip-pixel"
TASTE = "taste"
HIERARCHY = "hierarchy"
DATA = "data"
REFERENCE = "reference"

#: The rendered gates, in run order. ``defect-registry`` is pure source-scan and
#: runs first/always; these need a live DOM (a static file set or a URL). The
#: correctness gates (wow-dom, perf-a11y, chip-pixel, data) run at the mobile
#: floor; ``taste``, ``hierarchy`` and ``reference`` run at desktop width.
#: ``reference`` (V1.13b) is the pillar-1 CEILING leg — the candidate must meet
#: or beat a curated enterprise corpus; it is a member of the order tuple (so it
#: is fanned by ``run()`` and not orphaned) but, like a flag-gated gate, only
#: fires when ``include_rendered`` is on OR the ``reference=`` dial is set.
RENDERED_GATES = (WOW_DOM, PERF_A11Y, CHIP_PIXEL, TASTE, HIERARCHY, DATA, REFERENCE)

#: The COMPOSITION legs (V1.6 14/5). Taste + hierarchy score richness — type
#: scale, focal dominance, layered depth, hero imagery — at DESKTOP width, where
#: the awwwards promise of pillar 1 actually reads. They have NO 44px-touch
#: false-positive (that pathology is the wow-dom touch leg, calibration 11/5), so
#: they are decoupled and run ALWAYS-ON as a hard ship-block via ``composition=``
#: — independent of ``include_rendered`` (which dials the touch/correctness legs).
COMPOSITION_LEGS = (TASTE, HIERARCHY)
#: The TOUCH leg — wow-dom's 44px tap-target check, the one with the 40px
#: false-positive on good shadcn buttons. Stays behind ``include_rendered`` until
#: calibration 11/5 tiers it (pass ≥44 / warn 40–44 / fail <40).
TOUCH_LEGS = (WOW_DOM,)
#: The FIDELITY leg (V2.5.2) — chip-pixel's request↔render check. Like the
#: composition legs it has NO 44px-touch false-positive, and it is INERT when the
#: ``spec`` is None/empty (asserts nothing → passes). So it is the ALWAYS-ON
#: causality hard ship-block via ``fidelity=`` — independent of ``include_rendered``
#: (which keeps the touch leg behind calibration 11/5). It only bites when the user
#: actually steered an axis in onboarding (a persisted ``discovery_spec``), turning
#: chip taps from cosmetic into a real request↔output contract on the ship path.
FIDELITY_LEGS = (CHIP_PIXEL,)
#: The REFERENCE leg (V1.13b) — the pillar-1 CEILING ratchet. It renders the
#: candidate and a curated enterprise corpus and hard-fails when the candidate
#: regresses below the corpus on ≥2 of the five richness axes (it reuses the
#: taste/hierarchy per-axis verdicts, R-04 — no new metric). Like composition and
#: fidelity it is decoupled from ``include_rendered`` via its own ``reference=``
#: dial, and it ABSTAINS (never hard-fails) when the corpus is empty or a page
#: fails to render (R-10), so wiring it is safe even before the owner corpus-run
#: that flips it on for the live hot path.
REFERENCE_LEGS = (REFERENCE,)


@dataclass(frozen=True)
class GateVerdict:
    """One gate's contribution to the gauntlet."""

    gate: str
    passed: bool
    #: A rendered gate that produced no evidence where a render was expected.
    #: ``abstained`` implies ``not passed`` — no evidence is never a pass.
    abstained: bool
    classes: tuple[str, ...]
    summary: str
    subscore: dict[str, Any]


@dataclass(frozen=True)
class GauntletVerdict:
    """Aggregate verdict of one gauntlet run."""

    gates: tuple[GateVerdict, ...]
    #: True iff the caller asked for the rendered gates (url or static index.html).
    render_expected: bool = False

    @property
    def passed(self) -> bool:
        """STRICT verdict (CLI / niche-E2E): every applicable gate passed.

        Empty (nothing ran) is not a pass — no evidence ≠ ship. A rendered gate
        that abstained reports ``passed=False`` already, so an abstain where we
        waited for a render fails this too (the CLI exit-1 contract).
        """
        return bool(self.gates) and all(g.passed for g in self.gates)

    @property
    def hard_failed(self) -> tuple[GateVerdict, ...]:
        """Gates with a REAL finding (not a mere abstain).

        The hot path (``acceptance.evaluate``) blocks ship on these only: a flaky
        local render that abstains must not sink an otherwise-good page, but a
        deterministic defect or a concrete rubric violation must.
        """
        return tuple(g for g in self.gates if not g.passed and not g.abstained)

    @property
    def abstained(self) -> tuple[GateVerdict, ...]:
        return tuple(g for g in self.gates if g.abstained)

    @property
    def failed_classes(self) -> tuple[str, ...]:
        """Gate-prefixed classes of every non-passing gate (`gate:class`)."""
        out: list[str] = []
        for g in self.gates:
            for c in g.classes:
                out.append(f"{g.gate}:{c}")
        return tuple(out)

    def table(self) -> str:
        """Human-readable per-gate verdict table."""
        if not self.gates:
            return "acceptance-gauntlet: no gate ran (nothing to judge)"
        rows = []
        for g in self.gates:
            mark = "PASS" if g.passed else ("ABSTAIN" if g.abstained else "FAIL")
            extra = f" [{', '.join(g.classes)}]" if g.classes else ""
            rows.append(f"  {mark:<8} {g.gate}{extra}")
        verdict = "PASS" if self.passed else "FAIL"
        return f"acceptance-gauntlet: {verdict}\n" + "\n".join(rows)

    def summary(self) -> str:
        """The table plus each gate's own one-line summary."""
        parts = [self.table()]
        for g in self.gates:
            parts.append(g.summary)
        return "\n".join(parts)

    def subscore(self) -> dict[str, Any]:
        return {
            "gate": "acceptance-gauntlet",
            "passed": self.passed,
            "render_expected": self.render_expected,
            "hard_failed": [g.gate for g in self.hard_failed],
            "abstained": [g.gate for g in self.abstained],
            "failed_classes": list(self.failed_classes),
            "gates": [g.subscore for g in self.gates],
        }


def _from_registry(rep: defect_registry.DefectReport) -> GateVerdict:
    return GateVerdict(
        gate=DEFECT_REGISTRY,
        passed=rep.passed,
        abstained=False,  # pure source-scan — always has its evidence
        classes=rep.classes,
        summary=rep.summary(),
        subscore={
            "gate": DEFECT_REGISTRY,
            "passed": rep.passed,
            "classes": list(rep.classes),
            "count": len(rep.defects),
        },
    )


def _from_rendered(gate: str, rep: Any) -> GateVerdict:
    """Adapt a rendered gate's report (wow/perf/chip — same shape) to a verdict.

    ``rep.passed`` already returns ``False`` when ``rep.rendered`` is False, so an
    abstain never reads as a pass.
    """
    return GateVerdict(
        gate=gate,
        passed=rep.passed,
        abstained=not rep.rendered,
        classes=rep.classes,
        summary=rep.summary(),
        subscore=rep.subscore(),
    )


async def _audit_one(
    gate: str,
    *,
    files: dict[str, str] | None,
    url: str | None,
    spec: FidelitySpec,
    width: int,
    composition_width: int,
) -> Any:
    """Render one rendered gate against the live target (url first, else files).

    Each gate's own width is honoured: the composition legs (taste/hierarchy) read
    at ``composition_width`` (desktop by default; ``390`` for the mobile dimension,
    V1.6 15/5), the correctness/touch legs at the mobile ``width`` floor.
    """
    if url:
        if gate == WOW_DOM:
            return await wow_dom_gate.audit_url(url, width=width)
        if gate == PERF_A11Y:
            return await perf_a11y_gate.audit_url(url, width=width)
        if gate == CHIP_PIXEL:
            return await chip_pixel_gate.audit_url(url, spec, width=width)
        if gate == TASTE:
            return await taste_gate.audit_url(url, width=composition_width)
        if gate == HIERARCHY:
            return await hierarchy_gate.audit_url(url, width=composition_width)
        if gate == DATA:
            return await data_gate.audit_url(url, width=width)
        if gate == REFERENCE:
            return await reference_corpus.audit_url(url, width=composition_width)
    else:
        assert files is not None  # render_expected guarantees a target
        if gate == WOW_DOM:
            return await wow_dom_gate.audit_files(files, width=width)
        if gate == PERF_A11Y:
            return await perf_a11y_gate.audit_files(files, width=width)
        if gate == CHIP_PIXEL:
            return await chip_pixel_gate.audit_files(files, spec, width=width)
        if gate == TASTE:
            return await taste_gate.audit_files(files, width=composition_width)
        if gate == HIERARCHY:
            return await hierarchy_gate.audit_files(files, width=composition_width)
        if gate == DATA:
            return await data_gate.audit_files(files, width=width)
        if gate == REFERENCE:
            return await reference_corpus.audit_files(files, width=composition_width)
    raise AssertionError(f"unknown rendered gate: {gate}")  # pragma: no cover


async def run(
    files: dict[str, str] | None = None,
    *,
    url: str | None = None,
    spec: FidelitySpec | None = None,
    width: int = GATE_WIDTH,
    composition_width: int = COMPOSITION_WIDTH,
    include_rendered: bool = True,
    composition: bool = False,
    fidelity: bool = False,
    reference: bool = False,
) -> GauntletVerdict:
    """Fan the selected landed gates over ``files`` and/or a live ``url``.

    * ``defect_registry`` runs whenever ``files`` is given (source scan, pure).
    * the rendered legs run when there is something to render — a ``url`` or a
      static set containing ``index.html``. They run **sequentially** (one
      headless browser at a time) to respect machine RAM, in ``RENDERED_GATES``
      order.

    Two independent dials pick WHICH rendered legs run (V1.6 14/5 decouple):

    * ``include_rendered=True`` (default) runs the full set — wow-dom (incl. the
      44px touch check), perf-a11y, chip-pixel, taste, hierarchy, data. This is
      the standalone CLI / niche-E2E contract and stays the back-compat default.
      ``include_rendered=False`` lets a hot caller take the cheap deterministic
      floor without paying for the extra renders.
    * ``composition=True`` adds the ``COMPOSITION_LEGS`` (taste + hierarchy)
      **regardless** of ``include_rendered``. These are the awwwards richness
      floor with no 44px false-positive, so they are the ALWAYS-ON hard ship-block
      on the product path while the touch leg stays behind calibration (11/5).
    * ``fidelity=True`` adds the ``FIDELITY_LEGS`` (chip-pixel) the same way —
      independent of ``include_rendered``. chip-pixel has no 44px false-positive
      and is inert without a ``spec`` (asserts nothing → passes), so the caller
      switches it on only when there is a real ``discovery_spec`` to honour
      (V2.5.2): a request↔render mismatch then hard-fails the ship path.
    * ``reference=True`` adds the ``REFERENCE_LEGS`` (the pillar-1 CEILING leg,
      V1.13b) the same way — independent of ``include_rendered``. It grades the
      candidate against a curated enterprise corpus and hard-fails a candidate
      that regresses below the corpus on ≥2 richness axes; it ABSTAINS (never a
      hard fail) when the corpus is empty or a page does not render, so it is
      safe to wire before the owner corpus-run that turns it on for the live path.
      Unioning the dials never double-runs a leg.

    ``composition_width`` picks the viewport the composition legs render at
    (default desktop ``1440``; ``390`` for the MOBILE dimension — V1.6 15/5). A
    page rich on desktop but collapsed to a monotone column on mobile fails the
    legs at ``390`` while passing at ``1440``; niche_batch's dual-width loop runs
    both, so its @390 result is a real second render, not a duplicated desktop
    score. The correctness/touch legs always render at the mobile ``width`` floor.

    Fail-soft (R-10): each rendered gate already abstains on a render error, so
    an abstain reports ``passed=False`` but is NOT a ``hard_failed`` — a flaky
    render never sinks an otherwise-good page on the hot path (see
    ``GauntletVerdict.hard_failed`` vs the strict ``.passed``).
    """
    gates: list[GateVerdict] = []

    if files is not None:
        gates.append(_from_registry(defect_registry.scan(files)))

    legs: set[str] = set(RENDERED_GATES) if include_rendered else set()
    if composition:
        legs |= set(COMPOSITION_LEGS)
    if fidelity:
        legs |= set(FIDELITY_LEGS)
    if reference:
        legs |= set(REFERENCE_LEGS)

    has_target = bool(url or (files is not None and "index.html" in files))
    render_expected = bool(legs) and has_target
    if render_expected:
        spec = spec or FidelitySpec()
        for gate in RENDERED_GATES:  # stable order; only the selected legs run
            if gate in legs:
                rep = await _audit_one(
                    gate,
                    files=files,
                    url=url,
                    spec=spec,
                    width=width,
                    composition_width=composition_width,
                )
                gates.append(_from_rendered(gate, rep))

    return GauntletVerdict(tuple(gates), render_expected=render_expected)


# ── CLI: gauntlet a provisioned app dir or a live url (wireable in E2E) ───────


def _parse_spec(argv: list[str]) -> FidelitySpec:
    """Pull optional --palette / --sections / --tone into a FidelitySpec."""
    palette = sections = tone = None
    for i, a in enumerate(argv):
        nxt = argv[i + 1] if i + 1 < len(argv) else None
        if a == "--palette":
            palette = nxt
        elif a == "--sections":
            sections = nxt
        elif a == "--tone":
            tone = nxt
    if palette or sections or tone:
        return FidelitySpec.from_answers(palette=palette, sections=sections, tone=tone)
    return FidelitySpec()


def _main(argv: list[str]) -> int:  # pragma: no cover — thin CLI wrapper
    import asyncio

    target = argv[1] if len(argv) > 1 else "."
    spec = _parse_spec(argv)
    if target.startswith(("http://", "https://")):
        verdict = asyncio.run(run(url=target, spec=spec))
    else:
        files = defect_registry._read_tree(target)
        verdict = asyncio.run(run(files=files, spec=spec))
    print(verdict.summary())
    return 0 if verdict.passed else 1


if __name__ == "__main__":  # pragma: no cover
    import sys

    raise SystemExit(_main(sys.argv))


__all__ = [
    "COMPOSITION_LEGS",
    "COMPOSITION_WIDTH",
    "FIDELITY_LEGS",
    "GATE_WIDTH",
    "REFERENCE_LEGS",
    "RENDERED_GATES",
    "TOUCH_LEGS",
    "GateVerdict",
    "GauntletVerdict",
    "run",
]
