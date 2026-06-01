"""Tests for the deterministic palette guard (services/palette_guard.py).

Pins prod bug 2026-06-01: a freeform page invented its own off-palette colours
(`--brand:#DC2626`, training-default indigo/violet) instead of using the
project's seeded curated palette. The guard must snap :root colour vars to the
curated palette, kill banned indigo/violet/purple literals, leave on-palette
pages untouched, and never raise.
"""

from __future__ import annotations

from omnia_api.sections.palettes import CuratedPalette, all_palettes
from omnia_api.services.palette_guard import (
    BANNED_HEXES,
    enforce_palette,
    repair_html,
)


def _light_palette() -> CuratedPalette:
    for p in all_palettes():
        if p.id == "swiss-stone-noir":
            return p
    return next(p for p in all_palettes() if not p.dark_mode)


PAL = _light_palette()

# Sushi-style failure: invented --brand + alias vars, none matching the palette.
_OFF_PALETTE = (
    "<html><head><style>:root {\n"
    "  --brand: #DC2626;\n"
    "  --background: #0F1419;\n"
    "  --text: #1F1F1F;\n"
    "  --border: #E5E5E5;\n"
    "  --font-display: 'DM Sans';\n"
    "}</style></head><body>hi</body></html>"
)


def test_snaps_root_vars_to_curated_palette() -> None:
    fixed, changed = repair_html(_OFF_PALETTE, PAL)
    assert changed is True
    # --brand maps to the 'primary' role, --background to 'bg', etc.
    assert f"--brand: {PAL.primary};" in fixed
    assert f"--background: {PAL.bg};" in fixed
    assert f"--text: {PAL.text};" in fixed
    # font var is left untouched
    assert "--font-display: 'DM Sans';" in fixed


def test_banned_indigo_literal_replaced_with_primary() -> None:
    html = (
        "<html><body><div style='background:#6366f1'>x</div>"
        "<a class='bg-[#8b5cf6]'>y</a></body></html>"
    )
    fixed, changed = repair_html(html, PAL)
    assert changed is True
    assert "#6366f1" not in fixed.lower()
    assert "#8b5cf6" not in fixed.lower()
    assert PAL.primary in fixed


def test_all_banned_hexes_are_six_digit_lowercase() -> None:
    # Guards the BANNED_HEXES contract the matcher relies on.
    for h in BANNED_HEXES:
        assert h == h.lower()
        assert len(h) == 7 and h.startswith("#")


def test_on_palette_page_untouched() -> None:
    good = (
        f"<html><head><style>:root {{ --bg: {PAL.bg}; --primary: {PAL.primary}; "
        f"--text: {PAL.text}; }}</style></head><body>hi</body></html>"
    )
    fixed, changed = repair_html(good, PAL)
    assert changed is False
    assert fixed == good


def test_var_reference_not_touched() -> None:
    # A var alias (not a solid hex) must be left alone — can't safely snap it.
    html = (
        "<html><head><style>:root { --primary: var(--brand); --brand: #DC2626; }"
        "</style></head><body>hi</body></html>"
    )
    fixed, _ = repair_html(html, PAL)
    assert "--primary: var(--brand);" in fixed  # untouched
    assert f"--brand: {PAL.primary};" in fixed   # solid hex snapped


def test_idempotent() -> None:
    once, _ = repair_html(_OFF_PALETTE, PAL)
    twice, changed2 = repair_html(once, PAL)
    assert changed2 is False
    assert twice == once


def test_enforce_palette_only_touches_html() -> None:
    files = {
        "index.html": _OFF_PALETTE,
        "assets/app.css": ":root{--brand:#6366f1}",
        "data.json": "{}",
    }
    out = enforce_palette(files, PAL)
    assert out["index.html"] != _OFF_PALETTE
    assert out["assets/app.css"] == files["assets/app.css"]  # css file untouched
    assert out["data.json"] == "{}"


def test_enforce_palette_never_raises_on_garbage() -> None:
    out = enforce_palette({"index.html": "<style>:root{{{::"}, PAL)
    assert isinstance(out, dict)
