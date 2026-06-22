"""V1.13a — reference-corpus comparator: the pillar-1 CEILING ratchet.

The acceptance gauntlet's taste/hierarchy gates measure a FLOOR — "no defect
class is present". This module flips that into a CEILING claim — "a fresh
generation must MEET OR BEAT a hand-curated enterprise reference on its own
richness axes" — without inventing a new metric. It reads the EXACT per-axis
verdicts the gates already emit (``TasteReport.subscore()["checks"]`` +
``HierarchyReport.subscore()["checks"]``, R-04 single-source) and projects them
onto the five comparison axes the plan names (V1.13):

    type-scale, layout-variety, hero-imagery   (taste_gate)
    focal-dominance, asymmetry                  (hierarchy_gate)

The corpus lives in ``apps/api/src/omnia_api/services/reference_corpus_data/<niche>.html``
— hand-curated enterprise / awwwards snapshots, each citing its source in an HTML
comment, append-only, owner-approved. It sits under ``src/`` (package data) rather
than ``tests/`` on purpose (V1.13b): the prod api/worker image ``.dockerignore``s
``tests/``, so a corpus there would be empty at runtime and the gate would abstain
forever. Shipping it as package data makes the ceiling enforceable in the live
container. The adversarial ``tests/fixtures/bootstrap-baseline.html`` (the same
fixture the taste gate keeps red) MUST fall below the corpus on these axes; that is
the falsifiable teeth — it stays in ``tests/`` because only the test suite needs it.

Money-free, 0 LLM: hand-curation once + a deterministic compare. Only the final
"a live fresh generation beats the corpus" step needs an owner corpus-run.

V1.13b wires a ``REFERENCE`` leg into ``accept_gauntlet`` (the gate runs in
``RENDERED_GATES`` order, fanned by ``run()`` behind a dedicated ``reference=``
dial) via :func:`audit_files` / :func:`audit_url` + :class:`ReferenceReport`, so
this comparator no longer orphans.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from . import hierarchy_gate, taste_gate

log = logging.getLogger(__name__)

# The five comparison axes (V1.13). Each is a check one of the gates ALREADY
# scores — not a new vector. Order is canonical (taste axes, then hierarchy).
TASTE_AXES: tuple[str, ...] = (
    taste_gate.TYPE_SCALE,
    taste_gate.LAYOUT_VARIETY,
    taste_gate.HERO_IMAGERY,
)
HIERARCHY_AXES: tuple[str, ...] = (
    hierarchy_gate.FOCAL_DOMINANCE,
    hierarchy_gate.ASYMMETRY,
)
RICHNESS_AXES: tuple[str, ...] = TASTE_AXES + HIERARCHY_AXES

#: A candidate must meet-or-beat the reference on at least this many of the five
#: axes. ``>= 4/5`` mirrors the plan: a single regression is tolerated, two is a
#: hard fail.
MIN_AXES = 4

#: V1.13c — the mirror of :data:`MIN_AXES` from the adversary's side. A candidate
#: is BELOW the ceiling exactly when it regresses on more axes than the floor
#: tolerates, i.e. ``len(RICHNESS_AXES) - MIN_AXES + 1``. Derived (not hardcoded)
#: so the two thresholds can never drift: ``meets_or_beats`` failing ⟺
#: ``len(adversary_regressions(...)) >= MIN_REGRESSIONS``.
MIN_REGRESSIONS = len(RICHNESS_AXES) - MIN_AXES + 1

#: The defect class a below-corpus candidate carries into the gauntlet's
#: ``failed_classes`` (becomes ``reference:reference-below``).
BELOW_CLASS = "reference-below"

#: Combined richness score = taste richness (0..len(taste_gate.CHECKS)) + hierarchy
#: richness (0..len(hierarchy_gate.CHECKS)). The CONTINUOUS ceiling signal the
#: boolean axes throw away: a 4/5+2/3 generation vs a 5/5+3/3 reference is BELOW the
#: ceiling even though both clear every boolean floor (V1.13d).
MAX_RICHNESS_SCORE = len(taste_gate.CHECKS) + len(hierarchy_gate.CHECKS)

#: Corpus location — package data shipped with the api/worker image (V1.13b).
#: It lives next to this module under ``services/reference_corpus_data`` so the
#: prod image ships it; ``tests/`` is ``.dockerignore``d and would be empty at
#: runtime (the gate would then abstain forever).
CORPUS_DIR = Path(__file__).resolve().parent / "reference_corpus_data"

#: ``{axis: passed}`` over exactly :data:`RICHNESS_AXES`.
RichnessVector = dict[str, bool]


def richness_vector(
    taste_checks: dict[str, bool] | None,
    hierarchy_checks: dict[str, bool] | None,
) -> RichnessVector:
    """Project the two gates' per-axis verdicts onto the five comparison axes.

    ``taste_checks`` / ``hierarchy_checks`` are the ``subscore()["checks"]`` dicts
    (``{axis: passed}``). A missing axis reads as NOT passed — an abstaining
    gate must never inflate a vector.
    """
    merged: dict[str, bool] = {}
    merged.update(taste_checks or {})
    merged.update(hierarchy_checks or {})
    return {axis: bool(merged.get(axis, False)) for axis in RICHNESS_AXES}


def richness_score(taste_rep: object | None, hier_rep: object | None) -> int:
    """Combined richness score = taste.score + hierarchy.score over the rendered
    gates. A gate that abstained (or is None) contributes 0 — an un-rendered gate
    must never inflate the ceiling (R-10). Reads the SAME ``.score`` the gauntlet
    table shows (R-04, no new metric)."""
    t = int(getattr(taste_rep, "score", 0) or 0) if getattr(taste_rep, "rendered", False) else 0
    h = int(getattr(hier_rep, "score", 0) or 0) if getattr(hier_rep, "rendered", False) else 0
    return t + h


def axes_met_or_beaten(
    generated: RichnessVector, reference: RichnessVector
) -> tuple[str, ...]:
    """Axes where ``generated`` is no worse than ``reference``.

    A regression is only ``reference passes`` while ``generated fails``
    (``False < True``); matching or exceeding the reference holds the axis.
    """
    return tuple(
        a
        for a in RICHNESS_AXES
        if bool(generated.get(a, False)) >= bool(reference.get(a, False))
    )


def meets_or_beats(
    generated: RichnessVector, reference: RichnessVector, *, min_axes: int = MIN_AXES
) -> bool:
    """True iff ``generated`` holds at least ``min_axes`` of the five axes."""
    return len(axes_met_or_beaten(generated, reference)) >= min_axes


def adversary_regressions(
    candidate: RichnessVector, reference: RichnessVector
) -> tuple[str, ...]:
    """Axes where ``reference`` passes but ``candidate`` regresses (V1.13c).

    The complement of :func:`axes_met_or_beaten` projected to the human-readable
    side: these are the named axes a below-ceiling candidate *loses*, so the
    adversary-pre-proof can show exactly WHERE a mediocre baseline falls short.
    By construction ``len(adversary_regressions(c, r)) >= MIN_REGRESSIONS`` is
    the exact negation of ``meets_or_beats(c, r)`` — one source, no drift.
    """
    return tuple(
        a
        for a in RICHNESS_AXES
        if bool(reference.get(a, False)) and not bool(candidate.get(a, False))
    )


@dataclass(frozen=True)
class CorpusComparison:
    """One candidate-vs-reference verdict on the five-axis richness vector."""

    niche: str
    candidate: RichnessVector
    reference: RichnessVector
    met: tuple[str, ...]
    rendered: bool
    min_axes: int = MIN_AXES
    #: Combined taste+hierarchy richness (0..MAX_RICHNESS_SCORE). 0 when not scored
    #: (abstain / legacy boolean-only call) — never inflates the ceiling.
    candidate_score: int = 0
    reference_score: int = 0
    #: V1.13d — when True the comparison also requires the CONTINUOUS score floor
    #: (candidate within ``tolerance`` of the reference's own richness). Default
    #: False ⇒ pure boolean floor, byte-identical to V1.13b.
    enforce_score: bool = False
    tolerance: int = 0

    @property
    def score_floor_held(self) -> bool:
        """The continuous ceiling: candidate scores within ``tolerance`` of the
        reference's own richness. Only meaningful on a rendered pair against a
        reference that is itself rich (callers gate this behind a credible
        reference render — an un-rich reference is no ceiling)."""
        return self.candidate_score >= self.reference_score - self.tolerance

    @property
    def passed(self) -> bool:
        """A comparison that could not render BOTH pages abstains (not pass).

        The BOOLEAN floor (>= ``min_axes`` axes held) is always required. When
        ``enforce_score`` (V1.13d, dark), the CONTINUOUS score floor is required
        too — that is the real CEILING ratchet."""
        if not self.rendered:
            return False
        if len(self.met) < self.min_axes:
            return False
        if self.enforce_score and not self.score_floor_held:
            return False
        return True

    def summary(self) -> str:
        if not self.rendered:
            return f"reference[{self.niche}]: ABSTAIN (a page did not render)"
        verdict = "MEETS-OR-BEATS" if self.passed else "BELOW"
        misses = [a for a in RICHNESS_AXES if a not in self.met]
        tail = "" if not misses else f" — regressions: {', '.join(misses)}"
        score_tail = (
            f" [richness {self.candidate_score}/{self.reference_score} ceiling]"
            if self.enforce_score
            else ""
        )
        return (
            f"reference[{self.niche}]: {verdict} "
            f"({len(self.met)}/{len(RICHNESS_AXES)} axes ≥ reference, "
            f"floor {self.min_axes}){score_tail}{tail}"
        )


async def vector_of_files(
    files: dict[str, str], *, width: int = taste_gate.GATE_WIDTH
) -> tuple[RichnessVector, bool, int]:
    """Render a static ``{path: html}`` set once per gate and return its
    five-axis richness vector, whether BOTH gates actually rendered, and the
    combined richness SCORE (taste+hierarchy, V1.13d).

    Fail-soft: a gate that abstains contributes no passing axes / no score (R-10).
    """
    taste_rep = await taste_gate.audit_files(files, width=width)
    hier_rep = await hierarchy_gate.audit_files(files, width=hierarchy_gate.GATE_WIDTH)
    rendered = taste_rep.rendered and hier_rep.rendered
    taste_checks = taste_rep.subscore()["checks"] if taste_rep.rendered else {}
    hier_checks = hier_rep.subscore()["checks"] if hier_rep.rendered else {}
    return richness_vector(taste_checks, hier_checks), rendered, richness_score(taste_rep, hier_rep)


async def vector_of_html(
    html: str, *, width: int = taste_gate.GATE_WIDTH
) -> tuple[RichnessVector, bool, int]:
    """Convenience: richness vector + rendered + score of a single HTML document."""
    return await vector_of_files({"index.html": html}, width=width)


async def vector_of_url(
    url: str, *, width: int = taste_gate.GATE_WIDTH
) -> tuple[RichnessVector, bool, int]:
    """Render a live ``url`` once per gate and return its five-axis richness
    vector, whether BOTH gates rendered, and the combined richness score (R-10)."""
    taste_rep = await taste_gate.audit_url(url, width=width)
    hier_rep = await hierarchy_gate.audit_url(url, width=hierarchy_gate.GATE_WIDTH)
    rendered = taste_rep.rendered and hier_rep.rendered
    taste_checks = taste_rep.subscore()["checks"] if taste_rep.rendered else {}
    hier_checks = hier_rep.subscore()["checks"] if hier_rep.rendered else {}
    return richness_vector(taste_checks, hier_checks), rendered, richness_score(taste_rep, hier_rep)


def load_corpus(corpus_dir: Path = CORPUS_DIR) -> dict[str, str]:
    """``{niche: html}`` for every ``<niche>.html`` in the corpus (sorted)."""
    if not corpus_dir.is_dir():
        return {}
    return {
        p.stem: p.read_text(encoding="utf-8")
        for p in sorted(corpus_dir.glob("*.html"))
    }


async def _compare_vector_to_corpus(
    cand_vec: RichnessVector,
    cand_rendered: bool,
    *,
    corpus_dir: Path,
    min_axes: int,
    width: int,
    cand_score: int = 0,
    enforce_score: bool = False,
    tolerance: int = 0,
) -> list[CorpusComparison]:
    """Compare an already-rendered candidate vector against every corpus
    reference. Shared by :func:`compare_to_corpus` (single HTML) and the gauntlet
    adapter (:func:`audit_files` / :func:`audit_url`).

    When ``enforce_score`` (V1.13d), a pair is only credible — and thus only able
    to FAIL the candidate on the score floor — if the reference itself rendered
    AND cleared its own boolean floor. A thin/flaky reference render is NOT a bar
    to fail a candidate against: that pair is marked not-rendered → ABSTAIN (R-10).
    """
    corpus = load_corpus(corpus_dir)
    out: list[CorpusComparison] = []
    for niche, ref_html in corpus.items():
        ref_vec, ref_rendered, ref_score = await vector_of_html(ref_html, width=width)
        met = axes_met_or_beaten(cand_vec, ref_vec)
        # A reference is a credible CEILING only if it itself clears the boolean
        # floor; an un-rich corpus render (flaky / thin) is NOT a bar to fail a
        # candidate against — treat that pair as not-rendered → ABSTAIN (R-10).
        # Count the axes the reference actually HOLDS (its True axes); note
        # ``axes_met_or_beaten(ref, ref)`` would wrongly count all five since
        # ``False >= False`` holds, so we count held axes directly (mirrors
        # ``test_each_reference_fixture_is_genuinely_rich``).
        ref_held = sum(1 for a in RICHNESS_AXES if ref_vec.get(a, False))
        ref_is_credible = ref_rendered and ref_held >= min_axes
        rendered = cand_rendered and ref_rendered and (
            ref_is_credible if enforce_score else True
        )
        out.append(
            CorpusComparison(
                niche=niche,
                candidate=cand_vec,
                reference=ref_vec,
                met=met,
                rendered=rendered,
                min_axes=min_axes,
                candidate_score=cand_score,
                reference_score=ref_score,
                enforce_score=enforce_score,
                tolerance=tolerance,
            )
        )
    return out


async def compare_to_corpus(
    candidate_html: str,
    *,
    corpus_dir: Path = CORPUS_DIR,
    min_axes: int = MIN_AXES,
    width: int = taste_gate.GATE_WIDTH,
    enforce_score: bool = False,
    tolerance: int = 0,
) -> list[CorpusComparison]:
    """Render ``candidate_html`` and every corpus reference, then report a
    meet-or-beat verdict per niche. The candidate is rendered ONCE and compared
    against each reference's own vector.
    """
    cand_vec, cand_rendered, cand_score = await vector_of_html(candidate_html, width=width)
    return await _compare_vector_to_corpus(
        cand_vec, cand_rendered, corpus_dir=corpus_dir, min_axes=min_axes, width=width,
        cand_score=cand_score, enforce_score=enforce_score, tolerance=tolerance,
    )


# ── gauntlet adapter (V1.13b) ─────────────────────────────────────────────────
# A gate-report-shaped wrapper so ``accept_gauntlet`` can fan the comparator as
# the ``REFERENCE`` leg, adapting it through the same ``_from_rendered`` path as
# the taste/hierarchy/data gates (``.passed`` / ``.rendered`` / ``.classes`` /
# ``.summary()`` / ``.subscore()``).


@dataclass(frozen=True)
class ReferenceReport:
    """Aggregate of one candidate-vs-corpus run, shaped like a rendered gate.

    The CEILING claim is strict: the candidate must MEET-OR-BEAT *every* curated
    reference (i.e. beat the hardest one), so a generation that regresses below
    the corpus on ≥2 axes against any niche hard-fails. An empty corpus or a
    render miss yields ``rendered=False`` → ABSTAIN (R-10): the gate never sinks
    ship on missing evidence, only on a real below-corpus finding.
    """

    comparisons: tuple[CorpusComparison, ...]

    @property
    def rendered(self) -> bool:
        return bool(self.comparisons) and all(c.rendered for c in self.comparisons)

    @property
    def passed(self) -> bool:
        return self.rendered and all(c.passed for c in self.comparisons)

    @property
    def classes(self) -> tuple[str, ...]:
        # Abstain (not rendered) carries no class — no evidence is not a finding.
        if not self.rendered or self.passed:
            return ()
        return (BELOW_CLASS,)

    def summary(self) -> str:
        if not self.comparisons:
            return "reference: ABSTAIN (no corpus to compare against)"
        if not self.rendered:
            return "reference: ABSTAIN (a page did not render)"
        verdict = "MEETS-OR-BEATS" if self.passed else "BELOW"
        below = [c.niche for c in self.comparisons if not c.passed]
        tail = "" if not below else f" — below: {', '.join(below)}"
        return f"reference: {verdict} corpus ({len(self.comparisons)} niches){tail}"

    def subscore(self) -> dict[str, object]:
        return {
            "gate": "reference",
            "passed": self.passed,
            "rendered": self.rendered,
            "axes": list(RICHNESS_AXES),
            "comparisons": [
                {
                    "niche": c.niche,
                    "passed": c.passed,
                    "met": list(c.met),
                    "rendered": c.rendered,
                }
                for c in self.comparisons
            ],
        }

    @property
    def niches_met(self) -> int:
        """How many corpus niches the candidate meets-or-beats (V1.13c score).

        Unlike :attr:`passed` — the strict CEILING that demands beating EVERY niche —
        this is a CONTINUOUS ratchet signal: a generation that clears 3 of 4 niches
        is visibly close to the curated enterprise bar. It powers the non-blocking
        quality-card advisory while the gate is OFF.
        """
        return sum(1 for c in self.comparisons if c.passed)

    def advisory_card(self) -> dict[str, object]:
        """A NON-blocking quality-card score — never a ship-block (V1.13c).

        The reference gate is OFF on the live path until the owner corpus-run flips
        it (the falsifiable milestone — ``scripts/reference_flip_milestone.py``).
        Until then the corpus still yields a useful continuous signal: how close
        THIS generation came to the ceiling. This card carries that signal with
        ``blocking=False`` so a worker can surface "reference: X/N niches met"
        as advice without the gate ever sinking ship (R-10). An abstain (empty
        corpus or render miss) carries no number — no evidence is not a low score.
        """
        total = len(self.comparisons)
        met = self.niches_met
        usable = self.rendered and total > 0
        return {
            "signal": "reference",
            "blocking": False,
            "rendered": self.rendered,
            "met": met,
            "total": total,
            "summary": (
                f"reference: {met}/{total} niches met"
                if usable
                else "reference: advisory unavailable (abstain)"
            ),
        }


async def audit_files(
    files: dict[str, str],
    *,
    width: int = taste_gate.GATE_WIDTH,
    corpus_dir: Path = CORPUS_DIR,
    min_axes: int = MIN_AXES,
    enforce_score: bool = False,
    tolerance: int = 0,
) -> ReferenceReport:
    """Render a static ``{path: html}`` candidate and grade it against the corpus."""
    cand_vec, cand_rendered, cand_score = await vector_of_files(files, width=width)
    comps = await _compare_vector_to_corpus(
        cand_vec, cand_rendered, corpus_dir=corpus_dir, min_axes=min_axes, width=width,
        cand_score=cand_score, enforce_score=enforce_score, tolerance=tolerance,
    )
    return ReferenceReport(tuple(comps))


async def audit_url(
    url: str,
    *,
    width: int = taste_gate.GATE_WIDTH,
    corpus_dir: Path = CORPUS_DIR,
    min_axes: int = MIN_AXES,
    enforce_score: bool = False,
    tolerance: int = 0,
) -> ReferenceReport:
    """Render a live ``url`` candidate and grade it against the corpus."""
    cand_vec, cand_rendered, cand_score = await vector_of_url(url, width=width)
    comps = await _compare_vector_to_corpus(
        cand_vec, cand_rendered, corpus_dir=corpus_dir, min_axes=min_axes, width=width,
        cand_score=cand_score, enforce_score=enforce_score, tolerance=tolerance,
    )
    return ReferenceReport(tuple(comps))
