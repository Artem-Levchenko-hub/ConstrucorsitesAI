"""Tests for the Smart Defaults engine (Phase L8, awwwards-plan #15)."""

from __future__ import annotations

import datetime as _dt
from copy import deepcopy

from omnia_api.sections import PageIR, apply_smart_defaults


# ─── Test fixtures ──────────────────────────────────────────────────────


def _minimal_page() -> dict:
    """A minimal PageIR dict the model could plausibly emit."""
    return {
        "meta": {
            "title": "Acme",
            "description": "Acme — minimal smoke fixture for tests",
        },
        "sections": [
            {
                "type_variant": "header.v1",
                "brand": "Acme",
                "links": [
                    {"label": "Цены", "href": "#pricing-v1"},
                    {"label": "Контакты", "href": "#contact-v1"},
                ],
            },
            {
                "type_variant": "hero.v1",
                "headline": "Hero headline placeholder",
                "primary_cta": {"label": "Старт", "href": "#"},
            },
            {
                "type_variant": "pricing.v1",
                "headline": "Цены",
                "tiers": [
                    {"name": "Free", "price": "0", "features": ["a", "b"],
                     "cta": {"label": "Go", "href": "#"}},
                    {"name": "Pro", "price": "990", "features": ["a", "b", "c"],
                     "cta": {"label": "Buy", "href": "#"}},
                    {"name": "Enterprise", "price": "9990",
                     "features": ["a", "b", "c", "d"],
                     "cta": {"label": "Sales", "href": "#"}},
                ],
            },
            {
                "type_variant": "contact.v1",
                "headline": "Связаться с нами",
            },
            {
                "type_variant": "footer.v1",
                "brand": "Acme",
                "columns": [{"heading": "Продукт",
                             "links": [{"label": "Home", "href": "#top"}]}],
                # "©" without a year triggers the copyright rule — schema
                # requires at least 1 char so we use this minimal marker
                # the model frequently emits in raw output.
                "copyright": "©",
            },
        ],
    }


# ─── Palette rule ───────────────────────────────────────────────────────


def test_palette_completion_from_preset() -> None:
    ir = PageIR.model_validate(_minimal_page())
    # By default theme.primary == #6366f1 (schema default).
    assert ir.theme.primary.lower() == "#6366f1"
    out = apply_smart_defaults(ir, preset_id="saas-product")
    assert out.theme.primary == "#2563EB"
    assert out.theme.accent == "#10B981"


def test_palette_keeps_user_choice() -> None:
    payload = _minimal_page()
    payload["theme"] = {"primary": "#FF6B35", "accent": "#00F5D4"}
    ir = PageIR.model_validate(payload)
    out = apply_smart_defaults(ir, preset_id="saas-product")
    assert out.theme.primary == "#FF6B35"
    assert out.theme.accent == "#00F5D4"


def test_palette_skipped_when_no_preset_id() -> None:
    ir = PageIR.model_validate(_minimal_page())
    out = apply_smart_defaults(ir, preset_id=None)
    # Stays at schema default — we don't fabricate a preset.
    assert out.theme.primary.lower() == "#6366f1"


# ─── CTA href anchoring ─────────────────────────────────────────────────


def test_cta_href_anchored_to_contact_section() -> None:
    ir = PageIR.model_validate(_minimal_page())
    out = apply_smart_defaults(ir, preset_id=None)
    # Hero's primary_cta had href="#" → must be redirected to #contact-v1.
    hero = next(s for s in out.sections if s.type_variant == "hero.v1")
    assert hero.primary_cta.href == "#contact-v1"
    # Pricing tier CTAs were "#" too — same redirect.
    pricing = next(s for s in out.sections if s.type_variant == "pricing.v1")
    for tier in pricing.tiers:
        assert tier.cta.href == "#contact-v1"


def test_cta_href_no_anchor_fallback_to_top() -> None:
    """Page with no contact/cta/pricing sections — fallback to #top."""
    payload = {
        "meta": {"title": "Min", "description": "minimal page no conversion"},
        "sections": [
            {"type_variant": "header.v1", "brand": "X",
             "links": [
                 {"label": "Home", "href": "#top"},
                 {"label": "About", "href": "#about"},
             ]},
            {"type_variant": "hero.v1", "headline": "Hello world",
             "primary_cta": {"label": "Go", "href": "#"}},
            {"type_variant": "footer.v1", "brand": "X",
             "columns": [{"heading": "P",
                          "links": [{"label": "Home", "href": "#top"}]}],
             "copyright": "© 2026 X"},
        ],
    }
    ir = PageIR.model_validate(payload)
    out = apply_smart_defaults(ir, preset_id=None)
    hero = next(s for s in out.sections if s.type_variant == "hero.v1")
    assert hero.primary_cta.href == "#top"


def test_cta_href_dangling_anchor_redirected() -> None:
    payload = _minimal_page()
    # Point hero CTA to a section that doesn't exist.
    payload["sections"][1]["primary_cta"]["href"] = "#nonexistent-section"
    ir = PageIR.model_validate(payload)
    out = apply_smart_defaults(ir, preset_id=None)
    hero = next(s for s in out.sections if s.type_variant == "hero.v1")
    assert hero.primary_cta.href == "#contact-v1"


def test_cta_href_valid_anchor_preserved() -> None:
    """Anchor pointing to a real section must not be touched."""
    payload = _minimal_page()
    payload["sections"][1]["primary_cta"]["href"] = "#pricing-v1"
    ir = PageIR.model_validate(payload)
    out = apply_smart_defaults(ir, preset_id=None)
    hero = next(s for s in out.sections if s.type_variant == "hero.v1")
    assert hero.primary_cta.href == "#pricing-v1"


# ─── Pricing featured tier ──────────────────────────────────────────────


def test_pricing_auto_features_middle_tier() -> None:
    ir = PageIR.model_validate(_minimal_page())
    out = apply_smart_defaults(ir, preset_id=None)
    pricing = next(s for s in out.sections if s.type_variant == "pricing.v1")
    flags = [t.featured for t in pricing.tiers]
    # 3 tiers, middle should be featured.
    assert flags == [False, True, False]


def test_pricing_keeps_user_featured() -> None:
    payload = _minimal_page()
    payload["sections"][2]["tiers"][0]["featured"] = True
    ir = PageIR.model_validate(payload)
    out = apply_smart_defaults(ir, preset_id=None)
    pricing = next(s for s in out.sections if s.type_variant == "pricing.v1")
    flags = [t.featured for t in pricing.tiers]
    assert flags == [True, False, False]  # untouched


def test_pricing_two_tiers_features_last() -> None:
    payload = _minimal_page()
    payload["sections"][2]["tiers"] = payload["sections"][2]["tiers"][:2]
    ir = PageIR.model_validate(payload)
    out = apply_smart_defaults(ir, preset_id=None)
    pricing = next(s for s in out.sections if s.type_variant == "pricing.v1")
    flags = [t.featured for t in pricing.tiers]
    assert flags == [False, True]


# ─── Footer copyright ───────────────────────────────────────────────────


def test_footer_copyright_filled_when_empty() -> None:
    ir = PageIR.model_validate(_minimal_page())
    out = apply_smart_defaults(ir, preset_id=None)
    footer = next(s for s in out.sections if s.type_variant == "footer.v1")
    year = _dt.datetime.now(_dt.UTC).year
    assert footer.copyright.startswith(f"© {year}")
    assert "Acme" in footer.copyright


def test_footer_copyright_preserved_when_set() -> None:
    payload = _minimal_page()
    payload["sections"][-1]["copyright"] = "© 2025 Acme Corp. All rights reserved."
    ir = PageIR.model_validate(payload)
    out = apply_smart_defaults(ir, preset_id=None)
    footer = next(s for s in out.sections if s.type_variant == "footer.v1")
    assert footer.copyright == "© 2025 Acme Corp. All rights reserved."


# ─── Favicon emoji ──────────────────────────────────────────────────────


def test_favicon_default_replaced_by_preset() -> None:
    ir = PageIR.model_validate(_minimal_page())
    assert ir.meta.favicon_emoji == "🚀"
    out = apply_smart_defaults(ir, preset_id="wellness-casual")
    assert out.meta.favicon_emoji == "✦"


def test_favicon_keeps_user_choice() -> None:
    payload = _minimal_page()
    payload["meta"]["favicon_emoji"] = "🐢"
    ir = PageIR.model_validate(payload)
    out = apply_smart_defaults(ir, preset_id="wellness-casual")
    assert out.meta.favicon_emoji == "🐢"


# ─── Dark mode coercion ─────────────────────────────────────────────────


def test_dark_mode_coerced_from_hero_v3() -> None:
    payload = _minimal_page()
    payload["sections"][1] = {
        "type_variant": "hero.v3",
        "background": "mesh",
        "headline": "Dark hero",
        "primary_cta": {"label": "Go", "href": "#contact-v1"},
    }
    ir = PageIR.model_validate(payload)
    assert ir.theme.dark_mode is False
    out = apply_smart_defaults(ir, preset_id=None)
    assert out.theme.dark_mode is True


def test_dark_mode_background_coerced_when_inconsistent() -> None:
    payload = _minimal_page()
    payload["theme"] = {"dark_mode": True, "background": "#FFFFFF"}
    payload["sections"][1] = {
        "type_variant": "hero.v3",
        "background": "dark",
        "headline": "Dark hero",
        "primary_cta": {"label": "Go", "href": "#contact-v1"},
    }
    ir = PageIR.model_validate(payload)
    out = apply_smart_defaults(ir, preset_id=None)
    assert out.theme.background.upper() == "#0A0A0A"


# ─── Anchor uniqueness ──────────────────────────────────────────────────


def test_anchor_id_dedup() -> None:
    payload = _minimal_page()
    # Two pricing-like sections sharing the same explicit id
    payload["sections"].insert(3, {
        "type_variant": "features.v1",
        "id": "duplicate-id",
        "headline": "First Features Block",
        "items": [
            {"icon": "s", "title": "A", "body": "a"},
            {"icon": "s", "title": "B", "body": "b"},
            {"icon": "s", "title": "C", "body": "c"},
        ],
    })
    payload["sections"].insert(4, {
        "type_variant": "features.v2",
        "id": "duplicate-id",
        "headline": "Second Features Block",
        "items": [
            {"icon": "s", "title": "A", "body": "a"},
            {"icon": "s", "title": "B", "body": "b"},
        ],
    })
    ir = PageIR.model_validate(payload)
    out = apply_smart_defaults(ir, preset_id=None)
    ids = [s.id for s in out.sections if s.id]
    # First keeps the original id, second is suffixed.
    assert "duplicate-id" in ids
    assert "duplicate-id-2" in ids


# ─── Invariants ─────────────────────────────────────────────────────────


def test_idempotent() -> None:
    ir = PageIR.model_validate(_minimal_page())
    once = apply_smart_defaults(ir, preset_id="saas-product")
    twice = apply_smart_defaults(once, preset_id="saas-product")
    assert once.model_dump() == twice.model_dump()


def test_no_mutation_of_input() -> None:
    payload = _minimal_page()
    ir = PageIR.model_validate(payload)
    before_dump = ir.model_dump()
    apply_smart_defaults(ir, preset_id="saas-product")
    after_dump = ir.model_dump()
    assert before_dump == after_dump
    # Also: payload dict the caller built must not be touched.
    assert payload == _minimal_page()


def test_full_complete_page_passes_through_unchanged() -> None:
    """If every field is meaningfully populated, defaults shouldn't move."""
    payload = {
        "meta": {
            "title": "Real Title", "description": "Real description for tests",
            "favicon_emoji": "🐢",
        },
        "theme": {
            "primary": "#FF6B35", "accent": "#00F5D4",
            "background": "#FFFFFF", "text": "#0F172A",
            "neutral": "stone", "font_display": "Inter Display",
            "font_body": "Inter", "dark_mode": False,
        },
        "sections": [
            {"type_variant": "header.v1", "brand": "Brand",
             "links": [
                 {"label": "Pricing", "href": "#pricing-v1"},
                 {"label": "Hero", "href": "#hero"},
             ]},
            {"type_variant": "hero.v1", "id": "hero",
             "headline": "Real hero headline",
             "primary_cta": {"label": "Buy", "href": "#pricing-v1"}},
            {"type_variant": "pricing.v1", "id": "pricing-v1",
             "headline": "Real pricing",
             "tiers": [
                 {"name": "F", "price": "0", "features": ["a", "b"],
                  "cta": {"label": "G", "href": "#hero"}},
                 {"name": "P", "price": "9", "features": ["a", "b", "c"],
                  "cta": {"label": "B", "href": "#hero"}, "featured": True},
             ]},
            {"type_variant": "footer.v1", "brand": "Brand",
             "columns": [{"heading": "P",
                          "links": [{"label": "H", "href": "#top"}]}],
             "copyright": "© 2025 Brand. All rights reserved."},
        ],
    }
    expected = deepcopy(payload)
    ir = PageIR.model_validate(payload)
    out = apply_smart_defaults(ir, preset_id="saas-product")
    # Pydantic round-trip can normalise None values; compare key fields.
    assert out.theme.primary == "#FF6B35"
    assert out.theme.accent == "#00F5D4"
    assert out.meta.favicon_emoji == "🐢"
    footer = next(s for s in out.sections if s.type_variant == "footer.v1")
    assert footer.copyright == "© 2025 Brand. All rights reserved."
    pricing = next(s for s in out.sections if s.type_variant == "pricing.v1")
    assert [t.featured for t in pricing.tiers] == [False, True]
    # Original payload still equals its initial state.
    assert payload == expected
