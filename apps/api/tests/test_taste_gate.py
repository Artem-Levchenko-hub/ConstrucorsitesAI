"""Objective taste-floor gate — the richness rubric (V1.6 7/5).

"JS extracts, Python scores", so the whole rubric is exercised here with
hand-built observation dicts — no browser. Each richness check has a CLEAN case
(it passes) and a RED case (it fires), and the gate as a whole has a known-good
page that scores 5/5 and an adversarial generic page that must fall below the
4/5 floor. The one truly browser-dependent guarantee — that the committed
``bootstrap-baseline.html`` actually renders below the floor — is a render test
that abstains (skips) when no chromium is present and runs for real in the
prod-worker container, exactly like the other rendered gates.
"""

import asyncio
from pathlib import Path

import pytest

from omnia_api.services import taste_gate as g
from omnia_api.services.taste_gate import (
    CHECKS,
    FONT_PAIRING,
    HERO_IMAGERY,
    HIERARCHY,
    LAYOUT_VARIETY,
    MIN_SCORE,
    TYPE_SCALE,
    evaluate_observation,
    normalize_family,
)

DISPLAY = '"Playfair Display", Georgia, serif'
BODY = "Inter, system-ui, sans-serif"


def _txt(family=BODY, size=16, weight=400, top=10, sample="x"):
    """A text-node observation, the shape ``_AUDIT_JS`` emits per text node."""
    return {"family": family, "size": size, "weight": weight, "top": top, "sample": sample}


def _sec(width=1140, height=400, top=0, hasImage=False):
    """A section observation: rect + whether it carries a real image."""
    return {"width": width, "height": height, "top": top, "hasImage": hasImage}


def _good_obs():
    """A known-good landing page: paired fonts, real scale, dominant hero with an
    image, a full-bleed band + a contained band. Scores 5/5."""
    return {
        "viewportWidth": 1440,
        "viewportHeight": 900,
        "texts": [
            _txt(family=DISPLAY, size=48, weight=700, top=120, sample="Headline"),
            _txt(family=BODY, size=20, top=220, sample="lead"),
            _txt(family=BODY, size=16, top=300, sample="body"),
            _txt(family=BODY, size=16, top=340, sample="body"),
            _txt(family=BODY, size=14, top=420, sample="small"),
        ],
        "sections": [
            _sec(width=1440, height=620, top=0, hasImage=True),
            _sec(width=1140, height=480, top=620, hasImage=False),
        ],
    }


# ── normalize_family ──────────────────────────────────────────────────────────


def test_normalize_family_takes_first_token_lowercased():
    assert normalize_family(DISPLAY) == "playfair display"


def test_normalize_family_strips_quotes_and_whitespace():
    assert normalize_family("  'Some Font' , serif") == "some font"


def test_normalize_family_empty_is_blank():
    assert normalize_family("") == ""
    assert normalize_family(None) == ""


# ── whole-gate: the good page scores full ─────────────────────────────────────


def test_good_obs_scores_full_and_passes():
    rep = evaluate_observation(_good_obs())
    assert rep.score == len(CHECKS) == 5
    assert rep.findings == ()
    assert rep.passed is True
    assert rep.classes == ()


def test_fonts_are_surfaced_in_report():
    rep = evaluate_observation(_good_obs())
    assert rep.fonts == ("playfair display", "inter")


# ── 1. font-pairing ───────────────────────────────────────────────────────────


def test_font_pairing_red_single_family():
    obs = _good_obs()
    for t in obs["texts"]:
        t["family"] = BODY
    rep = evaluate_observation(obs)
    assert FONT_PAIRING in rep.classes
    assert rep.score == 4  # only this check drops


def test_font_pairing_clean_two_families():
    rep = evaluate_observation(_good_obs())
    assert FONT_PAIRING not in rep.classes


# ── 2. type-scale ─────────────────────────────────────────────────────────────


def test_type_scale_red_too_few_distinct_sizes():
    obs = _good_obs()
    obs["texts"] = [
        _txt(family=DISPLAY, size=40, top=120),
        _txt(size=16, top=300),
        _txt(size=16, top=340),
    ]
    rep = evaluate_observation(obs)
    assert TYPE_SCALE in rep.classes  # only 2 distinct sizes < 3


def test_type_scale_red_ratio_too_even():
    obs = _good_obs()
    obs["texts"] = [
        _txt(family=DISPLAY, size=20, top=120),
        _txt(size=18, top=300),
        _txt(size=16, top=340),
    ]
    rep = evaluate_observation(obs)
    assert TYPE_SCALE in rep.classes  # 20/16 = 1.25× < 2×


def test_type_scale_clean():
    rep = evaluate_observation(_good_obs())
    assert TYPE_SCALE not in rep.classes


# ── 3. hierarchy ──────────────────────────────────────────────────────────────


def test_hierarchy_red_no_dominant_focal():
    obs = _good_obs()
    # 4 distinct sizes (type-scale still passes) but the largest is only
    # 40/median(24)=1.67× — no dominant focal element.
    obs["texts"] = [
        _txt(family=DISPLAY, size=40, top=120),
        _txt(size=28, top=200),
        _txt(size=20, top=280),
        _txt(size=16, top=360),
    ]
    rep = evaluate_observation(obs)
    assert HIERARCHY in rep.classes
    assert TYPE_SCALE not in rep.classes


def test_hierarchy_red_competing_equal_headings():
    obs = _good_obs()
    # three equal 40px headings tower over the body: dominance is there, but no
    # SINGLE focal point (top tier of 3 > 2).
    obs["texts"] = [
        _txt(family=DISPLAY, size=40, top=120),
        _txt(family=DISPLAY, size=40, top=200),
        _txt(family=DISPLAY, size=40, top=280),
        _txt(size=24, top=360),
        _txt(size=16, top=420),
        _txt(size=16, top=460),
        _txt(size=16, top=500),
        _txt(size=16, top=540),
        _txt(size=16, top=580),
    ]
    rep = evaluate_observation(obs)
    assert HIERARCHY in rep.classes
    assert any("compete" in f.detail for f in rep.findings)


def test_hierarchy_red_insufficient_text():
    obs = _good_obs()
    obs["texts"] = [_txt(size=40, top=120)]
    rep = evaluate_observation(obs)
    assert HIERARCHY in rep.classes


def test_hierarchy_clean():
    rep = evaluate_observation(_good_obs())
    assert HIERARCHY not in rep.classes


# ── 4. layout-variety ─────────────────────────────────────────────────────────


def test_layout_variety_red_monotone_widths():
    obs = _good_obs()
    obs["sections"] = [
        _sec(width=1140, height=600, top=0, hasImage=True),
        _sec(width=1140, height=400, top=600, hasImage=False),
        _sec(width=1140, height=400, top=1000, hasImage=False),
    ]
    rep = evaluate_observation(obs)
    assert LAYOUT_VARIETY in rep.classes
    assert HERO_IMAGERY not in rep.classes  # hero still has its image


def test_layout_variety_red_single_section():
    obs = _good_obs()
    obs["sections"] = [_sec(width=1140, height=600, top=0, hasImage=True)]
    rep = evaluate_observation(obs)
    assert LAYOUT_VARIETY in rep.classes


def test_layout_variety_clean():
    rep = evaluate_observation(_good_obs())
    assert LAYOUT_VARIETY not in rep.classes


# ── 5. hero-imagery ───────────────────────────────────────────────────────────


def test_hero_imagery_red_solid_plate():
    obs = _good_obs()
    obs["sections"] = [
        _sec(width=1440, height=620, top=0, hasImage=False),
        _sec(width=1140, height=480, top=620, hasImage=False),
    ]
    rep = evaluate_observation(obs)
    assert HERO_IMAGERY in rep.classes
    assert LAYOUT_VARIETY not in rep.classes  # widths still vary


def test_hero_imagery_clean_with_image():
    rep = evaluate_observation(_good_obs())
    assert HERO_IMAGERY not in rep.classes


def test_hero_imagery_red_no_section_at_all():
    obs = _good_obs()
    obs["sections"] = []
    rep = evaluate_observation(obs)
    assert HERO_IMAGERY in rep.classes
    assert LAYOUT_VARIETY in rep.classes


def test_hero_is_the_topmost_section():
    obs = _good_obs()
    # topmost section has no image, a lower one does — the lower image must NOT
    # rescue the hero check.
    obs["sections"] = [
        _sec(width=1440, height=620, top=0, hasImage=False),
        _sec(width=1140, height=480, top=620, hasImage=True),
    ]
    rep = evaluate_observation(obs)
    assert HERO_IMAGERY in rep.classes


def test_strip_hero_image_drops_exactly_one_point():
    good = evaluate_observation(_good_obs())
    obs = _good_obs()
    obs["sections"][0]["hasImage"] = False  # strip the hero image only
    stripped = evaluate_observation(obs)
    assert good.score - stripped.score == 1
    assert stripped.classes == (HERO_IMAGERY,)


# ── adversarial: a generic Bootstrap-grade page must fall below the floor ──────


def _bootstrap_obs():
    """The shape ``bootstrap-baseline.html`` renders to: one system font, a real
    type scale (so type-scale alone won't save it), a solid hero plate and a
    monotone column of equal-width container sections."""
    return {
        "viewportWidth": 1440,
        "viewportHeight": 900,
        "texts": [
            _txt(family="system-ui", size=40, weight=700, top=160, sample="Business solutions"),
            _txt(family="system-ui", size=18, top=240, sample="Everything you need"),
            _txt(family="system-ui", size=16, top=80, sample="Features"),
            _txt(family="system-ui", size=32, weight=700, top=520, sample="Why choose us"),
            _txt(family="system-ui", size=20, top=600, sample="Fast setup"),
            _txt(family="system-ui", size=16, top=640, sample="Get up and running"),
            _txt(family="system-ui", size=14, top=1400, sample="© 2026 Acme Co"),
        ],
        "sections": [
            _sec(width=1440, height=360, top=80, hasImage=False),   # solid hero plate
            _sec(width=1440, height=520, top=440, hasImage=False),  # features band
            _sec(width=1440, height=520, top=960, hasImage=False),  # pricing band
        ],
    }


def test_bootstrap_obs_falls_below_floor():
    rep = evaluate_observation(_bootstrap_obs())
    assert rep.score < MIN_SCORE
    assert rep.passed is False


def test_bootstrap_obs_fails_the_expected_classes():
    rep = evaluate_observation(_bootstrap_obs())
    # the generic tells: one font, no hero imagery, one monotone width.
    assert FONT_PAIRING in rep.classes
    assert HERO_IMAGERY in rep.classes
    assert LAYOUT_VARIETY in rep.classes
    # but it is NOT broken — its type scale is real.
    assert TYPE_SCALE not in rep.classes


# ── report surface: abstain / threshold / subscore / summary ──────────────────


def test_abstain_when_not_rendered():
    rep = evaluate_observation(_good_obs(), rendered=False)
    assert rep.rendered is False
    assert rep.passed is False  # no evidence ≠ pass
    assert rep.score == 0


def test_passed_requires_min_score_boundary():
    obs = _good_obs()
    for t in obs["texts"]:  # break exactly one check (font) → 4/5
        t["family"] = BODY
    rep = evaluate_observation(obs)
    assert rep.score == MIN_SCORE
    assert rep.passed is True


def test_three_misses_fail_the_gate():
    rep = evaluate_observation(_bootstrap_obs())
    assert len(rep.classes) >= 3
    assert rep.passed is False


def test_classes_are_in_canonical_order():
    obs = _good_obs()
    for t in obs["texts"]:
        t["family"] = BODY  # font miss
    obs["sections"] = [_sec(width=1140, height=600, top=0, hasImage=False)]  # layout + hero miss
    rep = evaluate_observation(obs)
    assert list(rep.classes) == [c for c in CHECKS if c in rep.classes]
    assert rep.classes == (FONT_PAIRING, LAYOUT_VARIETY, HERO_IMAGERY)


def test_subscore_is_machine_readable():
    sub = evaluate_observation(_bootstrap_obs()).subscore()
    assert sub["gate"] == "taste"
    assert sub["passed"] is False
    assert sub["max_score"] == 5
    assert sub["checks"][FONT_PAIRING] is False
    assert sub["checks"][TYPE_SCALE] is True
    assert set(sub["checks"]) == set(CHECKS)


def test_summary_pass_is_one_line():
    s = evaluate_observation(_good_obs()).summary()
    assert "5/5" in s and "taste" in s


def test_summary_fail_lists_each_miss():
    s = evaluate_observation(_bootstrap_obs()).summary()
    assert FONT_PAIRING in s and HERO_IMAGERY in s


def test_summary_abstain():
    s = evaluate_observation(_good_obs(), rendered=False).summary()
    assert "ABSTAIN" in s


# ── helper edge cases ─────────────────────────────────────────────────────────


def test_major_sections_skip_short_bands():
    obs = _good_obs()
    obs["sections"] = [
        _sec(width=1440, height=60, top=0, hasImage=True),    # nav: too short
        _sec(width=1440, height=620, top=60, hasImage=True),  # hero
        _sec(width=1140, height=480, top=680, hasImage=False),
    ]
    rep = evaluate_observation(obs)
    assert rep.detail["major_sections"] == 2  # nav excluded


def test_above_fold_excludes_below_the_fold_text():
    obs = _good_obs()
    obs["viewportHeight"] = 500
    obs["texts"] = [
        _txt(family=DISPLAY, size=48, top=100),  # above fold
        _txt(size=16, top=200),                  # above fold
        _txt(size=16, top=300),                  # above fold
        _txt(size=200, top=4000),                # far below fold — must be ignored
    ]
    # the giant below-the-fold size never enters the hierarchy calculation
    assert g._above_fold_sizes(obs) == [48, 16, 16]
    rep = evaluate_observation(obs)
    assert HIERARCHY not in rep.classes  # 48 over a median of 16 is dominant


# ── the one browser-dependent guarantee: the committed fixture renders red ─────

_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "bootstrap-baseline.html"


def test_bootstrap_fixture_renders_below_the_floor():
    """Render the committed adversarial fixture for real. Abstains (skips) when no
    chromium is available locally; runs with teeth in the prod-worker container."""
    html = _FIXTURE.read_text(encoding="utf-8")
    rep = asyncio.run(g.audit_files({"index.html": html}))
    if not rep.rendered:
        pytest.skip("no chromium available — verified in prod-worker container")
    assert rep.passed is False, f"adversarial fixture must fail taste: {rep.summary()}"
    assert FONT_PAIRING in rep.classes
    assert HERO_IMAGERY in rep.classes
