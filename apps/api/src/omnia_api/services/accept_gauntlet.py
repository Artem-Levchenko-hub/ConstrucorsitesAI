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

# Gate identifiers, in the order they appear in the verdict table.
DEFECT_REGISTRY = "defect-registry"
WOW_DOM = "wow-dom"
PERF_A11Y = "perf-a11y"
CHIP_PIXEL = "chip-pixel"
TASTE = "taste"
HIERARCHY = "hierarchy"
DATA = "data"

#: The rendered gates, in run order. ``defect-registry`` is pure source-scan and
#: runs first/always; these need a live DOM (a static file set or a URL). The
#: correctness gates (wow-dom, perf-a11y, chip-pixel, data) run at the mobile
#: floor; ``taste`` and ``hierarchy`` run at desktop width.
RENDERED_GATES = (WOW_DOM, PERF_A11Y, CHIP_PIXEL, TASTE, HIERARCHY, DATA)


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


async def run(
    files: dict[str, str] | None = None,
    *,
    url: str | None = None,
    spec: FidelitySpec | None = None,
    width: int = GATE_WIDTH,
    include_rendered: bool = True,
) -> GauntletVerdict:
    """Fan every landed gate over ``files`` and/or a live ``url``.

    * ``defect_registry`` runs whenever ``files`` is given (source scan, pure).
    * the rendered gates (wow-dom, perf-a11y, chip-pixel) run when
      ``include_rendered`` is set AND there is something to render — a ``url`` or
      a static set containing ``index.html``. They run **sequentially** (one
      headless browser at a time) to respect machine RAM.

    ``include_rendered=False`` lets a hot caller (the freeform acceptance gate on
    the product-default path) take the cheap deterministic floor without paying
    for three extra renders; the standalone CLI / niche-E2E keeps the default.

    Fail-soft: each rendered gate already abstains on a render error, so this
    never raises on a flaky page (R-10).
    """
    gates: list[GateVerdict] = []

    if files is not None:
        gates.append(_from_registry(defect_registry.scan(files)))

    render_expected = include_rendered and bool(
        url or (files is not None and "index.html" in files)
    )
    if render_expected:
        spec = spec or FidelitySpec()
        if url:
            wow = await wow_dom_gate.audit_url(url, width=width)
            perf = await perf_a11y_gate.audit_url(url, width=width)
            chip = await chip_pixel_gate.audit_url(url, spec, width=width)
            taste = await taste_gate.audit_url(url, width=TASTE_WIDTH)
            hierarchy = await hierarchy_gate.audit_url(url, width=HIERARCHY_WIDTH)
            data = await data_gate.audit_url(url, width=width)
        else:
            assert files is not None  # render_expected guarantees index.html
            wow = await wow_dom_gate.audit_files(files, width=width)
            perf = await perf_a11y_gate.audit_files(files, width=width)
            chip = await chip_pixel_gate.audit_files(files, spec, width=width)
            taste = await taste_gate.audit_files(files, width=TASTE_WIDTH)
            hierarchy = await hierarchy_gate.audit_files(files, width=HIERARCHY_WIDTH)
            data = await data_gate.audit_files(files, width=width)
        gates.append(_from_rendered(WOW_DOM, wow))
        gates.append(_from_rendered(PERF_A11Y, perf))
        gates.append(_from_rendered(CHIP_PIXEL, chip))
        gates.append(_from_rendered(TASTE, taste))
        gates.append(_from_rendered(HIERARCHY, hierarchy))
        gates.append(_from_rendered(DATA, data))

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
    "GATE_WIDTH",
    "RENDERED_GATES",
    "GateVerdict",
    "GauntletVerdict",
    "run",
]
