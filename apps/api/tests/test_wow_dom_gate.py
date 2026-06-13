"""Machine WOW-DOM gate — the objective rubric half (V1.6 2/5).

The gate's design is "JS extracts, Python scores", so the entire rubric is
exercised here with hand-built observation dicts — no browser. Each check has a
CLEAN case (0 findings) and a RED case (exactly that check fires), mirroring the
defect-registry's falsifiable contract: the floor only goes up.
"""

import asyncio
import math
from pathlib import Path

import pytest

from omnia_api.services import wow_dom_gate as g
from omnia_api.services.wow_dom_gate import (
    composite,
    contrast_ratio,
    evaluate_observation,
    is_dead_href,
    is_large_text,
    relative_luminance,
    rgb_to_hsl,
)

WHITE = (255.0, 255.0, 255.0)
BLACK = (0.0, 0.0, 0.0)


def _txt(color, bg, size=16, weight=400, sample="x"):
    """A text-node observation, the shape ``_AUDIT_JS`` emits per text node."""
    return {"color": color, "bg": bg, "size": size, "weight": weight, "sample": sample}


# ── colour / typography maths ─────────────────────────────────────────────────


def test_relative_luminance_extremes():
    assert relative_luminance(WHITE) == 1.0
    assert relative_luminance(BLACK) == 0.0


def test_contrast_ratio_black_on_white_is_max():
    assert round(contrast_ratio(BLACK, WHITE), 1) == 21.0


def test_contrast_ratio_same_colour_is_one():
    assert round(contrast_ratio(WHITE, WHITE), 2) == 1.0


def test_composite_full_alpha_returns_fg():
    assert composite((10.0, 20.0, 30.0, 1.0), WHITE) == (10.0, 20.0, 30.0)


def test_composite_zero_alpha_returns_bg():
    assert composite((10.0, 20.0, 30.0, 0.0), BLACK) == (0.0, 0.0, 0.0)


def test_composite_half_alpha_blends():
    r, gr, b = composite((0.0, 0.0, 0.0, 0.5), WHITE)
    assert round(r) == 128 and round(gr) == 128 and round(b) == 128


def test_rgb_to_hsl_pure_red():
    hue, sat, light = rgb_to_hsl((255.0, 0.0, 0.0))
    assert round(hue) == 0 and sat == 1.0 and light == 0.5


def test_rgb_to_hsl_grey_is_unsaturated():
    _, sat, _ = rgb_to_hsl((128.0, 128.0, 128.0))
    assert sat == 0.0


def test_is_large_text():
    assert is_large_text(24.0, 400)
    assert not is_large_text(20.0, 400)
    assert is_large_text(19.0, 700)  # bold ≥18.66
    assert not is_large_text(19.0, 400)


def test_is_dead_href():
    for dead in (None, "", "#", "#!", "javascript:void(0)", " javascript:alert(1)"):
        assert is_dead_href(dead), dead
    for live in ("/", "/signin", "#section", "https://x.io", "mailto:a@b.c"):
        assert not is_dead_href(live), live


# ── observation scoring ───────────────────────────────────────────────────────


def _clean_obs() -> dict:
    """A page that passes every check: fits at 390px, black-on-white text, one
    live 48px CTA, one saturated accent."""
    return {
        "viewportWidth": 390,
        "scrollWidth": 390,
        "texts": [
            _txt([0, 0, 0, 1], [255, 255, 255, 1], sample="hello")
        ],
        "controls": [
            {
                "tag": "a", "href": "/signin", "rectW": 120, "rectH": 48,
                "displayInline": False, "disabled": False, "type": "", "inForm": False,
                "role": "", "visible": True, "text": "Войти",
            }
        ],
        "fills": [{"bg": [99, 102, 241, 1], "tag": "a", "area": 5760}],  # indigo
    }


def test_clean_obs_passes():
    report = evaluate_observation(_clean_obs())
    assert report.passed
    assert report.findings == ()
    assert report.rendered


def test_not_rendered_abstains():
    report = evaluate_observation({"viewportWidth": 390}, rendered=False)
    assert not report.passed
    assert not report.rendered
    assert "ABSTAIN" in report.summary()


# h-scroll
def test_h_scroll_flagged():
    obs = _clean_obs()
    obs["scrollWidth"] = 460
    report = evaluate_observation(obs)
    assert g.H_SCROLL in report.classes


def test_h_scroll_within_tolerance_passes():
    obs = _clean_obs()
    obs["scrollWidth"] = 391  # 1px sub-pixel rounding
    assert g.H_SCROLL not in evaluate_observation(obs).classes


# dead control
def test_dead_anchor_flagged():
    obs = _clean_obs()
    obs["controls"][0]["href"] = "#"
    report = evaluate_observation(obs)
    assert g.DEAD_CONTROL in report.classes


def test_live_anchor_not_flagged():
    assert g.DEAD_CONTROL not in evaluate_observation(_clean_obs()).classes


def test_invisible_dead_anchor_skipped():
    obs = _clean_obs()
    obs["controls"][0]["href"] = "#"
    obs["controls"][0]["visible"] = False
    assert g.DEAD_CONTROL not in evaluate_observation(obs).classes


def test_dead_button_not_flagged_handlers_out_of_scope():
    # A button with no href is NOT a dead-control here (React listeners are
    # delegated; the registry owns handler liveness). Only anchors are judged.
    obs = _clean_obs()
    obs["controls"].append(
        {"tag": "button", "href": None, "rectW": 100, "rectH": 48,
         "displayInline": False, "disabled": False, "type": "button", "inForm": False,
         "role": "", "visible": True, "text": "Click"}
    )
    assert g.DEAD_CONTROL not in evaluate_observation(obs).classes


# low contrast
def test_low_contrast_grey_on_white_flagged():
    obs = _clean_obs()
    obs["texts"][0]["color"] = [200, 200, 200, 1]  # ~1.6:1 on white
    report = evaluate_observation(obs)
    assert g.LOW_CONTRAST in report.classes


def test_high_contrast_passes():
    assert g.LOW_CONTRAST not in evaluate_observation(_clean_obs()).classes


def test_contrast_on_image_bg_skipped():
    obs = _clean_obs()
    obs["texts"][0] = _txt([250, 250, 250, 1], None)
    assert g.LOW_CONTRAST not in evaluate_observation(obs).classes


def test_large_text_uses_relaxed_floor():
    # ~3.5:1 mid-grey on white: FAILS normal 4.5 but PASSES large 3.0.
    grey = [120, 120, 120, 1]
    small = _clean_obs()
    small["texts"][0] = _txt(grey, [255, 255, 255, 1], size=16, sample="s")
    assert g.LOW_CONTRAST in evaluate_observation(small).classes
    large = _clean_obs()
    large["texts"][0] = _txt(grey, [255, 255, 255, 1], size=28, sample="l")
    assert g.LOW_CONTRAST not in evaluate_observation(large).classes


def test_translucent_text_composited_before_contrast():
    # 50%-alpha black on white = mid-grey ≈ 3.95:1 < 4.5 → flagged (proves the
    # gate composites alpha instead of treating the raw colour as opaque black).
    obs = _clean_obs()
    obs["texts"][0] = _txt([0, 0, 0, 0.5], [255, 255, 255, 1], sample="t")
    assert g.LOW_CONTRAST in evaluate_observation(obs).classes


# small targets
def test_small_target_flagged():
    obs = _clean_obs()
    obs["controls"][0].update(rectW=30, rectH=30, displayInline=False)
    assert g.SMALL_TARGET in evaluate_observation(obs).classes


def test_large_enough_target_passes():
    assert g.SMALL_TARGET not in evaluate_observation(_clean_obs()).classes


def test_inline_text_link_exempt():
    obs = _clean_obs()
    obs["controls"][0].update(rectW=40, rectH=20, displayInline=True)
    assert g.SMALL_TARGET not in evaluate_observation(obs).classes


def test_disabled_small_control_skipped():
    obs = _clean_obs()
    obs["controls"][0].update(rectW=20, rectH=20, displayInline=False, disabled=True)
    assert g.SMALL_TARGET not in evaluate_observation(obs).classes


# accent family
def test_two_accent_families_flagged():
    obs = _clean_obs()
    obs["fills"] = [
        {"bg": [99, 102, 241, 1], "tag": "a", "area": 100},   # indigo (~240°)
        {"bg": [16, 185, 129, 1], "tag": "button", "area": 100},  # emerald (~160°)
    ]
    report = evaluate_observation(obs)
    assert g.ACCENT_FAMILY in report.classes
    assert len(report.accent_colors) == 2


def test_one_accent_family_two_shades_passes():
    obs = _clean_obs()
    obs["fills"] = [
        {"bg": [99, 102, 241, 1], "tag": "a", "area": 100},   # indigo
        {"bg": [79, 70, 229, 1], "tag": "button", "area": 100},  # darker indigo, same bucket
    ]
    assert g.ACCENT_FAMILY not in evaluate_observation(obs).classes


def test_grey_fills_ignored_for_accent():
    obs = _clean_obs()
    obs["fills"] = [
        {"bg": [99, 102, 241, 1], "tag": "a", "area": 100},     # one real accent
        {"bg": [240, 240, 240, 1], "tag": "button", "area": 100},  # grey — not an accent
        {"bg": [20, 20, 20, 1], "tag": "button", "area": 100},     # near-black — not an accent
    ]
    assert g.ACCENT_FAMILY not in evaluate_observation(obs).classes


def test_no_fills_passes_accent():
    obs = _clean_obs()
    obs["fills"] = []
    report = evaluate_observation(obs)
    assert g.ACCENT_FAMILY not in report.classes
    assert report.accent_colors == ()


# subscore shape
def test_subscore_shape():
    obs = _clean_obs()
    obs["scrollWidth"] = 999
    obs["controls"][0]["href"] = "#"
    report = evaluate_observation(obs)
    sub = report.subscore()
    assert sub["gate"] == "wow-dom"
    assert sub["rendered"] is True
    assert sub["passed"] is False
    assert sub["h_scroll"] is True
    assert sub["counts"]["dead-control"] == 1
    assert set(sub["counts"]) == set(g.CHECKS)
    assert isinstance(sub["accent_colors"], list)


def test_multiple_checks_aggregate():
    obs = _clean_obs()
    obs["scrollWidth"] = 500
    obs["texts"][0]["color"] = [210, 210, 210, 1]
    obs["controls"][0].update(rectW=20, rectH=20, href="#")
    report = evaluate_observation(obs)
    assert {g.H_SCROLL, g.LOW_CONTRAST, g.SMALL_TARGET, g.DEAD_CONTROL} <= set(report.classes)
    assert not report.passed


def test_findings_capped_per_check():
    obs = _clean_obs()
    obs["texts"] = [
        _txt([205, 205, 205, 1], [255, 255, 255, 1], sample=f"s{i}")
        for i in range(40)
    ]
    report = evaluate_observation(obs)
    assert report.counts[g.LOW_CONTRAST] <= g._MAX_PER_CHECK


def test_luminance_monotonic():
    # sanity: darker grey has lower luminance than lighter grey
    assert relative_luminance((50.0, 50.0, 50.0)) < relative_luminance((200.0, 200.0, 200.0))
    assert math.isclose(relative_luminance((128.0, 128.0, 128.0)),
                        relative_luminance((128.0, 128.0, 128.0)))


# ── HARNESS-PARITY (V1.6 12/5): read the painted DOM, not the empty shell ──────
#
# A Next.js `/p/<slug>` is a client render — accent CTAs, sections and colour land
# *after* `load`, so a harness reading at `domcontentloaded`+600ms sees an empty
# shell (no fills → 0 accent colours) and reports a hollow PASS that silently
# un-gates beauty on the hot path. The fixed harness waits `load`+networkidle+
# fonts.ready+900ms and reads the hydrated content. These two tests are the
# falsifiable gate: behavioural (renders the late-painting fixture) and structural
# (the source can never regress to `domcontentloaded`).

_CLIENT_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "client-rendered.html"


def test_client_rendered_fixture_is_read_after_hydration():
    """The committed late-painting fixture, rendered for real, must be SEEN.

    Empty shell → 0 accent colours (no painted CTA); hydrated content → the blue
    CTA is read. Before the harness-parity fix (read at ``domcontentloaded``+600ms)
    this is red; after it (``load``+networkidle+settle) the painted DOM is read.
    Abstains (skips) without chromium; runs with teeth in the prod-worker container."""
    html = _CLIENT_FIXTURE.read_text(encoding="utf-8")
    rep = asyncio.run(g.audit_files({"index.html": html}))
    if not rep.rendered:
        pytest.skip("no chromium available — verified in prod-worker container")
    # The accent CTA is painted ~750ms in; only a `load`+networkidle+settle harness
    # waits long enough to read it. An empty shell yields zero accent colours.
    assert len(rep.accent_colors) >= 1, (
        f"harness read the empty shell, not the hydrated DOM: {rep.subscore()}"
    )


def test_audit_harness_routes_through_shared_settle_helper():
    """Structural ratchet (V1.6 13/5): the render harness must never read at
    ``domcontentloaded`` again — the recurring class that produced hollow PASSes on
    live niches. Navigation + settle now live in the single ``render_settle`` helper
    (``test_render_settle`` enforces the no-direct-``goto`` rule across all legs);
    here we assert wow_dom routes both audit paths through it. Pure source assertion
    (no browser), so it has teeth everywhere."""
    src = Path(g.__file__).read_text(encoding="utf-8")
    assert "from .render_settle import goto_and_settle" in src, (
        "wow_dom_gate must import the shared goto_and_settle helper"
    )
    assert src.count("goto_and_settle(page,") >= 2, (
        "both audit_url and audit_files must navigate via goto_and_settle"
    )
    assert 'wait_until="domcontentloaded"' not in src, (
        "wow_dom_gate must never reference domcontentloaded directly"
    )
