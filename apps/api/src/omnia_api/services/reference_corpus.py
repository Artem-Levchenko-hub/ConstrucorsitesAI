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

The corpus lives in ``apps/api/tests/fixtures/reference/<niche>.html`` —
hand-curated enterprise / awwwards snapshots, each citing its source in an HTML
comment, append-only, owner-approved. The adversarial ``bootstrap-baseline.html``
(the same fixture the taste gate keeps red) MUST fall below the corpus on these
axes; that is the falsifiable teeth.

Money-free, 0 LLM: hand-curation once + a deterministic compare. Only the final
"a live fresh generation beats the corpus" step needs an owner corpus-run.

V1.13b wires a ``REFERENCE`` leg into ``accept_gauntlet.RENDERED_GATES`` so this
comparator fans out with the other rendered legs instead of orphaning.
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

#: Plan-mandated corpus location: ``apps/api/tests/fixtures/reference``.
#: ``parents``: [0]=services [1]=omnia_api [2]=src [3]=api.
CORPUS_DIR = Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "reference"

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


@dataclass(frozen=True)
class CorpusComparison:
    """One candidate-vs-reference verdict on the five-axis richness vector."""

    niche: str
    candidate: RichnessVector
    reference: RichnessVector
    met: tuple[str, ...]
    rendered: bool
    min_axes: int = MIN_AXES

    @property
    def passed(self) -> bool:
        """A comparison that could not render BOTH pages abstains (not pass)."""
        return self.rendered and len(self.met) >= self.min_axes

    def summary(self) -> str:
        if not self.rendered:
            return f"reference[{self.niche}]: ABSTAIN (a page did not render)"
        verdict = "MEETS-OR-BEATS" if self.passed else "BELOW"
        misses = [a for a in RICHNESS_AXES if a not in self.met]
        tail = "" if not misses else f" — regressions: {', '.join(misses)}"
        return (
            f"reference[{self.niche}]: {verdict} "
            f"({len(self.met)}/{len(RICHNESS_AXES)} axes ≥ reference, "
            f"floor {self.min_axes}){tail}"
        )


async def vector_of_files(
    files: dict[str, str], *, width: int = taste_gate.GATE_WIDTH
) -> tuple[RichnessVector, bool]:
    """Render a static ``{path: html}`` set once per gate and return its
    five-axis richness vector + whether BOTH gates actually rendered.

    Fail-soft: a gate that abstains contributes no passing axes (R-10).
    """
    taste_rep = await taste_gate.audit_files(files, width=width)
    hier_rep = await hierarchy_gate.audit_files(files, width=hierarchy_gate.GATE_WIDTH)
    rendered = taste_rep.rendered and hier_rep.rendered
    taste_checks = taste_rep.subscore()["checks"] if taste_rep.rendered else {}
    hier_checks = hier_rep.subscore()["checks"] if hier_rep.rendered else {}
    return richness_vector(taste_checks, hier_checks), rendered


async def vector_of_html(
    html: str, *, width: int = taste_gate.GATE_WIDTH
) -> tuple[RichnessVector, bool]:
    """Convenience: richness vector of a single HTML document."""
    return await vector_of_files({"index.html": html}, width=width)


def load_corpus(corpus_dir: Path = CORPUS_DIR) -> dict[str, str]:
    """``{niche: html}`` for every ``<niche>.html`` in the corpus (sorted)."""
    if not corpus_dir.is_dir():
        return {}
    return {
        p.stem: p.read_text(encoding="utf-8")
        for p in sorted(corpus_dir.glob("*.html"))
    }


async def compare_to_corpus(
    candidate_html: str,
    *,
    corpus_dir: Path = CORPUS_DIR,
    min_axes: int = MIN_AXES,
    width: int = taste_gate.GATE_WIDTH,
) -> list[CorpusComparison]:
    """Render ``candidate_html`` and every corpus reference, then report a
    meet-or-beat verdict per niche. The candidate is rendered ONCE and compared
    against each reference's own vector.
    """
    corpus = load_corpus(corpus_dir)
    cand_vec, cand_rendered = await vector_of_html(candidate_html, width=width)
    out: list[CorpusComparison] = []
    for niche, ref_html in corpus.items():
        ref_vec, ref_rendered = await vector_of_html(ref_html, width=width)
        met = axes_met_or_beaten(cand_vec, ref_vec)
        out.append(
            CorpusComparison(
                niche=niche,
                candidate=cand_vec,
                reference=ref_vec,
                met=met,
                rendered=cand_rendered and ref_rendered,
                min_axes=min_axes,
            )
        )
    return out
