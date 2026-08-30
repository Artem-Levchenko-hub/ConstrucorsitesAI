"""Design-token spread + determinism (Phase 11, Sprint 1.3)."""

from omnia_api.sections.palettes import all_palettes
from omnia_api.services import design_tokens
from omnia_api.services.design_tokens import tokens_for_project
from omnia_api.services.skill_library import font_supports_cyrillic


def test_deterministic_per_project():
    a = tokens_for_project("proj-123")
    b = tokens_for_project("proj-123")
    assert a == b
    assert a.palette.id == b.palette.id
    assert (a.display_font, a.body_font) == (b.display_font, b.body_font)


def test_spread_across_projects():
    ids = [f"proj-{i}" for i in range(40)]
    toks = [tokens_for_project(i) for i in ids]
    palettes = {t.palette.id for t in toks}
    fonts = {(t.display_font, t.body_font) for t in toks}
    # The whole point of Phase 11: 40 projects must NOT collapse onto one
    # palette/font. (Old behaviour was always the first palette.)
    assert len(palettes) >= 8
    assert len(fonts) >= 5


def test_prompt_block_carries_palette_and_fonts():
    t = tokens_for_project("proj-x")
    block = t.prompt_block()
    assert t.palette.bg in block
    assert t.palette.accent in block
    assert t.display_font in block
    assert t.body_font in block
    assert "fonts.googleapis.com" in block
    assert ":root" in block
    # The anti-default guard must be present.
    assert "indigo" in block.lower()
    assert "ЕДИНОЕ АРТ-НАПРАВЛЕНИЕ" in block
    assert t.direction_id in block


def test_css_vars_valid_root_block():
    css = tokens_for_project("proj-y").css_vars()
    assert css.startswith(":root{")
    assert css.rstrip().endswith("}")
    assert "--accent" in css
    assert "--font-display" in css


def test_industry_hint_narrows_to_vibe():
    t = tokens_for_project("proj-fin", industry_hint="fintech")
    assert t.palette.vibe == "fintech-trust"


def test_fitness_uses_one_semantic_lane_with_controlled_variety():
    toks = [
        tokens_for_project(f"fitness-{i}", industry_hint="Фитнес мини-приложение для тренировок")
        for i in range(40)
    ]
    assert {t.direction_id for t in toks} == {"fitness-performance"}
    assert {t.palette.vibe for t in toks} <= {"linear-dark", "brutalist"}
    assert all(t.palette.dark_mode for t in toks)
    assert len({t.palette.id for t in toks}) >= 4
    assert len({(t.display_font, t.body_font) for t in toks}) == 5
    assert all(font_supports_cyrillic(t.display_font) for t in toks)
    assert all(font_supports_cyrillic(t.body_font) for t in toks)


def test_latin_fitness_can_use_catalogue_sports_pair():
    pairs = {
        tokens_for_project(
            f"latin-fitness-{i}",
            industry_hint="fitness gym workout app",
            require_cyrillic=False,
        ).font_pair_name
        for i in range(80)
    }
    assert "Sports/Fitness" in pairs
    assert pairs == {
        "Sports/Fitness",
        "Bold Statement",
        "Kinetic Brutalism (Space Grotesk)",
    }


def test_english_preset_id_still_defaults_to_cyrillic_safe_fonts():
    tokens = [tokens_for_project(f"preset-{i}", industry_hint="fitness") for i in range(30)]
    assert all(font_supports_cyrillic(t.display_font) for t in tokens)
    assert all(font_supports_cyrillic(t.body_font) for t in tokens)


def test_explicit_light_mode_wins_over_fitness_dark_anchor():
    t = tokens_for_project(
        "fitness-light",
        industry_hint="fitness gym app",
        dark_mode=False,
    )
    assert t.direction_id == "fitness-performance"
    assert t.palette.dark_mode is False


def test_google_fonts_url_only_requests_supported_weights():
    tokens = next(
        t
        for i in range(50)
        if (t := tokens_for_project(f"russo-{i}", industry_hint="Фитнес тренировки")).display_font
        == "Russo One"
    )
    assert "family=Russo+One:wght@400" in tokens.google_fonts_url
    assert "Russo+One:wght@400;500" not in tokens.google_fonts_url


def test_catalogue_pair_keeps_its_exact_multi_family_google_url():
    direction = design_tokens._direction_for_hint("creative agency")
    cohort = design_tokens._font_cohort(direction, require_cyrillic=False)
    pair = next(p for p in cohort if p.name == "Bold Typography Mobile (Inter Poster)")
    assert pair.google_fonts_url is not None
    assert "family=JetBrains+Mono" in pair.google_fonts_url
    assert "family=Playfair+Display:ital@1" in pair.google_fonts_url


def test_dark_mode_filter():
    assert tokens_for_project("proj-dark", dark_mode=True).palette.dark_mode is True
    assert tokens_for_project("proj-light", dark_mode=False).palette.dark_mode is False


def test_every_project_resolves_a_real_palette():
    valid = {p.id for p in all_palettes()}
    for i in range(60):
        assert tokens_for_project(f"p{i}").palette.id in valid
