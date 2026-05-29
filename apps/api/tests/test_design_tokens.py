"""Design-token spread + determinism (Phase 11, Sprint 1.3)."""

from omnia_api.sections.palettes import all_palettes
from omnia_api.services.design_tokens import tokens_for_project


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


def test_css_vars_valid_root_block():
    css = tokens_for_project("proj-y").css_vars()
    assert css.startswith(":root{")
    assert css.rstrip().endswith("}")
    assert "--accent" in css
    assert "--font-display" in css


def test_industry_hint_narrows_to_vibe():
    t = tokens_for_project("proj-fin", industry_hint="fintech")
    assert t.palette.vibe == "fintech-trust"


def test_dark_mode_filter():
    assert tokens_for_project("proj-dark", dark_mode=True).palette.dark_mode is True
    assert tokens_for_project("proj-light", dark_mode=False).palette.dark_mode is False


def test_every_project_resolves_a_real_palette():
    valid = {p.id for p in all_palettes()}
    for i in range(60):
        assert tokens_for_project(f"p{i}").palette.id in valid
