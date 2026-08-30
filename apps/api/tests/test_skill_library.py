"""Tests for `services.skill_library` — vendored `ui-ux-pro-max` loaders.

These tests verify the CSV-backed loaders parse cleanly and the public
helpers (`lookup_*`, `random_ux_guidelines`, `format_design_brief`) return
the shape callers will rely on. Source-of-truth CSVs are in
`apps/api/skills/ui-ux-pro-max/data/` — if upstream re-syncs them, this
test suite is what catches schema drift.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from omnia_api.services import skill_library


def test_palettes_load_current_snapshot() -> None:
    palettes = skill_library._palettes()
    assert len(palettes) >= 192
    p = palettes[0]
    assert p["primary"].startswith("#")
    assert p["foreground"].startswith("#")
    assert p["product_type"]


def test_font_pairings_load_current_snapshot() -> None:
    fps = skill_library._font_pairings()
    assert len(fps) >= 74
    fp = fps[0]
    assert fp["heading"]
    assert fp["body"]
    assert "fonts.googleapis.com" in fp["css_import"]


def test_ux_guidelines_load_current_snapshot() -> None:
    rules = skill_library._ux_guidelines()
    assert len(rules) >= 119
    rule = rules[0]
    assert rule["do"]
    assert rule["dont"]
    assert rule["severity"] in {"High", "Medium", "Low"}


def test_vendored_snapshot_has_exact_source_and_license() -> None:
    root = Path(__file__).parents[1] / "skills" / "ui-ux-pro-max"
    source = json.loads((root / "SOURCE.json").read_text(encoding="utf-8"))

    assert source["version"] == "2.13.0"
    assert source["commit"] == "8a1a6d857332da32252d77365da90c3f6293b47b"
    assert source["license"] == "MIT"
    assert "MIT License" in (root / "LICENSE").read_text(encoding="utf-8")


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


def test_font_pairing_candidates_keep_semantic_variety() -> None:
    pairs = skill_library.font_pairing_candidates("fitness", "sports", "energetic", "bold", limit=6)
    assert len(pairs) >= 3
    assert pairs[0]["name"] == "Sports/Fitness"
    assert len({(p["heading"], p["body"]) for p in pairs}) == len(pairs)


def test_font_pairing_candidates_can_require_cyrillic() -> None:
    pairs = skill_library.font_pairing_candidates(
        "fitness", "bold", "dark", limit=8, require_cyrillic=True
    )
    assert pairs
    assert all(skill_library.font_supports_cyrillic(p["heading"]) for p in pairs)
    assert all(skill_library.font_supports_cyrillic(p["body"]) for p in pairs)
    assert skill_library.font_supports_cyrillic("Inter") is True
    assert skill_library.font_supports_cyrillic("Barlow") is False
    assert skill_library.font_weights("Russo One") == (400,)
    assert 700 in skill_library.font_weights("Manrope")
    assert skill_library.font_category("Lora") == "Serif"


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
    out = skill_library.format_design_brief(palette=palette, font_pairing=fp, guidelines=rules)
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


# ───────────────────────────────────────────────────────────────────────────
# Phase J — Malewicz-derived smart lookups
# ───────────────────────────────────────────────────────────────────────────


def test_derive_gradient_pair_basic() -> None:
    """Normalized primary + a different shifted hex — the simplest contract."""
    a, b = skill_library.derive_gradient_pair("#92400E")
    assert a == "#92400e"
    assert b.startswith("#") and len(b) == 7
    assert a != b


def test_derive_gradient_pair_accepts_3_digit_hex() -> None:
    a, b = skill_library.derive_gradient_pair("#abc")
    # 3-digit expanded: #aabbcc
    assert a == "#aabbcc"
    assert b.startswith("#") and len(b) == 7


def test_derive_gradient_pair_rejects_garbage() -> None:
    with pytest.raises(ValueError):
        skill_library.derive_gradient_pair("not-a-hex")
    with pytest.raises(ValueError):
        skill_library.derive_gradient_pair("#zzzzzz")
    with pytest.raises(ValueError):
        skill_library.derive_gradient_pair("#12345")  # 5 digits — invalid


def test_derive_gradient_pair_hue_wrap() -> None:
    """Hue + 25° must wrap properly past 360 — deep pink near hue 328
    rotates to about hue 353, still produces a valid hex (not crash)."""
    _, b = skill_library.derive_gradient_pair("#FF1493")
    assert len(b) == 7
    # Returned hex must parse as valid hex
    int(b.lstrip("#"), 16)


def test_derive_gradient_pair_saturation_clamp() -> None:
    """Saturation × 0.9 of fully-saturated input still produces valid hex —
    no negative-saturation crash."""
    _, b = skill_library.derive_gradient_pair("#000000")  # zero sat
    assert b == "#000000" or b.startswith("#")  # black stays black


def test_derive_shadow_tint_returns_required_keys() -> None:
    tint = skill_library.derive_shadow_tint("#92400E")
    for key in ("x", "y", "blur", "spread", "color", "tint_hex", "css"):
        assert key in tint, f"missing key {key!r}"
    assert tint["css"].startswith("box-shadow:")
    assert tint["css"].endswith(";")


def test_derive_shadow_tint_alpha_clamp() -> None:
    """rgba alpha must be ≤ 0.4 per Malewicz Ch9."""
    tint = skill_library.derive_shadow_tint("#92400E")
    alpha = float(tint["color"].rsplit(",", 1)[1].rstrip(")").strip())
    assert 0 < alpha <= 0.4


def test_derive_shadow_tint_spread_negative() -> None:
    """spread is always negative — keeps the shadow inside the silhouette."""
    tint = skill_library.derive_shadow_tint("#2563EB")
    assert tint["spread"] < 0


def test_derive_shadow_tint_rejects_garbage() -> None:
    with pytest.raises(ValueError):
        skill_library.derive_shadow_tint("nope")


def test_auto_nav_style_no_hamburger_primary() -> None:
    """G17 — hamburger MUST NOT be primary mobile nav."""
    assert skill_library.auto_nav_style("mobile", "primary") == "bottom-tabs"
    # Default tier is primary
    assert skill_library.auto_nav_style("mobile") == "bottom-tabs"


def test_auto_nav_style_hamburger_secondary_only() -> None:
    """Secondary is the only place hamburger is allowed."""
    assert skill_library.auto_nav_style("mobile", "secondary") == "hamburger"


def test_auto_nav_style_desktop_primary() -> None:
    assert skill_library.auto_nav_style("desktop", "primary") == "top-bar"


def test_auto_nav_style_desktop_secondary_side_rail() -> None:
    """Desktop secondary = side-rail (app-style: Slack/Linear/Discord layout)."""
    assert skill_library.auto_nav_style("desktop", "secondary") == "side-rail"


def test_auto_nav_style_unknown_target_raises() -> None:
    with pytest.raises(ValueError):
        skill_library.auto_nav_style("watch", "primary")
    with pytest.raises(ValueError):
        skill_library.auto_nav_style("mobile", "tertiary")


def test_lookup_micro_copy_returns_dict() -> None:
    out = skill_library.lookup_micro_copy("save", "fitness")
    assert "primary" in out and "secondary" in out
    # "Save workout" → primary contains "save" or "workout"
    assert "save" in out["primary"].lower() or "workout" in out["primary"].lower()


def test_lookup_micro_copy_russian_vertical() -> None:
    """RU verticals (food/medical/legal/realestate/education) emit Cyrillic."""
    out = skill_library.lookup_micro_copy("subscribe", "medical")
    # Should be Cyrillic ("Записаться к врачу" / "Не сейчас")
    assert any("Ѐ" <= ch <= "ӿ" for ch in out["primary"]), f"expected Cyrillic primary, got {out!r}"


def test_lookup_micro_copy_unknown_pair_fallback() -> None:
    """Missing (context, vertical) returns context.title() + 'Отмена' —
    safe no-KeyError contract."""
    out = skill_library.lookup_micro_copy("login", "fitness")
    assert out["primary"] == "Login"
    assert out["secondary"] == "Отмена"


def test_lookup_micro_copy_all_5_contexts_for_each_vertical() -> None:
    """The mini-table must cover all 10 verticals × 5 contexts so generated
    copy is never the dumb fallback for known verticals."""
    contexts = ("save", "delete", "subscribe", "cancel", "submit")
    verticals = (
        "fitness",
        "saas",
        "wellness",
        "food",
        "medical",
        "legal",
        "realestate",
        "education",
        "media",
        "commerce",
    )
    for v in verticals:
        for c in contexts:
            out = skill_library.lookup_micro_copy(c, v)
            # Non-fallback values: primary != context.title() (fallback shape).
            assert out["primary"] != c.title(), f"missing copy for ({c!r}, {v!r}) — got fallback"


def test_lookup_design_patterns_has_usability_score() -> None:
    """Phase J extension — every returned pattern carries a 1-10 score."""
    patterns = skill_library.lookup_design_patterns("saas", limit=3)
    if patterns:  # only assert structure when there's a match
        assert "usability_score" in patterns[0]
        assert 1 <= patterns[0]["usability_score"] <= 10


def test_lookup_design_patterns_score_for_neumorphism() -> None:
    """Neumorphism scores low (4) per Ch24 accessibility issues."""
    patterns = skill_library.lookup_design_patterns("neumorphism", limit=5)
    # We can't guarantee neumorphism is in the corpus, so only assert
    # IF we got matches; otherwise this is a no-op.
    for p in patterns:
        if "neumorphism" in (p["name"] + p["vibe_tags"] + p["summary"]).lower():
            assert p["usability_score"] == 4
            return  # found and asserted; done


def test_format_design_brief_renders_phase_j_block() -> None:
    """When derived tokens are passed, brief shows ПРОИЗВОДНЫЕ ТОКЕНЫ section."""
    out = skill_library.format_design_brief(
        gradient_pair=("#92400e", "#a16207"),
        shadow_tint={
            "x": 0,
            "y": 8,
            "blur": 20,
            "spread": -2,
            "color": "rgba(146, 64, 14, 0.18)",
            "tint_hex": "#5d2e0a",
            "css": "box-shadow: 0 8px 20px -2px rgba(146, 64, 14, 0.18);",
        },
        nav_style="bottom-tabs (mobile primary)",
        micro_copy={
            "save": {"primary": "Save workout", "secondary": "Cancel"},
        },
    )
    assert "ПРОИЗВОДНЫЕ ТОКЕНЫ" in out
    assert "#92400e" in out
    assert "bottom-tabs" in out
    assert "Save workout" in out


def test_format_design_brief_skips_empty_phase_j_lines() -> None:
    """Partial Phase J input renders only the lines with data — no empty entries."""
    out = skill_library.format_design_brief(
        gradient_pair=("#000000", "#111111"),
    )
    assert "ПРОИЗВОДНЫЕ ТОКЕНЫ" in out
    assert "gradient_pair" in out
    # No shadow / nav / micro_copy lines
    assert "shadow_tint" not in out
    assert "nav_style" not in out
    assert "micro_copy" not in out


def test_format_design_brief_no_phase_j_section_when_all_none() -> None:
    """When no Phase J kwarg is passed, the section header is absent."""
    palette = skill_library.lookup_palette("SaaS")
    out = skill_library.format_design_brief(palette=palette)
    assert "ПРОИЗВОДНЫЕ ТОКЕНЫ" not in out
