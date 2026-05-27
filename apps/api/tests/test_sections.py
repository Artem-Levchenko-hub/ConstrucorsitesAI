"""Smoke tests for the Section catalog (Phase L1).

These don't try to pin down exact HTML; they enforce the *contract*:

* every registered variant in ``catalog.REGISTRY`` has a matching Jinja
  template on disk (catches forgot-to-add-template regressions);
* every registered variant has a matching Pydantic class with a
  ``type_variant`` literal equal to the registry key;
* a minimal happy-path PageIR validates and renders without error;
* IR validation rejects the obvious bad inputs (unknown variant,
  too-short headline, missing required field, extra forbidden field);
* the renderer is deterministic (same IR → same HTML).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from omnia_api.sections import PageIR, render_page
from omnia_api.sections.catalog import CLASS_FOR, REGISTRY, TEMPLATE_FOR, VARIANT_IDS

_TEMPLATES_DIR = (
    Path(__file__).resolve().parents[1] / "src" / "omnia_api" / "sections" / "templates"
)


# ─── Catalog completeness ────────────────────────────────────────────────


def test_registry_has_at_least_one_of_each_required_type() -> None:
    """Sanity: catalog must cover every section type at least once."""
    required = {"header", "hero", "features", "pricing", "cta", "footer"}
    seen_types = {vid.split(".")[0] for vid in VARIANT_IDS}
    missing = required - seen_types
    assert not missing, f"Catalog missing types: {missing}"


@pytest.mark.parametrize("variant_id", VARIANT_IDS)
def test_every_variant_has_template_file(variant_id: str) -> None:
    template_path = _TEMPLATES_DIR / TEMPLATE_FOR[variant_id]
    assert template_path.is_file(), f"Missing template for {variant_id}: {template_path}"


@pytest.mark.parametrize("variant_id", VARIANT_IDS)
def test_every_class_pins_its_type_variant_literal(variant_id: str) -> None:
    cls = CLASS_FOR[variant_id]
    field = cls.model_fields["type_variant"]
    # `Literal["hero.v1"]` -> default is "hero.v1"; we assert the default
    # matches the registry key so the discriminator and the registry
    # cannot drift out of sync.
    assert field.default == variant_id


def test_registry_keys_unique() -> None:
    assert len(REGISTRY) == len(set(REGISTRY.keys()))


# ─── Happy path render ───────────────────────────────────────────────────


def _full_page_payload() -> dict:
    """One section of each registered variant. Updated when a new variant lands."""
    return {
        "meta": {
            "title": "Smoke Test",
            "description": "Catalog smoke test covering every registered variant.",
        },
        "sections": [
            {
                "type_variant": "header.v1",
                "brand": "Acme",
                "links": [
                    {"label": "Home", "href": "#top"},
                    {"label": "Pricing", "href": "#pricing"},
                ],
            },
            {
                "type_variant": "hero.v1",
                "headline": "Hero V1 split layout",
                "primary_cta": {"label": "Start", "href": "#"},
            },
            {
                "type_variant": "hero.v2",
                "headline": "Hero V2 centered with accent",
                "headline_accent": "accent",
                "primary_cta": {"label": "Go", "href": "#"},
            },
            {
                "type_variant": "hero.v3",
                "headline": "Hero V3 mesh fullbleed",
                "background": "mesh",
                "primary_cta": {"label": "Try", "href": "#"},
            },
            {
                "type_variant": "stats.v1",
                "headline": "Stats row",
                "items": [
                    {"value": "120+", "label": "users"},
                    {"value": "99.9%", "label": "uptime"},
                    {"value": "24/7", "label": "support"},
                ],
            },
            {
                "type_variant": "features.v1",
                "headline": "Features V1 grid",
                "items": [
                    {"icon": "s", "title": "A", "body": "a"},
                    {"icon": "s", "title": "B", "body": "b"},
                    {"icon": "s", "title": "C", "body": "c"},
                ],
            },
            {
                "type_variant": "features.v2",
                "headline": "Features V2 alternating",
                "items": [
                    {"icon": "s", "title": "A", "body": "a"},
                    {"icon": "s", "title": "B", "body": "b"},
                ],
            },
            {
                "type_variant": "about.v1",
                "headline": "About us",
                "body": "first paragraph here.\n\nsecond paragraph too.",
            },
            {
                "type_variant": "testimonials.v1",
                "headline": "Customer reviews",
                "items": [
                    {"quote": "great product overall", "author": "John"},
                    {"quote": "love it so much yes", "author": "Jane", "role": "CEO"},
                ],
            },
            {
                "type_variant": "pricing.v1",
                "headline": "Pricing tiers",
                "tiers": [
                    {"name": "Free", "price": "0", "features": ["a", "b"],
                     "cta": {"label": "Go", "href": "#"}},
                    {"name": "Pro", "price": "990", "features": ["a", "b", "c"],
                     "cta": {"label": "Buy", "href": "#"}, "featured": True},
                ],
            },
            {
                "type_variant": "pricing.v2",
                "headline": "Compare plans",
                "tiers": [
                    {"name": "Free", "price": "0", "features": ["a", "b"],
                     "cta": {"label": "Go", "href": "#"}},
                    {"name": "Pro", "price": "99", "features": ["a", "b", "c"],
                     "cta": {"label": "Buy", "href": "#"}, "featured": True},
                ],
            },
            {
                "type_variant": "faq.v1",
                "headline": "Frequently asked questions",
                "items": [
                    {"question": "Q1?", "answer": "A1."},
                    {"question": "Q2?", "answer": "A2."},
                    {"question": "Q3?", "answer": "A3."},
                ],
            },
            {
                "type_variant": "cta.v1",
                "headline": "Closing CTA band",
                "primary_cta": {"label": "Start", "href": "#"},
            },
            {
                "type_variant": "cta.v2",
                "headline": "Split-card CTA",
                "primary_cta": {"label": "A", "href": "#"},
                "secondary_cta": {"label": "B", "href": "#"},
            },
            {
                "type_variant": "contact.v1",
                "headline": "Contact us",
                "email": "hi@example.com",
                "phone": "+7 999 000",
            },
            {
                "type_variant": "footer.v1",
                "brand": "Acme",
                "columns": [{"heading": "Product",
                             "links": [{"label": "Home", "href": "#"}]}],
                "copyright": "2026 Acme",
            },
        ],
    }


def test_full_page_renders_all_variants() -> None:
    ir = PageIR.model_validate(_full_page_payload())
    html = render_page(ir)
    # very loose contract: doctype + every section's anchor id is present
    assert html.startswith("<!doctype html>")
    for s in ir.sections:
        anchor = s.id or s.type_variant.replace(".", "-")
        assert f'id="{anchor}"' in html, f"Anchor for {s.type_variant} missing"


def test_render_is_deterministic() -> None:
    ir = PageIR.model_validate(_full_page_payload())
    assert render_page(ir) == render_page(ir)


# ─── Validation contract ─────────────────────────────────────────────────


def test_unknown_variant_rejected() -> None:
    payload = {
        "meta": {"title": "Test", "description": "Description for test page here"},
        "sections": [{"type_variant": "hero.v99", "headline": "x", "primary_cta": {"label": "x", "href": "#"}}],
    }
    with pytest.raises(ValidationError):
        PageIR.model_validate(payload)


def test_too_short_headline_rejected() -> None:
    payload = {
        "meta": {"title": "Test", "description": "Description for test page here"},
        "sections": [
            {"type_variant": "hero.v1", "headline": "Hi",
             "primary_cta": {"label": "Go", "href": "#"}},
        ],
    }
    with pytest.raises(ValidationError):
        PageIR.model_validate(payload)


def test_extra_field_forbidden() -> None:
    payload = {
        "meta": {"title": "Test", "description": "Description for test page here"},
        "sections": [
            {"type_variant": "hero.v1", "headline": "Hello world",
             "primary_cta": {"label": "Go", "href": "#"},
             "rogue_field": "should-fail"},
        ],
    }
    with pytest.raises(ValidationError):
        PageIR.model_validate(payload)


def test_missing_required_cta_rejected() -> None:
    payload = {
        "meta": {"title": "Test", "description": "Description for test page here"},
        "sections": [
            {"type_variant": "hero.v1", "headline": "Hello world"},
        ],
    }
    with pytest.raises(ValidationError):
        PageIR.model_validate(payload)
