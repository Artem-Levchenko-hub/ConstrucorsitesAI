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


# ─── global brand surface (prod bug 2026-06-16) ───────────────────────────

_GLOBALS_PATH = "src/app/globals.css"
# Mirrors the template: default near-black :root, a dark-neutral block, and a
# low-specificity .dark brand fallback — the trailing brand block must win.
_GLOBALS = (
    ":root {\n  --primary: oklch(0.21 0.006 285.9);\n"
    "  --primary-foreground: oklch(0.985 0 0);\n  --ring: oklch(0.62 0.13 264);\n}\n"
    ":root.dark, .dark {\n  --background: oklch(0.16 0.004 285.8);\n}\n"
    ".dark {\n  --primary: oklch(0.92 0.004 286.3);\n}\n"
    "@theme inline {\n  --color-primary: var(--primary);\n}\n"
)
# The writer scoped a CORRECT teal brand to the public landing only (page.tsx).
_PAGE_TEAL = (
    "export default function Home(){return (<main>"
    '<style>{":root{--primary:oklch(0.65 0.18 185);'
    "--primary-foreground:oklch(0.99 0 0);--ring:oklch(0.65 0.18 185);"
    '--radius:0.5rem}"}</style>'
    "<h1>Бел-Дент</h1></main>)}"
)


def test_global_brand_carries_writer_landing_teal_to_globals():
    # THE bug: landing teal in page.tsx, dashboard reverts to template near-black.
    # The harvested teal must be pinned globally so every route is themed.
    out = apply_app_palette(
        {_GLOBALS_PATH: _GLOBALS, "src/app/page.tsx": _PAGE_TEAL}, BURGUNDY
    )
    css = out[_GLOBALS_PATH]
    assert "/* omnia:global-brand */" in css
    assert "--primary:oklch(0.65 0.18 185)" in css  # the writer's teal, verbatim
    # appended LAST so it wins source-order over the default :root and .dark
    assert css.rstrip().endswith("}")
    assert css.index("/* omnia:global-brand */") > css.index(".dark {")


def test_global_brand_prefers_writer_visible_over_palette_pick():
    # Writer chose a real teal; the project palette is the unrelated burgundy.
    # The hand-picked teal wins over pick_app_primary(BURGUNDY) == gold.
    out = apply_app_palette(
        {_GLOBALS_PATH: _GLOBALS, "src/app/page.tsx": _PAGE_TEAL}, BURGUNDY
    )
    assert "oklch(0.65 0.18 185)" in out[_GLOBALS_PATH]
    assert "#CA8A04" not in out[_GLOBALS_PATH]


def test_global_brand_rejects_near_white_writer_primary():
    # A washed-out near-white landing primary is NOT trusted — fall back to the
    # deterministic palette pick (gold) so the app is not invisible.
    page = _PAGE_TEAL.replace("oklch(0.65 0.18 185)", "oklch(0.96 0.01 28)")
    out = apply_app_palette(
        {_GLOBALS_PATH: _GLOBALS, "src/app/page.tsx": page}, BURGUNDY
    )
    css = out[_GLOBALS_PATH]
    assert "--primary:#CA8A04" in css
    assert "oklch(0.96 0.01 28)" not in css


def test_global_brand_falls_back_to_palette_without_writer_override():
    out = apply_app_palette({_GLOBALS_PATH: _GLOBALS}, PAPER_TEA)
    assert "--primary:#134E4A" in out[_GLOBALS_PATH]


def test_global_brand_is_idempotent():
    files = {_GLOBALS_PATH: _GLOBALS, "src/app/page.tsx": _PAGE_TEAL}
    once = apply_app_palette(files, BURGUNDY)
    twice = apply_app_palette(once, BURGUNDY)
    assert once[_GLOBALS_PATH] == twice[_GLOBALS_PATH]
    # exactly one global-brand block, not stacked on re-run
    assert once[_GLOBALS_PATH].count("/* omnia:global-brand */") == 1
    assert twice[_GLOBALS_PATH].count("/* omnia:global-brand */") == 1


def test_global_brand_no_globals_file_is_noop():
    # Freeform builds have no globals.css → nothing pinned, fail-soft.
    files = {"index.html": "<html></html>"}
    assert apply_app_palette(files, BURGUNDY) == files


def test_layout_and_globals_share_one_brand():
    # When the writer DID author an (app)/layout block, it is synced to the SAME
    # harvested brand as the global surface (one colour everywhere).
    files = {
        _GLOBALS_PATH: _GLOBALS,
        "src/app/page.tsx": _PAGE_TEAL,
        _LAYOUT_PATH: _LAYOUT,
    }
    out = apply_app_palette(files, BURGUNDY)
    assert "--primary:oklch(0.65 0.18 185)" in out[_GLOBALS_PATH]
    assert "--primary:oklch(0.65 0.18 185)" in out[_LAYOUT_PATH]
    # brief-driven radius/motion on the layout block are preserved
    assert "--radius:0.65rem" in out[_LAYOUT_PATH]
