"""Tests for Phase D.1' + D.2' — awwwards corpus + design pattern snippets.

Two JSON data files under ``apps/api/data/`` feed the prompt builder with
concrete stylistic anchors (awwwards_corpus) and ready-to-paste tailwind
class lists (design_patterns). These tests verify the data loads, has the
required shape, and the lookup helpers degrade gracefully when nothing
matches — never silently returning an empty list to the prompt builder.
"""

from __future__ import annotations

from omnia_api.services.skill_library import (
    _load_awwwards_corpus,
    _load_design_patterns,
    lookup_awwwards_reference,
    lookup_design_pattern_snippets,
)


def test_corpus_has_ten_entries() -> None:
    corpus = _load_awwwards_corpus()
    assert len(corpus) >= 10
    western_count = sum(1 for e in corpus if e.get("region") == "western")
    russian_count = sum(1 for e in corpus if e.get("region") == "russian")
    assert western_count >= 5
    assert russian_count >= 5


def test_patterns_cover_required_sections() -> None:
    patterns = _load_design_patterns()
    section_types = {p["section_type"] for p in patterns}
    required = {"hero", "features", "testimonials", "pricing", "cta", "footer"}
    assert required.issubset(section_types), f"Missing sections: {required - section_types}"
    assert len(patterns) >= 48


def test_corpus_lookup_filters_by_region() -> None:
    western_only = lookup_awwwards_reference(region="western", limit=10)
    russian_only = lookup_awwwards_reference(region="russian", limit=10)
    assert len(western_only) >= 1 and len(russian_only) >= 1
    assert all(e["region"] == "western" for e in western_only)
    assert all(e["region"] == "russian" for e in russian_only)


def test_pattern_lookup_returns_matching_section() -> None:
    hero_patterns = lookup_design_pattern_snippets("hero", limit=5)
    assert len(hero_patterns) >= 1
    assert all(p["section_type"] == "hero" for p in hero_patterns)


def test_pattern_lookup_style_filter_falls_back_when_no_match() -> None:
    """Asking for an obscure style still returns ANY hero pattern, never []."""
    result = lookup_design_pattern_snippets("hero", style_id="nonexistent-style", limit=3)
    assert len(result) >= 1
