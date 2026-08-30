"""Pure unit tests for the font catalog (no DB / app needed)."""

from omnia_api.services import fonts
from omnia_api.services.design_tokens import _FONT_PAIRINGS
from omnia_api.services.skill_library import font_supports_cyrillic


def test_catalog_nonempty_and_unique():
    cat = fonts.font_catalog()
    assert len(cat) > 10
    families = [f.family for f in cat]
    assert len(families) == len(set(families))


def test_every_font_has_valid_url_category_and_is_known():
    for f in fonts.font_catalog():
        assert f.google_fonts_url.startswith("https://fonts.googleapis.com/css2?")
        assert "family=" in f.google_fonts_url
        assert f.category in {"sans", "serif", "display", "mono"}
        assert f.css_stack.startswith(f"'{f.family}'")
        assert fonts.is_known_family(f.family)
        assert fonts.href_for(f.family) == f.google_fonts_url
        assert fonts.css_stack_for(f.family) == f.css_stack
        assert font_supports_cyrillic(f.family)


def test_vendored_picker_pairings_are_cyrillic_safe():
    assert len(_FONT_PAIRINGS) > 10
    assert all(font_supports_cyrillic(heading) for heading, _ in _FONT_PAIRINGS)
    assert all(font_supports_cyrillic(body) for _, body in _FONT_PAIRINGS)


def test_unknown_family_rejected():
    assert not fonts.is_known_family("Definitely Not A Font 9000")
    assert fonts.href_for("Nope") is None
    assert fonts.css_stack_for("Nope") is None
