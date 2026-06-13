"""V1.13a — reference-corpus comparator (pillar-1 ceiling ratchet).

Two layers, exactly like the other rendered gates:

* PURE-PYTHON teeth (no browser, run everywhere): the projection +
  meet-or-beat logic, and the falsifiable shape — a candidate that holds the
  whole vector passes, one regression still passes (4/5), two regressions fail
  (3/5), and the adversary shape (only type-scale, like the bootstrap baseline)
  falls below a full reference.
* RENDER teeth (abstain/skip when no chromium, real teeth in the prod-worker
  container): every curated corpus fixture genuinely scores high on its own
  axes, and the committed ``bootstrap-baseline.html`` does NOT meet or beat the
  corpus. If a future change lets the baseline beat the corpus, the ceiling has
  started certifying mediocrity — keep it below.
"""

import asyncio
from pathlib import Path

import pytest

from omnia_api.services import reference_corpus as rc
from omnia_api.services.reference_corpus import (
    MIN_AXES,
    RICHNESS_AXES,
    axes_met_or_beaten,
    meets_or_beats,
    richness_vector,
)

_FIXTURES = Path(__file__).resolve().parent / "fixtures"
_BASELINE = _FIXTURES / "bootstrap-baseline.html"

# The five axes, by gate of origin.
_TASTE = ("type-scale", "layout-variety", "hero-imagery")
_HIER = ("focal-dominance", "asymmetry")


def _full_taste():
    return {a: True for a in _TASTE}


def _full_hier():
    return {a: True for a in _HIER}


# ── projection: reuse the gates' own per-axis verdicts, no new vector ──────────


def test_richness_vector_covers_exactly_the_five_axes():
    vec = richness_vector(_full_taste(), _full_hier())
    assert tuple(vec) == RICHNESS_AXES
    assert len(RICHNESS_AXES) == 5
    assert all(vec.values())


def test_richness_vector_missing_axis_reads_false():
    # An abstaining gate (empty checks) must never inflate the vector.
    vec = richness_vector({}, {})
    assert vec == {a: False for a in RICHNESS_AXES}


def test_richness_vector_merges_both_gates():
    vec = richness_vector(
        {"type-scale": True, "layout-variety": False, "hero-imagery": True},
        {"focal-dominance": True, "asymmetry": False},
    )
    assert vec == {
        "type-scale": True,
        "layout-variety": False,
        "hero-imagery": True,
        "focal-dominance": True,
        "asymmetry": False,
    }


# ── meet-or-beat: the comparative claim ────────────────────────────────────────


def _vec(**overrides):
    base = {a: True for a in RICHNESS_AXES}
    base.update(overrides)
    return base


def test_equal_vectors_meet_or_beat():
    ref = _vec()
    assert meets_or_beats(ref, ref) is True
    assert len(axes_met_or_beaten(ref, ref)) == 5


def test_one_regression_still_meets_floor():
    ref = _vec()
    gen = _vec(asymmetry=False)  # 4/5 axes still hold
    assert len(axes_met_or_beaten(gen, ref)) == 4
    assert meets_or_beats(gen, ref) is True


def test_two_regressions_fall_below_floor():
    ref = _vec()
    gen = _vec(asymmetry=False, **{"hero-imagery": False})  # 3/5
    assert len(axes_met_or_beaten(gen, ref)) == 3
    assert meets_or_beats(gen, ref) is False


def test_candidate_may_exceed_a_weak_reference():
    # Beating the reference on an axis it failed still holds the axis.
    ref = _vec(asymmetry=False)
    gen = _vec()
    assert meets_or_beats(gen, ref) is True
    assert len(axes_met_or_beaten(gen, ref)) == 5


def test_adversary_shape_falls_below_full_reference():
    # The bootstrap baseline's shape: only type-scale holds; flat hero, monotone
    # layout, 3-equal-card rows, no focal anchor.
    adversary = {
        "type-scale": True,
        "layout-variety": False,
        "hero-imagery": False,
        "focal-dominance": False,
        "asymmetry": False,
    }
    ref = _vec()
    assert len(axes_met_or_beaten(adversary, ref)) == 1
    assert meets_or_beats(adversary, ref) is False


# ── corpus is curated, sourced and append-only ─────────────────────────────────


def test_corpus_has_at_least_four_sourced_niches():
    corpus = rc.load_corpus()
    assert len(corpus) >= 4, f"corpus too thin: {sorted(corpus)}"
    for niche, html in corpus.items():
        assert "Reference source:" in html, f"{niche}.html does not cite its source"


# ── render teeth (abstain without chromium, real teeth in the container) ───────


def _render_vec(html: str):
    return asyncio.run(rc.vector_of_html(html))


def test_each_reference_fixture_is_genuinely_rich():
    """Every curated reference must clear the floor on its OWN axes — a corpus
    entry that cannot itself meet the bar is not a credible ceiling."""
    corpus = rc.load_corpus()
    assert corpus, "no reference corpus found"
    rendered_any = False
    for niche, html in corpus.items():
        vec, rendered = _render_vec(html)
        if not rendered:
            continue
        rendered_any = True
        held = sum(1 for a in RICHNESS_AXES if vec[a])
        assert held >= MIN_AXES, (
            f"reference {niche} only holds {held}/5 of its own richness axes: {vec}"
        )
    if not rendered_any:
        pytest.skip("no chromium available — verified in prod-worker container")


def test_bootstrap_baseline_falls_below_the_corpus():
    """The adversarial baseline must NOT meet or beat the curated corpus. This is
    the falsifiable teeth: keep it below."""
    html = _BASELINE.read_text(encoding="utf-8")
    comparisons = asyncio.run(rc.compare_to_corpus(html))
    assert comparisons, "no reference corpus found"
    if not any(c.rendered for c in comparisons):
        pytest.skip("no chromium available — verified in prod-worker container")
    for c in comparisons:
        if not c.rendered:
            continue
        assert not c.passed, (
            f"bootstrap baseline must fall below the corpus, but beat {c.niche}: "
            f"{c.summary()}"
        )
