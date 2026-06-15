"""Tests for the deterministic entity-app brand-token injector (app_theme)."""

from __future__ import annotations

from omnia_api.sections.palettes import CuratedPalette, contrast_ratio
from omnia_api.services.app_theme import apply_app_palette, pick_app_primary

# Real curated palettes (values mirror sections/palettes.py).
BURGUNDY = CuratedPalette(  # dark editorial: `primary` is a NEAR-WHITE tint
    "luxury-burgundy", "Burgundy", "editorial-luxury",
    "#FEF2F2", "#CA8A04", "#450A0A", "#7F1D1D", "#FAFAFA", "#FCA5A5", "#991B1B",
    dark_mode=True,
)
PAPER_TEA = CuratedPalette(  # swiss-minimal: `primary` is a real deep teal
    "swiss-paper-tea", "Paper Tea", "swiss-minimal",
    "#134E4A", "#CA8A04", "#FAFAF9", "#FFFFFF", "#0F172A", "#475569", "#E2E8F0",
)

_LAYOUT_PATH = "src/app/(app)/layout.tsx"
# Mirrors the writer's output: a brand :root <style> + a sibling helper <style>.
_LAYOUT = (
    "export default function AppLayout({children}){return (<html><head>"
    '<style>{":root{--primary:oklch(0.85 0.04 28);'
    "--primary-foreground:oklch(0.15 0.02 20);--ring:oklch(0.85 0.04 28);"
    "--radius:0.65rem;--omnia-ease:cubic-bezier(.22,1,.36,1);"
    '--omnia-dur:.72s}"}</style>'
    '<style>{".accent-gold{color:#CA8A04}"}</style>'
    "</head><body>{children}</body></html>)}"
)


def _apply(palette: CuratedPalette, code: str = _LAYOUT) -> str:
    return apply_app_palette({_LAYOUT_PATH: code}, palette)[_LAYOUT_PATH]


# ─── pick_app_primary ─────────────────────────────────────────────────────


def test_near_white_primary_falls_back_to_accent():
    # A dark palette's near-white `primary` is invisible on the light canvas →
    # the accent pop (gold) must be chosen instead.
    primary, fg = pick_app_primary(BURGUNDY)
    assert primary == "#CA8A04"
    # Foreground must be readable on the gold fill (a dark softened ink here).
    assert contrast_ratio(fg, primary) >= 4.5


def test_real_brand_primary_is_kept():
    primary, _ = pick_app_primary(PAPER_TEA)
    assert primary == "#134E4A"


# ─── apply_app_palette ────────────────────────────────────────────────────


def test_burgundy_layout_gets_visible_gold_primary():
    out = _apply(BURGUNDY)
    assert "--primary:#CA8A04" in out
    assert "--ring:#CA8A04" in out
    # The washed-out near-white oklch the writer guessed is gone.
    assert "oklch(0.85 0.04 28)" not in out


def test_brief_driven_radius_and_motion_are_preserved():
    out = _apply(BURGUNDY)
    assert "--radius:0.65rem" in out
    assert "--omnia-ease:cubic-bezier(.22,1,.36,1)" in out
    assert "--omnia-dur:.72s" in out


def test_sibling_rules_are_left_untouched():
    # The .accent-gold helper <style> is not the brand block — keep it verbatim.
    out = _apply(BURGUNDY)
    assert '.accent-gold{color:#CA8A04}' in out


def test_idempotent():
    once = _apply(BURGUNDY)
    twice = apply_app_palette({_LAYOUT_PATH: once}, BURGUNDY)[_LAYOUT_PATH]
    assert once == twice


def test_paper_tea_keeps_teal_primary():
    out = _apply(PAPER_TEA)
    assert "--primary:#134E4A" in out
    assert "--ring:#134E4A" in out


def test_non_entity_build_is_untouched():
    files = {"index.html": "<html><style>:root{--primary:#fff}</style></html>"}
    assert apply_app_palette(files, BURGUNDY) == files


def test_layout_without_root_block_is_fail_soft():
    code = "export default function L({children}){return <div>{children}</div>}"
    assert _apply(BURGUNDY, code) == code
