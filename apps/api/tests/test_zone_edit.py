"""Tests for zone-scoped edit helpers (owner directive 2026-06-06)."""

from __future__ import annotations

from omnia_api.services.zone_edit import (
    distinctive_anchors,
    extract_block,
    find_enclosing_block,
    root_id,
    splice,
)

PAGE = (
    '<header class="nav"><a>Logo</a></header>\n'
    '<section id="hero" class="relative min-h-[90vh]">\n'
    '  <div class="omnia-shader" data-omnia-colors="#0C0A09,#1C1816"></div>\n'
    '  <div class="omnia-shader-over relative z-10"><h1>PREMIUM SUSHI</h1></div>\n'
    "</section>\n"
    '<section id="about" class="py-24"><h2>О нас</h2></section>\n'
    "<footer>contacts</footer>"
)


def test_distinctive_anchors_skips_generic() -> None:
    anchors = distinctive_anchors(
        [{"selector": "div.omnia-shader-over.relative.z-10", "text": "PREMIUM SUSHI"}]
    )
    # generic classes dropped; the overlay class is intentionally generic too
    assert "relative" not in anchors
    assert "z-10" not in anchors
    assert "omnia-shader-over" not in anchors  # in the generic skip-set
    # the visible text survives as an anchor
    assert "PREMIUM SUSHI" in anchors


def test_find_enclosing_block_picks_the_hero_section() -> None:
    # anchor by the distinctive shader class
    span = find_enclosing_block(PAGE, ["omnia-shader"])
    assert span is not None
    block = PAGE[span[0] : span[1]]
    assert block.startswith('<section id="hero"')
    assert block.endswith("</section>")
    assert "PREMIUM SUSHI" in block
    # the OTHER section + header/footer are NOT inside
    assert "О нас" not in block
    assert "Logo" not in block


def test_find_enclosing_block_by_text_anchor() -> None:
    span = find_enclosing_block(PAGE, ["PREMIUM SUSHI"])
    assert span is not None
    assert PAGE[span[0] : span[1]].startswith('<section id="hero"')


def test_find_enclosing_block_none_when_no_match() -> None:
    assert find_enclosing_block(PAGE, ["does-not-exist-xyz"]) is None
    assert find_enclosing_block(PAGE, []) is None


def test_root_id_and_extract_block() -> None:
    block = '<section id="hero" class="x">...</section>'
    assert root_id(block) == "hero"
    out = extract_block(
        'Готово.\n```html\n<section id="hero" class="y">new</section>\n```\nконец'
    )
    assert out == '<section id="hero" class="y">new</section>'
    assert root_id(out) == "hero"
    assert extract_block("просто текст") is None


def test_splice_replaces_only_the_span() -> None:
    span = find_enclosing_block(PAGE, ["omnia-shader"])
    assert span is not None
    new_block = '<section id="hero" class="relative min-h-[90vh]">CHANGED</section>'
    out = splice(PAGE, span, new_block)
    assert "CHANGED" in out
    # everything outside the hero is byte-identical
    assert '<section id="about" class="py-24"><h2>О нас</h2></section>' in out
    assert '<header class="nav"><a>Logo</a></header>' in out
    assert "<footer>contacts</footer>" in out
    # the old hero content is gone
    assert "PREMIUM SUSHI" not in out
