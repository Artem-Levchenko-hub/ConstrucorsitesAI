"""Section variant registry.

Single source of truth that maps ``type_variant`` strings to their
Pydantic class AND Jinja template path. Used by:

* ``renderer.py`` — picks the template via ``TEMPLATE_FOR[s.type_variant]``.
* ``prompt_builder.py`` — emits ``CATALOG_BLURB`` into the LLM system prompt
  so the model knows exactly which variants exist and what props each takes.
* tests — iterates the registry to guarantee every Pydantic class has a
  matching template on disk.

When a new variant is added in ``ir.py``, append one row here. The
``test_catalog_complete`` test fails loudly if anything is forgotten.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from omnia_api.sections.ir import (
    AboutV1,
    ContactV1,
    CTAV1,
    CTAV2,
    FAQV1,
    FeaturesV1,
    FeaturesV2,
    FooterV1,
    HeaderV1,
    HeroV1,
    HeroV2,
    HeroV3,
    PricingV1,
    PricingV2,
    StatsV1,
    TestimonialsV1,
)

if TYPE_CHECKING:
    from pydantic import BaseModel


# ─── Registry ────────────────────────────────────────────────────────────
# Order matters: this is the order in which the LLM sees variants in the
# system prompt, so the FIRST entry of each type becomes the implicit
# default when the model is uncertain. Pick the safest / most common as
# vN=1 for each section kind.

REGISTRY: dict[str, tuple[type["BaseModel"], str]] = {
    # type_variant         (PydanticClass,      template_path)
    "header.v1":         (HeaderV1,            "header/v1.html.j2"),
    "hero.v1":           (HeroV1,              "hero/v1.html.j2"),
    "hero.v2":           (HeroV2,              "hero/v2.html.j2"),
    "hero.v3":           (HeroV3,              "hero/v3.html.j2"),
    "stats.v1":          (StatsV1,             "stats/v1.html.j2"),
    "features.v1":       (FeaturesV1,          "features/v1.html.j2"),
    "features.v2":       (FeaturesV2,          "features/v2.html.j2"),
    "about.v1":          (AboutV1,             "about/v1.html.j2"),
    "testimonials.v1":   (TestimonialsV1,      "testimonials/v1.html.j2"),
    "pricing.v1":        (PricingV1,           "pricing/v1.html.j2"),
    "pricing.v2":        (PricingV2,           "pricing/v2.html.j2"),
    "faq.v1":            (FAQV1,               "faq/v1.html.j2"),
    "cta.v1":            (CTAV1,               "cta/v1.html.j2"),
    "cta.v2":            (CTAV2,               "cta/v2.html.j2"),
    "contact.v1":        (ContactV1,           "contact/v1.html.j2"),
    "footer.v1":         (FooterV1,            "footer/v1.html.j2"),
}

VARIANT_IDS: list[str] = list(REGISTRY.keys())

TEMPLATE_FOR: dict[str, str] = {vid: path for vid, (_, path) in REGISTRY.items()}

CLASS_FOR: dict[str, type["BaseModel"]] = {vid: cls for vid, (cls, _) in REGISTRY.items()}


# ─── Human-readable catalog blurb for the LLM system prompt ──────────────
# Compact format so the catalog costs ≤ ~600 tokens. The model only needs
# variant_id + 1-line shape description; full schema lives in JSON-Schema
# we attach separately for retry-loop validation.

CATALOG_BLURB: str = """\
КАТАЛОГ СЕКЦИЙ. Выбирай ровно из этого списка — никаких «своих» секций.
Формат вывода — JSON, одна секция = объект с обязательным полем
"type_variant".

# header
header.v1   — sticky top-nav: бренд | links (2-7) | CTA?

# hero  (один на страницу, всегда первый после header)
hero.v1     — split: copy слева, картинка справа. CTA primary + secondary?
hero.v2     — centered, гигантская типографика, gradient-text акцент в headline
hero.v3     — full-bleed mesh / aurora / dark, glow CTA, опц. pill_label

# proof
stats.v1    — 3-6 KPI чисел (value + label)

# features
features.v1 — 3-кол. icon-grid, 3-6 items
features.v2 — alternating rows text↔image, 2-4 items

# narrative
about.v1    — split text + image, опц. reverse=true

# social
testimonials.v1 — 3-кол. quote cards, 2-6 quotes

# commerce
pricing.v1  — 3-tier cards (2-4 tier), middle = featured
pricing.v2  — 2-tier comparison side-by-side

# объяснения
faq.v1      — аккордеон, 3-10 вопросов

# close
cta.v1      — centered band, 1 primary CTA
cta.v2      — split-card с двумя CTA + опц. visual

# action
contact.v1  — split: форма + адрес/телефон/email

# footer (последний)
footer.v1   — 1-4 колонки links + social? + copyright
"""


__all__ = [
    "CATALOG_BLURB",
    "CLASS_FOR",
    "REGISTRY",
    "TEMPLATE_FOR",
    "VARIANT_IDS",
]
