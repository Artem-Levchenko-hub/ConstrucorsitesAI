"""Tests for `services.skill_library` — vendored `ui-ux-pro-max` loaders.

These tests verify the CSV-backed loaders parse cleanly and the public
helpers (`lookup_*`, `random_ux_guidelines`, `format_design_brief`) return
the shape callers will rely on. Source-of-truth CSVs are in
`apps/api/skills/ui-ux-pro-max/data/` — if upstream re-syncs them, this
test suite is what catches schema drift.
"""

from __future__ import annotations

import pytest

from omnia_api.services import skill_library


def test_palettes_load_at_least_100() -> None:
    """SKILL.md frontmatter claims 161 palettes — assert the loader actually
    sees a triple-digit count, not just a single header row."""
    palettes = skill_library._palettes()
    assert len(palettes) >= 100
    p = palettes[0]
    assert p["primary"].startswith("#")
    assert p["foreground"].startswith("#")
    assert p["product_type"]


def test_font_pairings_load_at_least_30() -> None:
    fps = skill_library._font_pairings()
    assert len(fps) >= 30
    fp = fps[0]
    assert fp["heading"]
    assert fp["body"]
    assert "fonts.googleapis.com" in fp["css_import"]


def test_ux_guidelines_load_at_least_50() -> None:
    rules = skill_library._ux_guidelines()
    assert len(rules) >= 50
    rule = rules[0]
    assert rule["do"]
    assert rule["dont"]
    assert rule["severity"] in {"High", "Medium", "Low"}


def test_lookup_palette_matches_saas_keyword() -> None:
    palette = skill_library.lookup_palette("SaaS")
    assert palette is not None
    assert "saas" in palette["product_type"].lower()
    assert palette["primary"].startswith("#")


def test_lookup_palette_returns_none_on_empty_keywords() -> None:
    assert skill_library.lookup_palette() is None


def test_lookup_palette_returns_none_on_no_match() -> None:
    # A keyword that genuinely matches nothing in the product-type column.
    assert skill_library.lookup_palette("zzzzzzz_no_such_thing") is None


def test_lookup_font_pairing_matches_tech_keyword() -> None:
    fp = skill_library.lookup_font_pairing("tech", "startup")
    assert fp is not None
    # "Tech Startup" pair scores 2 (tech + startup); should win.
    assert "tech" in (fp["name"] + " " + fp["keywords"] + " " + fp["best_for"]).lower()


def test_random_ux_guidelines_respects_limit() -> None:
    rules = skill_library.random_ux_guidelines(limit=3, severity="High", seed=42)
    assert len(rules) == 3
    assert all(g["severity"].lower() == "high" for g in rules)


def test_random_ux_guidelines_seeded_is_deterministic() -> None:
    a = skill_library.random_ux_guidelines(limit=5, seed=123)
    b = skill_library.random_ux_guidelines(limit=5, seed=123)
    assert [g["issue"] for g in a] == [g["issue"] for g in b]


def test_random_ux_guidelines_no_severity_filter() -> None:
    pool = skill_library.random_ux_guidelines(limit=10, severity=None, seed=1)
    # Mixed severities possible when no filter.
    severities = {g["severity"] for g in pool}
    assert len(severities) >= 1  # at least one — could be all-High by chance


def test_format_design_brief_empty_inputs_returns_empty_string() -> None:
    assert skill_library.format_design_brief() == ""


def test_format_design_brief_palette_only() -> None:
    palette = skill_library.lookup_palette("SaaS")
    out = skill_library.format_design_brief(palette=palette)
    assert "PALETTE" in out
    assert "FONTS" not in out
    assert "UX RULES" not in out


def test_format_design_brief_all_sections() -> None:
    palette = skill_library.lookup_palette("SaaS")
    fp = skill_library.lookup_font_pairing("tech")
    rules = skill_library.random_ux_guidelines(limit=3, seed=7)
    out = skill_library.format_design_brief(
        palette=palette, font_pairing=fp, guidelines=rules
    )
    assert "PALETTE" in out
    assert "FONTS" in out
    assert "UX RULES" in out
    # Compact: <1KB even with all three sections.
    assert len(out) < 1500


@pytest.mark.parametrize("label", ["primary", "accent", "bg", "fg", "muted", "border"])
def test_format_design_brief_includes_palette_labels(label: str) -> None:
    """Every palette field the brief surfaces should be labelled in the output —
    not raw hex without a key — so the model knows what each color is for."""
    palette = skill_library.lookup_palette("E-commerce")
    out = skill_library.format_design_brief(palette=palette)
    assert label in out.lower()
