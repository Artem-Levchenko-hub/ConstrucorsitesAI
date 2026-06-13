"""Hierarchy-richness gate — composition teeth (V1.6 9/5).

"JS extracts, Python scores", so the whole rubric is exercised here with
hand-built observation dicts — no browser. Each richness check has a CLEAN case
(it passes) and a RED case (it fires), the known-good composition scores 3/3, and
the same adversarial ``bootstrap-baseline.html`` the taste gate keeps red must
fall below this gate's 2/3 floor too (gradient hero → no dominant visual, twin
3-card grids → generic asymmetry). The one browser-dependent guarantee — that the
committed fixture actually renders below the floor — is a render test that
abstains (skips) when no chromium is present and runs for real in the prod-worker
container, exactly like the other rendered gates.
"""

import asyncio
from pathlib import Path

import pytest

from omnia_api.services import hierarchy_gate as g
from omnia_api.services.hierarchy_gate import (
    ASYMMETRY,
    CHECKS,
    FOCAL_DOMINANCE,
    MIN_SCORE,
    TYPE_DOMINANCE,
    evaluate_observation,
)


def _txt(size=16, top=10):
    """A text-node observation — the shape ``_AUDIT_JS`` emits per text node."""
    return {"size": size, "top": top}


def _visual(frac=0.35, top=0, containsVisual=False):
    """A visual-element observation: above-fold area fraction + wrapper flag."""
    return {"frac": frac, "top": top, "containsVisual": containsVisual}


def _card(left=0, w=420, h=160, top=600):
    """A child rect inside a sibling group (the card-row detector's input)."""
    return {"l": left, "w": w, "h": h, "top": top}


def _row(n=3, w=420, h=160, top=600, gap=40):
    """``n`` equal cards laid out in a horizontal row."""
    return [_card(left=i * (w + gap), w=w, h=h, top=top) for i in range(n)]


def _good_obs():
    """A known-good landing: a dominant hero headline over flat body type, ONE
    hero visual covering a third of the fold, and below-fold content with NO
    generic card row. Scores 3/3."""
    return {
        "viewportWidth": 1440,
        "viewportHeight": 900,
        "texts": [
            _txt(size=56, top=160),  # hero headline — the focal type
            _txt(size=18, top=260),
            _txt(size=16, top=320),
            _txt(size=16, top=360),
            _txt(size=14, top=420),
        ],
        "visuals": [_visual(frac=0.34, top=0)],
        "groups": [],
    }


def _bootstrap_obs():
    """The Acme-Co Bootstrap fixture's shape: a real type step (40px over 16px →
    type-dominance passes), a GRADIENT hero (no visual → focal-dominance fails),
    and two rows of three identical cards (asymmetry fails). 1/3 → below floor."""
    return {
        "viewportWidth": 1440,
        "viewportHeight": 900,
        "texts": [
            _txt(size=40, top=180),
            _txt(size=32, top=520),
            _txt(size=18, top=240),
            *[_txt(size=16, top=600 + i * 30) for i in range(8)],
        ],
        "visuals": [],  # gradient hero carries no url() background-image
        "groups": [_row(n=3, top=620), _row(n=3, top=980)],
    }


# ── whole-gate: the good page scores full ─────────────────────────────────────


def test_good_obs_scores_full_and_passes():
    rep = evaluate_observation(_good_obs())
    assert rep.score == len(CHECKS) == 3
    assert rep.findings == ()
    assert rep.passed is True
    assert rep.classes == ()


def test_min_score_is_two_of_three():
    assert MIN_SCORE == 2


# ── 1. type-dominance ─────────────────────────────────────────────────────────


def test_type_dominance_clean_dominant_headline():
    rep = evaluate_observation(_good_obs())
    assert TYPE_DOMINANCE not in rep.classes


def test_type_dominance_red_flat_type():
    obs = _good_obs()
    obs["texts"] = [_txt(size=18, top=160), _txt(size=16, top=260), _txt(size=16, top=320)]
    rep = evaluate_observation(obs)
    assert TYPE_DOMINANCE in rep.classes  # 18/16 = 1.1× < 2.2×


def test_type_dominance_red_single_text_element():
    obs = _good_obs()
    obs["texts"] = [_txt(size=40, top=160)]
    rep = evaluate_observation(obs)
    assert TYPE_DOMINANCE in rep.classes  # no measurable hierarchy


def test_type_dominance_red_no_text():
    obs = _good_obs()
    obs["texts"] = []
    rep = evaluate_observation(obs)
    assert TYPE_DOMINANCE in rep.classes


def test_type_dominance_uses_whole_page_median_not_above_fold():
    # body type repeats far down the page; the median stays at the body size and
    # the hero still towers over it (this is the whole-page reading, stricter than
    # taste's above-fold median).
    obs = _good_obs()
    obs["texts"] = [_txt(size=56, top=160)] + [_txt(size=16, top=2000 + i) for i in range(20)]
    rep = evaluate_observation(obs)
    assert TYPE_DOMINANCE not in rep.classes  # 56/16 = 3.5×


def test_type_dominance_boundary_just_below_ratio():
    obs = _good_obs()
    obs["texts"] = [_txt(size=20, top=160), _txt(size=10, top=260), _txt(size=10, top=320)]
    rep = evaluate_observation(obs)
    # median 10, max 20 → exactly 2.0× < 2.2× → fires
    assert TYPE_DOMINANCE in rep.classes


def test_type_dominance_boundary_just_above_ratio():
    obs = _good_obs()
    obs["texts"] = [_txt(size=23, top=160), _txt(size=10, top=260), _txt(size=10, top=320)]
    rep = evaluate_observation(obs)
    assert TYPE_DOMINANCE not in rep.classes  # 2.3× ≥ 2.2×


# ── 2. focal-dominance ────────────────────────────────────────────────────────


def test_focal_dominance_clean_single_visual():
    rep = evaluate_observation(_good_obs())
    assert FOCAL_DOMINANCE not in rep.classes


def test_focal_dominance_red_no_visual():
    obs = _good_obs()
    obs["visuals"] = []
    rep = evaluate_observation(obs)
    assert FOCAL_DOMINANCE in rep.classes


def test_focal_dominance_red_visual_too_small():
    obs = _good_obs()
    obs["visuals"] = [_visual(frac=0.10)]  # a thumbnail, below the 25% floor
    rep = evaluate_observation(obs)
    assert FOCAL_DOMINANCE in rep.classes


def test_focal_dominance_red_two_competing_visuals():
    obs = _good_obs()
    obs["visuals"] = [_visual(frac=0.30, top=0), _visual(frac=0.28, top=0)]
    rep = evaluate_observation(obs)
    assert FOCAL_DOMINANCE in rep.classes  # no single anchor


def test_focal_dominance_excludes_wrapper_keeps_leaf():
    # a full-bleed <section> (wrapper) around the hero <img>: only the leaf counts,
    # so there is exactly one dominant — passes.
    obs = _good_obs()
    obs["visuals"] = [
        _visual(frac=0.70, top=0, containsVisual=True),  # the wrapping section
        _visual(frac=0.34, top=0, containsVisual=False),  # the hero image leaf
    ]
    rep = evaluate_observation(obs)
    assert FOCAL_DOMINANCE not in rep.classes


def test_focal_dominance_fullbleed_bg_hero_counts():
    # a full-bleed background-image hero with no inner <img>: it is its own focal.
    obs = _good_obs()
    obs["visuals"] = [_visual(frac=0.78, top=0, containsVisual=False)]
    rep = evaluate_observation(obs)
    assert FOCAL_DOMINANCE not in rep.classes


def test_focal_dominance_small_decorative_visual_ignored():
    # a 34% hero plus a 3% decorative icon → still exactly one dominant.
    obs = _good_obs()
    obs["visuals"] = [_visual(frac=0.34, top=0), _visual(frac=0.03, top=10)]
    rep = evaluate_observation(obs)
    assert FOCAL_DOMINANCE not in rep.classes


def test_focal_dominance_boundary_at_25_percent():
    obs = _good_obs()
    obs["visuals"] = [_visual(frac=0.25, top=0)]
    rep = evaluate_observation(obs)
    assert FOCAL_DOMINANCE not in rep.classes  # ≥ 25% inclusive


# ── 3. asymmetry (generic card row) ───────────────────────────────────────────


def test_asymmetry_clean_no_card_row():
    rep = evaluate_observation(_good_obs())
    assert ASYMMETRY not in rep.classes


def test_asymmetry_red_three_equal_cards():
    obs = _good_obs()
    obs["groups"] = [_row(n=3)]
    rep = evaluate_observation(obs)
    assert ASYMMETRY in rep.classes


def test_asymmetry_red_four_equal_cards():
    obs = _good_obs()
    obs["groups"] = [_row(n=4, w=320)]
    rep = evaluate_observation(obs)
    assert ASYMMETRY in rep.classes


def test_asymmetry_two_cards_do_not_fire():
    obs = _good_obs()
    obs["groups"] = [_row(n=2)]
    rep = evaluate_observation(obs)
    assert ASYMMETRY not in rep.classes  # a 2-up is not the generic 3-card grid


def test_asymmetry_unequal_widths_do_not_fire():
    obs = _good_obs()
    obs["groups"] = [
        [_card(left=0, w=600, h=160), _card(left=640, w=300, h=160), _card(left=960, w=420, h=160)]
    ]
    rep = evaluate_observation(obs)
    assert ASYMMETRY not in rep.classes  # deliberate width variety


def test_asymmetry_unequal_heights_do_not_fire():
    obs = _good_obs()
    obs["groups"] = [
        [_card(left=0, w=420, h=160), _card(left=460, w=420, h=320), _card(left=920, w=420, h=160)]
    ]
    rep = evaluate_observation(obs)
    assert ASYMMETRY not in rep.classes  # masonry-ish, not a flat grid


def test_asymmetry_short_items_do_not_fire():
    # a row of buttons / nav links / tags: too short to be content cards.
    obs = _good_obs()
    obs["groups"] = [_row(n=4, w=120, h=36)]
    rep = evaluate_observation(obs)
    assert ASYMMETRY not in rep.classes


def test_asymmetry_full_width_stacked_rows_do_not_fire():
    # three stacked full-width bands (each ~95% viewport) are not a card row.
    obs = _good_obs()
    obs["groups"] = [[_card(left=0, w=1370, h=200, top=600 + i * 220) for i in range(3)]]
    rep = evaluate_observation(obs)
    assert ASYMMETRY not in rep.classes


def test_asymmetry_different_rows_do_not_merge():
    # three cards split across two different baselines (a 2-up + a 1) → no row of 3.
    obs = _good_obs()
    obs["groups"] = [
        [_card(left=0, w=420, h=160, top=600), _card(left=460, w=420, h=160, top=600),
         _card(left=0, w=420, h=160, top=900)]
    ]
    rep = evaluate_observation(obs)
    assert ASYMMETRY not in rep.classes


def test_asymmetry_detail_reports_count_and_width():
    obs = _good_obs()
    obs["groups"] = [_row(n=3, w=420)]
    rep = evaluate_observation(obs)
    finding = next(f for f in rep.findings if f.check == ASYMMETRY)
    assert "3-card" in finding.detail


# ── whole-gate: floor behaviour ───────────────────────────────────────────────


def test_bootstrap_obs_falls_below_floor():
    rep = evaluate_observation(_bootstrap_obs())
    assert rep.score == 1  # type-dominance passes; focal + asymmetry fail
    assert rep.passed is False
    assert FOCAL_DOMINANCE in rep.classes
    assert ASYMMETRY in rep.classes
    assert TYPE_DOMINANCE not in rep.classes


def test_good_page_with_card_grid_still_passes():
    # the calibration-safety contract: a real focal hierarchy + a card grid is
    # 2/3 (asymmetry fires alone) → PASS. A card row must never sink a good page.
    obs = _good_obs()
    obs["groups"] = [_row(n=3)]
    rep = evaluate_observation(obs)
    assert ASYMMETRY in rep.classes
    assert rep.score == 2
    assert rep.passed is True


def test_two_failures_drop_below_floor():
    obs = _good_obs()
    obs["visuals"] = []  # focal fails
    obs["groups"] = [_row(n=3)]  # asymmetry fails
    rep = evaluate_observation(obs)
    assert rep.score == 1
    assert rep.passed is False


def test_one_failure_stays_at_floor():
    obs = _good_obs()
    obs["visuals"] = []  # only focal fails
    rep = evaluate_observation(obs)
    assert rep.score == 2
    assert rep.passed is True


# ── report surface ────────────────────────────────────────────────────────────


def test_classes_are_in_canonical_order():
    obs = _good_obs()
    obs["texts"] = [_txt(size=16, top=10), _txt(size=16, top=20)]  # type fails
    obs["visuals"] = []  # focal fails
    obs["groups"] = [_row(n=3)]  # asymmetry fails
    rep = evaluate_observation(obs)
    assert rep.classes == (TYPE_DOMINANCE, FOCAL_DOMINANCE, ASYMMETRY)


def test_subscore_is_machine_readable():
    sub = evaluate_observation(_bootstrap_obs()).subscore()
    assert sub["gate"] == "hierarchy"
    assert sub["passed"] is False
    assert sub["max_score"] == 3
    assert sub["checks"][FOCAL_DOMINANCE] is False
    assert sub["checks"][TYPE_DOMINANCE] is True
    assert set(sub["checks"]) == set(CHECKS)


def test_subscore_detail_carries_counts():
    sub = evaluate_observation(_bootstrap_obs()).subscore()
    assert sub["detail"]["card_row"] is True
    assert sub["detail"]["focal_dominants"] == 0


def test_summary_pass_is_one_line():
    s = evaluate_observation(_good_obs()).summary()
    assert "3/3" in s and "hierarchy" in s


def test_summary_fail_lists_each_miss():
    s = evaluate_observation(_bootstrap_obs()).summary()
    assert FOCAL_DOMINANCE in s and ASYMMETRY in s


def test_abstain_is_not_a_pass():
    rep = evaluate_observation(_good_obs(), rendered=False)
    assert rep.rendered is False
    assert rep.passed is False
    assert rep.score == 0


def test_summary_abstain():
    s = evaluate_observation(_good_obs(), rendered=False).summary()
    assert "ABSTAIN" in s


# ── helper edge cases ─────────────────────────────────────────────────────────


def test_focal_dominants_helper_filters_wrappers_and_small():
    obs = {
        "visuals": [
            _visual(frac=0.70, containsVisual=True),  # wrapper → out
            _visual(frac=0.40, containsVisual=False),  # leaf → in
            _visual(frac=0.10, containsVisual=False),  # too small → out
        ]
    }
    assert len(g._focal_dominants(obs)) == 1


def test_card_row_helper_returns_none_without_three():
    assert g._card_row(_row(n=2), 1440) is None


def test_find_card_row_helper_scans_all_groups():
    obs = {"viewportWidth": 1440, "groups": [_row(n=2), _row(n=3, w=300)]}
    hit = g._find_card_row(obs)
    assert hit is not None and hit[0] == 3


# ── the one browser-dependent guarantee: the committed fixture renders red ─────

_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "bootstrap-baseline.html"


def test_bootstrap_fixture_renders_below_the_floor():
    """Render the committed adversarial fixture for real. Abstains (skips) when no
    chromium is available locally; runs with teeth in the prod-worker container."""
    html = _FIXTURE.read_text(encoding="utf-8")
    rep = asyncio.run(g.audit_files({"index.html": html}))
    if not rep.rendered:
        pytest.skip("no chromium available — verified in prod-worker container")
    assert rep.passed is False, f"adversarial fixture must fail hierarchy: {rep.summary()}"
    assert FOCAL_DOMINANCE in rep.classes
    assert ASYMMETRY in rep.classes
