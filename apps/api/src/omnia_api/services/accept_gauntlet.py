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
    catalog_coherence_gate,
    chip_pixel_gate,
    compose_gate,
    data_gate,
    defect_registry,
    edit_registry,
    hierarchy_gate,
    onboarding_registry,
    perf_a11y_gate,
    reference_corpus,
    render_registry,
    taste_gate,
    viral_registry,
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
COMPOSE = "compose"
ONBOARDING = "onboarding"
RENDER = "render"
EDIT = "edit"
VIRAL = "viral"
WOW_DOM = "wow-dom"
PERF_A11Y = "perf-a11y"
CHIP_PIXEL = "chip-pixel"
TASTE = "taste"
HIERARCHY = "hierarchy"
DATA = "data"
REFERENCE = "reference"
CATALOG = "catalog"

#: The rendered gates, in run order. ``defect-registry`` is pure source-scan and
#: runs first/always; these need a live DOM (a static file set or a URL). The
#: correctness gates (wow-dom, perf-a11y, chip-pixel, data) run at the mobile
#: floor; ``taste``, ``hierarchy``, ``reference`` and ``catalog`` run at desktop
#: width. ``reference`` (V1.13b) is the pillar-1 CEILING leg — the candidate must
#: meet or beat a curated enterprise corpus; it is a member of the order tuple (so
#: it is fanned by ``run()`` and not orphaned) but, like a flag-gated gate, only
#: fires when ``include_rendered`` is on OR the ``reference=`` dial is set.
#: ``catalog`` (V1.17) is the catalog-realism ratchet — see ``ADVISORY_GATES``: it
#: is in the order tuple (so ``run()`` fans it) but is ADVISORY (a non-blocking
#: quality-card) and runs ONLY behind its own ``catalog=`` dial, never via
#: ``include_rendered``.
RENDERED_GATES = (WOW_DOM, PERF_A11Y, CHIP_PIXEL, TASTE, HIERARCHY, DATA, REFERENCE, CATALOG)

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
#: The CATALOG leg (V1.17) — the catalog-realism ratchet. The eight money-free
#: RULE-10 demo-seeder fixes (niche titles/price-bands/real images/title↔category/
#: title↔description/category synonyms/future dates/niche emails) each shipped with
#: a unit test, but the only gate over the seeder's output, ``data_gate``, measures
#: ``MIN_ROWS >= 6`` — non-emptiness, never realism, so a NEW niche could silently
#: regress any class. This leg scores the rendered catalog DOM across those realism
#: axes (price-band/title↔category/title↔description/image-resolves/date-future),
#: reusing ``taste_gate``'s JS-extract→Python-score shape (R-04). It is decoupled
#: from ``include_rendered`` via its own ``catalog=`` dial.
CATALOG_LEGS = (CATALOG,)
#: ADVISORY gates surface a quality-card (a score + findings in the table / summary
#: / subscore) but NEVER block ship — they are excluded from ``hard_failed``, the
#: strict ``passed`` verdict, and ``failed_classes``. V1.17 folds ``catalog`` in
#: this way: the realism ratchet teaches without false-rejecting good catalogs
#: while the niche heuristics earn trust. For every NON-advisory gate this set is
#: empty, so the keystone's ship semantics are byte-identical.
ADVISORY_GATES = frozenset({CATALOG})


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

        ADVISORY gates (V1.17 ``catalog``) are excluded — a non-blocking quality
        card never decides the strict verdict; a low realism score must not exit
        the CLI 1 while the niche heuristics earn trust.
        """
        blocking = tuple(g for g in self.gates if g.gate not in ADVISORY_GATES)
        return bool(blocking) and all(g.passed for g in blocking)

    @property
    def hard_failed(self) -> tuple[GateVerdict, ...]:
        """Gates with a REAL finding (not a mere abstain).

        The hot path (``acceptance.evaluate``) blocks ship on these only: a flaky
        local render that abstains must not sink an otherwise-good page, but a
        deterministic defect or a concrete rubric violation must. ADVISORY gates
        (V1.17 ``catalog``) are excluded — they surface but never block.
        """
        return tuple(
            g
            for g in self.gates
            if g.gate not in ADVISORY_GATES and not g.passed and not g.abstained
        )

    @property
    def abstained(self) -> tuple[GateVerdict, ...]:
        return tuple(g for g in self.gates if g.abstained)

    @property
    def failed_classes(self) -> tuple[str, ...]:
        """Gate-prefixed classes of every non-passing gate (`gate:class`).

        ADVISORY gates (V1.17 ``catalog``) are excluded — this list feeds the
        BLOCKED-ship issue set, so a non-blocking observation must not leak into
        it; the catalog card's own findings live in its per-gate ``subscore``.
        """
        out: list[str] = []
        for g in self.gates:
            if g.gate in ADVISORY_GATES:
                continue
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


def viral_eligible_from_verdict(verdict: GauntletVerdict) -> bool:
    """Certify a project's shared surface against the beauty floor (V4.9).

    The bridge from pillar 1 (WOW design) to pillar 4 (virality): a project may
    enter the viral pool — its zero-signup forks inherit the right to be
    re-shared — only when the surface a stranger sees is itself floor-green.

    The contract (reuses the existing per-gate verdicts, R-04 — no new metric):

    * ``taste`` AND ``hierarchy`` must have been MEASURED and PASSED. These are
      the awwwards floor of pillar 1; a surface we never scored on them cannot
      be vouched for, so a missing or abstained leg is never eligible (mirrors
      ``GauntletVerdict.passed`` — no evidence ≠ a pass).
    * NO gate may carry a real finding. When the ``viral`` leg ran (it folds
      first-paint over the served surface), a hard-fail there disqualifies too —
      so the stranger-cold path adds first-paint as a hard requirement, while
      the authenticated entity gate certifies on taste+hierarchy alone. Abstains
      (a flaky render of an optional leg) are tolerated; the caller decides
      whether to write the verdict on a flaky run (see ``workers.quality``).
    """
    by_gate = {g.gate: g for g in verdict.gates}
    floor = (by_gate.get(TASTE), by_gate.get(HIERARCHY))
    if any(g is None or g.abstained or not g.passed for g in floor):
        return False
    return not verdict.hard_failed


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


def _from_compose(rep: compose_gate.ComposeReport) -> GateVerdict:
    """Adapt the compose floor (V3.3) — a pure source-scan, like the registry.

    It never abstains: a set with no standalone HTML page is INERT and reports
    ``passed=True`` (nothing to judge ≠ a flaky render with no evidence).
    """
    return GateVerdict(
        gate=COMPOSE,
        passed=rep.passed,
        abstained=False,  # pure source-scan — its evidence is always present
        classes=rep.classes,
        summary=rep.summary(),
        subscore=rep.subscore(),
    )


def _from_viral(rep: viral_registry.ViralReport) -> GateVerdict:
    """Adapt the viral registry (V4.6) — a pure context-scan, like the compose floor.

    It never abstains: an episode with nothing to judge is INERT and reports
    ``passed=True`` (nothing to judge ≠ a flaky render with no evidence). It folds
    ``first_paint_gate`` internally over the served-surface observation (R-04).
    """
    return GateVerdict(
        gate=VIRAL,
        passed=rep.passed,
        abstained=False,  # pure context-scan — its evidence is always present
        classes=rep.classes,
        summary=rep.summary(),
        subscore=rep.subscore(),
    )


def _from_onboarding(rep: onboarding_registry.OnboardingReport) -> GateVerdict:
    """Adapt the onboarding registry (V2.7) — a pure context-scan, like the viral one.

    It never abstains: a turn with no ASK card to judge (a BUILD turn, an empty
    context) is INERT and reports ``passed=True`` (nothing to judge ≠ a flaky
    render with no evidence). Its chip-hygiene verdict is single-sourced on
    :func:`discovery._clean_choices` (R-04).
    """
    return GateVerdict(
        gate=ONBOARDING,
        passed=rep.passed,
        abstained=False,  # pure context-scan — its evidence is always present
        classes=rep.classes,
        summary=rep.summary(),
        subscore=rep.subscore(),
    )


def _from_render(rep: render_registry.RenderReport) -> GateVerdict:
    """Adapt the render registry (V3.12) — a pure context-scan, like the onboarding one.

    It never abstains: a turn with no brief to narrate (a non-generation turn, an
    empty context) is INERT and reports ``passed=True`` (nothing to narrate ≠ a
    flaky render with no evidence). Its narratable-payload verdict is single-sourced
    on :func:`art_director_writer.parse_brief` (R-04).
    """
    return GateVerdict(
        gate=RENDER,
        passed=rep.passed,
        abstained=False,  # pure context-scan — its evidence is always present
        classes=rep.classes,
        summary=rep.summary(),
        subscore=rep.subscore(),
    )


def _from_edit(rep: edit_registry.EditReport) -> GateVerdict:
    """Adapt the edit-loop registry (V1.11) — a pure context-scan, like the render one.

    It never abstains: a turn that is not an edit (no BEFORE/AFTER pair to compare,
    an empty context) is INERT and reports ``passed=True`` (nothing to compare ≠ a
    flaky render with no evidence). Its iteration-regression verdict is single-sourced
    on the gauntlet's own ``passed_classes`` and the snapshot path's section
    signatures (R-04).
    """
    return GateVerdict(
        gate=EDIT,
        passed=rep.passed,
        abstained=False,  # pure context-scan — its evidence is always present
        classes=rep.classes,
        summary=rep.summary(),
        subscore=rep.subscore(),
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
        if gate == CATALOG:
            return await catalog_coherence_gate.audit_url(url, width=composition_width)
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
        if gate == CATALOG:
            return await catalog_coherence_gate.audit_files(files, width=composition_width)
    raise AssertionError(f"unknown rendered gate: {gate}")  # pragma: no cover


async def run(
    files: dict[str, str] | None = None,
    *,
    url: str | None = None,
    spec: FidelitySpec | None = None,
    width: int = GATE_WIDTH,
    composition_width: int = COMPOSITION_WIDTH,
    include_rendered: bool = True,
    compose: bool = False,
    composition: bool = False,
    fidelity: bool = False,
    reference: bool = False,
    catalog: bool = False,
    viral: bool = False,
    viral_context: viral_registry.ViralContext | None = None,
    onboarding: bool = False,
    onboarding_context: onboarding_registry.OnboardingContext | None = None,
    render: bool = False,
    render_context: render_registry.RenderContext | None = None,
    edit: bool = False,
    edit_context: edit_registry.EditContext | None = None,
) -> GauntletVerdict:
    """Fan the selected landed gates over ``files`` and/or a live ``url``.

    * ``defect_registry`` runs whenever ``files`` is given (source scan, pure).
    * the rendered legs run when there is something to render — a ``url`` or a
      static set containing ``index.html``. They run **sequentially** (one
      headless browser at a time) to respect machine RAM, in ``RENDERED_GATES``
      order.

    * ``compose=True`` adds the V3.3 composition FLOOR — a pure source-scan (no
      render) that hard-fails a catastrophically flat freeform ``index.html`` (one
      type size / no section rhythm / no hero) before any paid render or the
      advisory vision pass. It is INERT on a set with no standalone HTML page
      (entity stacks), so it never false-positives the live hot path.

    * ``viral=True`` adds the V4.6 viral defect-registry — also a pure
      context-scan (no render). It scores the ``viral_context`` (one share→fork
      episode) against the viral invariants — link-served-before-done, dead remix
      CTA, leaked-tenant fork, dropped seed param — and folds ``first_paint_gate``
      over the served-surface observation (R-04). It is INERT on a ``None`` /
      empty context, so it never false-fails; a fork is not a generation, so it is
      independent of ``files`` and exercised by the synthetic viral-loop (V4.7) /
      the paid-run manifest, not the per-gen ship path.

    * ``onboarding=True`` adds the V2.7 onboarding defect-registry — also a pure
      context-scan (no render, no LLM). It scores the ``onboarding_context`` (one
      discovery ASK turn) against the pillar-2 invariants — bare-text question,
      trapped-no-«Другое», dirty chips — single-sourcing the chip-hygiene verdict
      on ``discovery._clean_choices`` (R-04). It is INERT on a BUILD / empty /
      ``None`` context, so it never false-fails; an onboarding turn is not a
      generation, so it is independent of ``files``.

    * ``render=True`` adds the V3.12 render defect-registry — also a pure
      context-scan (no render, no LLM). It scores the ``render_context`` (one
      rendered generation turn's art-director brief) against the pillar-3
      invariants — silent render, swatchless render, motionless render —
      single-sourcing the narratable-payload verdict on
      ``art_director_writer.parse_brief`` (R-04). It is INERT on a no-brief / empty /
      ``None`` context, so it never false-fails; a brief is not a file set, so it is
      independent of ``files``.

    * ``edit=True`` adds the V1.11 edit-loop regression registry — also a pure
      context-scan (no render, no LLM). It scores ONE edit turn (the
      ``edit_context``'s BEFORE gen-1 + AFTER post-edit snapshots) against the
      iteration invariants — a previously-clean gauntlet class regressing, an
      untargeted section mutating as collateral, the rollback snapshot failing to
      restore the gen-1 surface — single-sourcing the verdict on the gauntlet's own
      ``passed_classes`` and the snapshot path's section signatures (R-04). It is
      INERT on a non-edit (no BEFORE/AFTER pair) / empty / ``None`` context, so it
      never false-fails; an edit turn is not a first generation, so it is independent
      of ``files`` and exercised by the iteration ratchet / the paid-run manifest.

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
    * ``catalog=True`` adds the ``CATALOG_LEGS`` (the V1.17 catalog-realism
      ratchet) — independent of ``include_rendered`` and ADVISORY: it surfaces a
      realism quality-card (a 0–5 score + the fired axes in the table / summary /
      subscore) but NEVER blocks ship (excluded from ``hard_failed`` / strict
      ``passed`` / ``failed_classes``). It ABSTAINS on a render miss and WAIVES a
      page with no catalog grid (R-10), so wiring it is safe everywhere; the hot
      path leaves it OFF until the owner flips it on, while the CLI / paid-run
      manifest fold it in to watch the eight RULE-10 classes stay a floor.

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
        # V3.3 — the money-free composition floor. Like the defect registry it is a
        # pure source-scan (no render), so it runs BEFORE the rendered legs and its
        # finding lands in ``hard_failed`` — hard-blocking a catastrophically flat
        # page before any paid render or the advisory vision pass. It is INERT (a
        # passing no-op) on a set with no standalone ``index.html`` (entity stacks,
        # judged by the rendered legs), so wiring it is safe on every path.
        if compose:
            gates.append(_from_compose(compose_gate.scan(files)))

    # V4.6 — the money-free viral defect-registry. Like the compose floor it is a
    # pure CONTEXT-scan (no render): it scores one share→fork episode (the
    # ``viral_context``) against the falsifiable viral invariants and folds
    # first_paint_gate over the served-surface observation (R-04). It is INERT (a
    # passing no-op) on an empty/None context, so wiring it is safe on every path;
    # it is independent of ``files`` (a share/fork episode is not a generation).
    if viral:
        gates.append(_from_viral(viral_registry.scan(viral_context)))

    # V2.7 — the money-free onboarding defect-registry. Like the viral registry it
    # is a pure CONTEXT-scan (no render, no LLM): it scores one discovery ASK turn
    # (the ``onboarding_context``) against the falsifiable pillar-2 invariants —
    # bare-text question, trapped-no-«Другое», dirty chips — single-sourcing the
    # chip-hygiene verdict on ``discovery._clean_choices`` (R-04). It is INERT (a
    # passing no-op) on a BUILD / empty / None context, so wiring it is safe on
    # every path; an onboarding turn is not a generation, so it is independent of
    # ``files`` and exercised by the pillar-2 finish (V2.9) / the paid-run manifest.
    if onboarding:
        gates.append(_from_onboarding(onboarding_registry.scan(onboarding_context)))

    # V3.12 — the money-free render defect-registry. Like the onboarding registry it
    # is a pure CONTEXT-scan (no render, no LLM): it scores one rendered generation
    # turn (the ``render_context``'s art-director brief) against the falsifiable
    # pillar-3 invariants — silent render, swatchless render, motionless render —
    # single-sourcing the narratable-payload verdict on
    # ``art_director_writer.parse_brief`` (R-04), the exact payload the ``omnia:brief``
    # event ships to the client. It is INERT (a passing no-op) on a no-brief / empty /
    # None context, so wiring it is safe on every path; a brief is not a file set, so
    # it is independent of ``files`` and exercised by the pillar-3 finish / the
    # paid-run manifest, not the per-gen ship path.
    if render:
        gates.append(_from_render(render_registry.scan(render_context)))

    # V1.11 — the money-free edit-loop regression registry. Like the render registry
    # it is a pure CONTEXT-scan (no render, no LLM): it scores ONE edit turn (the
    # ``edit_context``'s BEFORE gen-1 + AFTER post-edit snapshots) against the
    # falsifiable iteration invariants — a previously-clean gauntlet class regressing,
    # an untargeted section mutating as collateral, the rollback snapshot failing to
    # restore the gen-1 surface — single-sourcing the verdict on the gauntlet's own
    # ``passed_classes`` and the snapshot path's section signatures (R-04). It is
    # INERT (a passing no-op) on a non-edit / empty / None context, so wiring it is
    # safe on every path; an edit turn is not a first generation, so it is independent
    # of ``files`` and exercised by the iteration ratchet / the paid-run manifest, not
    # the per-gen ship path.
    if edit:
        gates.append(_from_edit(edit_registry.scan(edit_context)))

    # ``include_rendered`` fans the BLOCKING rendered legs only; advisory legs
    # (V1.17 ``catalog``) never ride the broad dial — they run solely behind their
    # own switch, so a hot caller that flips ``include_rendered`` on never silently
    # pays for the advisory render.
    legs: set[str] = (
        {g for g in RENDERED_GATES if g not in ADVISORY_GATES}
        if include_rendered
        else set()
    )
    if composition:
        legs |= set(COMPOSITION_LEGS)
    if fidelity:
        legs |= set(FIDELITY_LEGS)
    if reference:
        legs |= set(REFERENCE_LEGS)
    if catalog:
        legs |= set(CATALOG_LEGS)

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
        verdict = asyncio.run(run(url=target, spec=spec, catalog=True))
    else:
        files = defect_registry._read_tree(target)
        verdict = asyncio.run(run(files=files, spec=spec, compose=True, catalog=True))
    print(verdict.summary())
    return 0 if verdict.passed else 1


if __name__ == "__main__":  # pragma: no cover
    import sys

    raise SystemExit(_main(sys.argv))


__all__ = [
    "ADVISORY_GATES",
    "CATALOG_LEGS",
    "COMPOSE",
    "COMPOSITION_LEGS",
    "COMPOSITION_WIDTH",
    "FIDELITY_LEGS",
    "GATE_WIDTH",
    "REFERENCE_LEGS",
    "RENDERED_GATES",
    "TOUCH_LEGS",
    "VIRAL",
    "GateVerdict",
    "GauntletVerdict",
    "run",
]
