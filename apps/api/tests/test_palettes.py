"""Validation tests for the curated palette catalog (Phase L10).

Every palette in `sections/palettes.py` is contractually required to:
* Pass WCAG AA body-text contrast (text vs bg ≥ 4.5)
* Pass WCAG AA Large-text contrast for muted text (muted vs bg ≥ 3)
* Avoid Albers' "pure #000 on pure #FFF" vibration trap
* Have all 11 HEX fields present and parseable

If any of these fail, the palette regresses the design ceiling and
should not ship. This test is the gate.
"""

from __future__ import annotations

import pytest

from omnia_api.sections import palettes as pl


def test_palette_catalog_nonempty() -> None:
    all_p = pl.all_palettes()
    assert len(all_p) >= 60, f"expected ≥60 curated palettes, got {len(all_p)}"


def test_unique_palette_ids() -> None:
    ids = [p.id for p in pl.all_palettes()]
    assert len(ids) == len(set(ids)), "duplicate palette ids found"


def test_all_palettes_have_valid_hex() -> None:
    import re
    hex_re = re.compile(r"^#[0-9A-Fa-f]{6}$")
    for p in pl.all_palettes():
        for field in (
            "primary", "accent", "bg", "surface",
            "text", "muted", "border",
            "success", "warning", "error",
        ):
            v = getattr(p, field)
            assert hex_re.match(v), f"{p.id}.{field} = {v!r} not 6-digit HEX"


@pytest.mark.parametrize("palette", pl.all_palettes(), ids=lambda p: p.id)
def test_each_palette_passes_wcag(palette: pl.CuratedPalette) -> None:
    issues = pl.validate_palette(palette)
    assert not issues, f"{palette.id}: " + " | ".join(issues)


def test_pick_palette_returns_real_palette() -> None:
    assert pl.pick_palette() is not None
    assert pl.pick_palette(vibe="swiss-minimal").vibe == "swiss-minimal"
    assert pl.pick_palette(vibe="apple-tech").vibe == "apple-tech"
    assert pl.pick_palette(vibe="brutalist").vibe == "brutalist"


def test_pick_palette_respects_dark_mode() -> None:
    p_dark = pl.pick_palette(vibe="linear-dark", dark_mode=True)
    assert p_dark.dark_mode is True
    p_light = pl.pick_palette(vibe="swiss-minimal", dark_mode=False)
    assert p_light.dark_mode is False


def test_pick_palette_unknown_vibe_returns_fallback() -> None:
    # Unknown vibe → still returns *some* palette (no crash).
    p = pl.pick_palette(vibe="nope-doesnt-exist")
    assert isinstance(p, pl.CuratedPalette)


def test_contrast_ratio_known_values() -> None:
    # Sanity check against well-known WCAG examples.
    # Pure white on pure black = 21.0
    assert pl.contrast_ratio("#FFFFFF", "#000000") == pytest.approx(21.0)
    # Same colour = 1.0
    assert pl.contrast_ratio("#777777", "#777777") == pytest.approx(1.0)
    # Mid grey on white ≈ 4.5+ (passes AA body for #595959 sweetspot)
    assert pl.contrast_ratio("#595959", "#FFFFFF") > 4.5


def test_meets_wcag_aa_helpers() -> None:
    assert pl.meets_wcag_aa("#000000", "#FFFFFF") is True
    # 3.0 boundary for AA Large
    assert pl.meets_wcag_aa("#777777", "#FFFFFF", large=True) is True
    # Bad pair: light grey on white
    assert pl.meets_wcag_aa("#DDDDDD", "#FFFFFF") is False


def test_eight_vibes_covered() -> None:
    """All eight lean-prompt vibes must have ≥ 4 palette options."""
    vibes = {
        "swiss-minimal", "apple-tech", "linear-dark", "fintech-trust",
        "editorial-luxury", "brutalist", "glassmorphism", "y2k-neo",
        "wellness-casual",
    }
    for v in vibes:
        ps = pl.palettes_for_vibe(v)
        assert len(ps) >= 4, f"vibe {v!r} only has {len(ps)} palettes"


def test_get_palette_by_id() -> None:
    p = pl.get_palette("swiss-stone-noir")
    assert p is not None
    assert p.name == "Stone Noir"
    assert pl.get_palette("does-not-exist") is None


def test_albers_no_pure_extremes() -> None:
    """Every palette body uses softened neutrals, not pure #000/#FFF."""
    for p in pl.all_palettes():
        pair = (p.text.upper(), p.bg.upper())
        assert pair != ("#000000", "#FFFFFF"), \
            f"{p.id}: forbidden #000-on-#FFF"
        assert pair != ("#FFFFFF", "#000000"), \
            f"{p.id}: forbidden #FFF-on-#000"
