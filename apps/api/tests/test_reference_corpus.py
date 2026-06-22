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
    BELOW_CLASS,
    MIN_AXES,
    RICHNESS_AXES,
    CorpusComparison,
    ReferenceReport,
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


# ── V1.13c: adversary-pre-proof helper (named regressions, no threshold drift) ─


def test_min_regressions_is_the_exact_mirror_of_min_axes():
    # A candidate is below the ceiling exactly when it loses more axes than the
    # floor tolerates — derived, never hardcoded, so the two can't drift.
    assert rc.MIN_REGRESSIONS == len(RICHNESS_AXES) - MIN_AXES + 1
    assert rc.MIN_REGRESSIONS == 2


def test_adversary_regressions_names_the_lost_axes():
    ref = _vec()
    gen = _vec(asymmetry=False, **{"hero-imagery": False})  # loses 2 axes
    lost = rc.adversary_regressions(gen, ref)
    assert set(lost) == {"hero-imagery", "asymmetry"}
    # Order follows the canonical RICHNESS_AXES, not insertion.
    assert list(lost) == [a for a in RICHNESS_AXES if a in {"hero-imagery", "asymmetry"}]


def test_adversary_regressions_is_the_negation_of_meets_or_beats():
    # The whole proof rests on this equivalence: "regresses on >= MIN_REGRESSIONS
    # axes" must mean exactly the same as "does NOT meet or beat".
    ref = _vec()
    for gen in (
        _vec(),  # holds all → meets
        _vec(asymmetry=False),  # 1 regression → still meets
        _vec(asymmetry=False, **{"hero-imagery": False}),  # 2 → below
        {a: (a == RICHNESS_AXES[0]) for a in RICHNESS_AXES},  # adversary shape
    ):
        below = len(rc.adversary_regressions(gen, ref)) >= rc.MIN_REGRESSIONS
        assert below is (not meets_or_beats(gen, ref)), gen


def test_adversary_holds_floor_when_reference_is_weak():
    # If the reference itself fails an axis, the adversary failing the same axis
    # is NOT a regression (you can't fall below a floor that isn't there).
    weak_ref = _vec(asymmetry=False, **{"hero-imagery": False})
    adversary = {a: (a == RICHNESS_AXES[0]) for a in RICHNESS_AXES}
    lost = rc.adversary_regressions(adversary, weak_ref)
    assert "asymmetry" not in lost and "hero-imagery" not in lost
    assert set(lost) == {"layout-variety", "focal-dominance"}


# ── corpus is curated, sourced and append-only ─────────────────────────────────


def test_corpus_is_the_four_frozen_niches():
    # V1.13c pins the frozen reference set so it can never silently shrink below
    # the four niches the ceiling de-risk was proven against.
    corpus = rc.load_corpus()
    assert {"editorial", "saas", "agency", "ecommerce"} <= set(corpus)


def test_corpus_has_at_least_four_sourced_niches():
    corpus = rc.load_corpus()
    assert len(corpus) >= 4, f"corpus too thin: {sorted(corpus)}"
    for niche, html in corpus.items():
        assert "Reference source:" in html, f"{niche}.html does not cite its source"


def test_corpus_ships_under_src_not_tests():
    # V1.13b carry-forward: the prod image .dockerignores tests/, so the corpus
    # MUST live under src/ (package data) or the runtime gate finds nothing and
    # abstains forever. Pin the shipped location.
    assert rc.CORPUS_DIR.name == "reference_corpus_data"
    parts = rc.CORPUS_DIR.parts
    assert "src" in parts and "tests" not in parts, rc.CORPUS_DIR
    assert rc.CORPUS_DIR.is_dir(), f"corpus dir missing: {rc.CORPUS_DIR}"


# ── V1.13b: the gauntlet adapter (ReferenceReport) ─────────────────────────────
# Gate-report shape so accept_gauntlet can fan the comparator as the REFERENCE
# leg. Deterministic (no chromium): we drive CorpusComparison verdicts directly.


def _comp(niche, *, passed, rendered=True):
    full = {a: True for a in RICHNESS_AXES}
    cand = full if passed else {a: (a == RICHNESS_AXES[0]) for a in RICHNESS_AXES}
    met = axes_met_or_beaten(cand, full)
    return CorpusComparison(
        niche=niche, candidate=cand, reference=full, met=met, rendered=rendered
    )


def test_report_passes_only_when_every_niche_is_met():
    rep = ReferenceReport((_comp("saas", passed=True), _comp("agency", passed=True)))
    assert rep.rendered is True
    assert rep.passed is True
    assert rep.classes == ()


def test_report_below_any_niche_is_a_hard_finding():
    # Strict CEILING: beating one reference but falling below another fails — the
    # candidate must beat the HARDEST reference.
    rep = ReferenceReport((_comp("saas", passed=True), _comp("agency", passed=False)))
    assert rep.passed is False
    assert rep.classes == (BELOW_CLASS,)
    assert "below: agency" in rep.summary()


def test_report_abstains_on_empty_corpus():
    # The carry-forward safety: no corpus → ABSTAIN (rendered False), and abstain
    # carries NO class — it is never a hard finding on the hot path.
    rep = ReferenceReport(())
    assert rep.rendered is False
    assert rep.passed is False
    assert rep.classes == ()
    assert "ABSTAIN" in rep.summary()


def test_report_abstains_on_render_miss():
    rep = ReferenceReport((_comp("saas", passed=True, rendered=False),))
    assert rep.rendered is False
    assert rep.passed is False
    assert rep.classes == ()


def test_subscore_is_machine_readable():
    rep = ReferenceReport((_comp("saas", passed=False),))
    sub = rep.subscore()
    assert sub["gate"] == "reference"
    assert sub["passed"] is False
    assert sub["axes"] == list(RICHNESS_AXES)
    assert sub["comparisons"][0]["niche"] == "saas"


# ── V1.13c: the NON-blocking advisory score (flip-runbook companion) ───────────
# A continuous "X/N niches met" signal that surfaces while the strict gate is OFF.
# It must never read as a ship-block, and an abstain must carry no number.


def test_niches_met_counts_passing_comparisons():
    rep = ReferenceReport(
        (
            _comp("saas", passed=True),
            _comp("agency", passed=False),
            _comp("editorial", passed=True),
        )
    )
    assert rep.niches_met == 2  # passed: saas + editorial
    assert rep.passed is False  # strict ceiling still fails on agency


def test_advisory_card_is_never_blocking():
    rep = ReferenceReport((_comp("saas", passed=True), _comp("agency", passed=False)))
    card = rep.advisory_card()
    assert card["blocking"] is False
    assert card["met"] == 1
    assert card["total"] == 2
    assert card["summary"] == "reference: 1/2 niches met"


def test_advisory_card_full_clear_reports_all_met():
    rep = ReferenceReport((_comp("saas", passed=True), _comp("agency", passed=True)))
    card = rep.advisory_card()
    assert card["met"] == 2 and card["total"] == 2
    assert card["summary"] == "reference: 2/2 niches met"


def test_advisory_card_abstains_with_no_number():
    # Empty corpus → abstain: no evidence is NOT a zero score (it would read as a
    # failing generation when it is really an un-measured one).
    empty = ReferenceReport(())
    card = empty.advisory_card()
    assert card["rendered"] is False
    assert card["summary"] == "reference: advisory unavailable (abstain)"
    # A render miss is the same: rendered False ⇒ advisory unavailable.
    miss = ReferenceReport((_comp("saas", passed=True, rendered=False),))
    assert miss.advisory_card()["summary"] == "reference: advisory unavailable (abstain)"


def test_audit_files_abstains_when_corpus_empty(monkeypatch):
    # End-to-end through audit_files with a stubbed-empty corpus: the candidate
    # renders fine but there is nothing to compare against → abstain, no class.
    async def _vec(files, **kw):
        return ({a: True for a in RICHNESS_AXES}, True, rc.MAX_RICHNESS_SCORE)

    monkeypatch.setattr(rc, "vector_of_files", _vec)
    monkeypatch.setattr(rc, "load_corpus", lambda corpus_dir=rc.CORPUS_DIR: {})
    rep = asyncio.run(rc.audit_files({"index.html": "<html></html>"}))
    assert rep.rendered is False
    assert rep.classes == ()


def test_audit_files_below_corpus_carries_class(monkeypatch):
    # A thin candidate vs a full corpus reference → below → reference-below class.
    thin = {a: (a == RICHNESS_AXES[0]) for a in RICHNESS_AXES}
    full = {a: True for a in RICHNESS_AXES}

    async def _cand(files, **kw):
        return (thin, True, 1)

    async def _ref(html, **kw):
        return (full, True, rc.MAX_RICHNESS_SCORE)

    monkeypatch.setattr(rc, "vector_of_files", _cand)
    monkeypatch.setattr(rc, "vector_of_html", _ref)
    monkeypatch.setattr(
        rc, "load_corpus", lambda corpus_dir=rc.CORPUS_DIR: {"saas": "<html></html>"}
    )
    rep = asyncio.run(rc.audit_files({"index.html": "<html></html>"}))
    assert rep.rendered is True
    assert rep.passed is False
    assert rep.classes == (BELOW_CLASS,)


# ── render teeth (abstain without chromium, real teeth in the container) ───────


def _render_vec(html: str):
    vec, rendered, _score = asyncio.run(rc.vector_of_html(html))
    return vec, rendered


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


def test_bootstrap_baseline_regresses_at_least_two_axes_per_niche():
    """V1.13c adversary-pre-proof — the explicit, named-axis teeth.

    ``test_bootstrap_baseline_falls_below_the_corpus`` proves the baseline does
    not *meet or beat* the corpus; this proves the stronger, falsifiable claim the
    paid-flip de-risk needs: the baseline REGRESSES on at least ``MIN_REGRESSIONS``
    of the five richness axes against EVERY frozen niche, naming which axes. If a
    future change lets the baseline hold the floor on any niche, the ceiling would
    certify mediocrity and flipping ``reference_gate`` would be unsafe — fail loud.
    """
    base_html = _BASELINE.read_text(encoding="utf-8")
    adv_vec, adv_rendered = _render_vec(base_html)
    if not adv_rendered:
        pytest.skip("no chromium available — verified in prod-worker container")
    corpus = rc.load_corpus()
    assert corpus, "no reference corpus found"
    proven = 0
    for niche, ref_html in corpus.items():
        ref_vec, ref_rendered = _render_vec(ref_html)
        if not ref_rendered:
            continue
        regressed = rc.adversary_regressions(adv_vec, ref_vec)
        proven += 1
        assert len(regressed) >= rc.MIN_REGRESSIONS, (
            f"baseline must regress on >= {rc.MIN_REGRESSIONS} axes vs {niche}, "
            f"but only lost {len(regressed)}: {list(regressed)} "
            f"(ref={ref_vec}, adversary={adv_vec})"
        )
    # The render path actually exercised the proof on every rendered niche — a
    # silent all-abstain must not read as green.
    assert proven >= 1, "no reference niche rendered — proof did not run"


# ── V1.13d ceiling ratchet (continuous score floor) ───────────────────────


class _FakeGateRep:
    """Stand-in for a TasteReport/HierarchyReport with the .score/.rendered the
    ceiling reads."""

    def __init__(self, score: int, rendered: bool = True) -> None:
        self.score = score
        self.rendered = rendered


def test_richness_score_sums_rendered_gate_scores():
    assert rc.richness_score(_FakeGateRep(4), _FakeGateRep(2)) == 6
    # An abstained (not rendered) gate contributes 0 — never inflates the ceiling.
    assert rc.richness_score(_FakeGateRep(5, rendered=False), _FakeGateRep(3)) == 3
    assert rc.richness_score(None, None) == 0


def test_score_floor_held_within_tolerance():
    full = {a: True for a in RICHNESS_AXES}
    met = axes_met_or_beaten(full, full)
    within = CorpusComparison(
        niche="saas", candidate=full, reference=full, met=met, rendered=True,
        candidate_score=6, reference_score=7, enforce_score=True, tolerance=1,
    )
    assert within.passed is True  # 6 >= 7 - 1
    strict = CorpusComparison(
        niche="saas", candidate=full, reference=full, met=met, rendered=True,
        candidate_score=6, reference_score=7, enforce_score=True, tolerance=0,
    )
    assert strict.passed is False  # 6 < 7


def test_score_floor_ignored_when_enforce_off():
    """Back-compat guard: enforce_score=False ⇒ pure boolean floor (V1.13b)."""
    full = {a: True for a in RICHNESS_AXES}
    met = axes_met_or_beaten(full, full)
    comp = CorpusComparison(
        niche="saas", candidate=full, reference=full, met=met, rendered=True,
        candidate_score=3, reference_score=8, enforce_score=False,
    )
    assert comp.passed is True  # low score ignored when not enforcing


def test_boolean_floor_still_required_under_score_enforce():
    """A high score never bypasses the boolean axis floor."""
    full = {a: True for a in RICHNESS_AXES}
    thin = {a: (a == RICHNESS_AXES[0]) for a in RICHNESS_AXES}
    met = axes_met_or_beaten(thin, full)  # only 1 axis held
    comp = CorpusComparison(
        niche="saas", candidate=thin, reference=full, met=met, rendered=True,
        candidate_score=8, reference_score=8, enforce_score=True, tolerance=0,
    )
    assert comp.passed is False  # 1/5 axes < MIN_AXES even at a perfect score


def test_audit_files_ceiling_blocks_low_score_candidate(monkeypatch):
    """A boolean-passing candidate that nonetheless under-scores the reference is
    BELOW the ceiling when enforce_score is on — and PASSES when it is off."""
    full = {a: True for a in RICHNESS_AXES}

    async def _cand(files, **kw):
        return (full, True, 5)

    async def _ref(html, **kw):
        return (full, True, 8)

    monkeypatch.setattr(rc, "vector_of_files", _cand)
    monkeypatch.setattr(rc, "vector_of_html", _ref)
    monkeypatch.setattr(
        rc, "load_corpus", lambda corpus_dir=rc.CORPUS_DIR: {"saas": "<html></html>"}
    )
    blocked = asyncio.run(
        rc.audit_files({"index.html": "<html></html>"}, enforce_score=True, tolerance=1)
    )
    assert blocked.passed is False
    assert blocked.classes == (BELOW_CLASS,)
    # Same candidate, ceiling OFF (default) → passes (dark default is safe).
    passes = asyncio.run(rc.audit_files({"index.html": "<html></html>"}))
    assert passes.passed is True


def test_ceiling_abstains_when_reference_not_credible(monkeypatch):
    """A thin/flaky reference render is no credible ceiling — that pair ABSTAINS
    under enforce_score instead of failing the candidate against noise (R-10)."""
    full = {a: True for a in RICHNESS_AXES}
    thin_ref = {a: (a == RICHNESS_AXES[0]) for a in RICHNESS_AXES}  # only 1 axis

    async def _cand(files, **kw):
        return (full, True, 8)

    async def _ref(html, **kw):
        return (thin_ref, True, 1)

    monkeypatch.setattr(rc, "vector_of_files", _cand)
    monkeypatch.setattr(rc, "vector_of_html", _ref)
    monkeypatch.setattr(
        rc, "load_corpus", lambda corpus_dir=rc.CORPUS_DIR: {"saas": "<html></html>"}
    )
    rep = asyncio.run(
        rc.audit_files({"index.html": "<html></html>"}, enforce_score=True, tolerance=0)
    )
    assert rep.rendered is False  # the only pair abstained
    assert rep.classes == ()
