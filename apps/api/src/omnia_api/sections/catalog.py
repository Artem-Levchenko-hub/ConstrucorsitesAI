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
    BandV1,
    ContactV1,
    CTAV1,
    CTAV2,
    FAQV1,
    FeaturesV1,
    FeaturesV2,
    FeaturesV3,
    FooterV1,
    HeaderV1,
    HeroV1,
    HeroV2,
    HeroV3,
    HeroV4,
    HeroV5,
    HeroV6,
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
    "hero.v4":           (HeroV4,              "hero/v4.html.j2"),
    "hero.v5":           (HeroV5,              "hero/v5.html.j2"),
    "hero.v6":           (HeroV6,              "hero/v6.html.j2"),
    "stats.v1":          (StatsV1,             "stats/v1.html.j2"),
    "features.v1":       (FeaturesV1,          "features/v1.html.j2"),
    "features.v2":       (FeaturesV2,          "features/v2.html.j2"),
    "features.v3":       (FeaturesV3,          "features/v3.html.j2"),
    "about.v1":          (AboutV1,             "about/v1.html.j2"),
    "band.v1":           (BandV1,              "band/v1.html.j2"),
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
Одна секция = JSON-объект с обязательным полем "type_variant". Имена полей —
СТРОГО как ниже (схема жёсткая: лишнее или переименованное поле = invalid → retry).

ОБЩИЕ ПРАВИЛА ПОЛЕЙ (частые ошибки — НЕ повторяй):
• Любая ссылка/навигация/соцсеть = объект {"label","href"}. НЕ "url", НЕ "platform", НЕ "name", НЕ "title".
• Кнопка (CTA) = {"label","href","style"}, style ∈ primary|ghost|outline.
• header: имя бренда = строка "brand". НЕ "logo", НЕ "logo_text".
• footer.columns[*]: заголовок колонки = "heading". НЕ "title". footer.social[*] = {"label","href"}.
• contact: плоские поля "address"/"phone"/"email" (строки). НЕ объект "contact_info".
• "background" секции = РОВНО один из: canvas|muted|mesh|aurora|dark|grain. НЕ tailwind-класс ("bg-white"/"bg-zinc-50" = invalid). Цвета фона страницы задаются в theme.background (HEX), а не тут.
• Опционально у ЛЮБОЙ секции: "id" (a-z0-9-), "background" (enum выше), "motion" ∈ reveal|reveal-blur|fade-up|none.
• Поле с "?" — опциональное; без "?" — обязательное. НЕ добавляй полей вне списка секции.
• РАЗНООБРАЗЬ РАСКЛАДКУ (иначе «шаблонно»): не лепи hero.v1+features.v1 на каждом сайте. Для нестандартного вида бери hero.v4 (сплит-экран) / hero.v5 (крупная типографика), features.v3 (бенто), band.v1 (тёмная полоса-ритм между светлыми секциями). Под индустрию/референс подбирай форму, а не дефолт.

# header (первая секция)
header.v1   {brand, brand_href?, links:[{label,href}] ×2-7, cta?:{label,href,style}}

# hero (один, сразу после header)
hero.v1     {eyebrow?, headline, subheadline?, primary_cta:{label,href,style}, secondary_cta?, image_url?, image_alt?}
hero.v2     {eyebrow?, headline, headline_accent?, subheadline?, primary_cta:{label,href,style}, secondary_cta?}
hero.v3     {background:mesh|aurora|dark, eyebrow?, headline, subheadline?, primary_cta:{label,href,style}, secondary_cta?, pill_label?}
hero.v4     {panel_side?:left|right, eyebrow?, headline, subheadline?, primary_cta:{label,href,style}, secondary_cta?, image_url?, image_alt?}   (сплит-экран: копия | визуал во всю высоту)
hero.v5     {eyebrow?, headline, subheadline?, primary_cta:{label,href,style}, secondary_cta?}   (огромная типографика во весь экран, без картинки)
hero.v6     {bg_photo, overlay?:dark|darker|gradient, eyebrow?, headline, subheadline?, primary_cta:{label,href,style}, secondary_cta?}   (фон — РЕАЛЬНОЕ фото по теме; bg_photo = англ. ключевики, напр. "sushi restaurant interior")

# proof
stats.v1    {eyebrow?, headline?, items:[{value,label}] ×3-6}

# features
features.v1 {eyebrow?, headline, subheadline?, items:[{icon,title,body}] ×3-6}   (icon = heroicons-имя, напр. "wrench")
features.v2 {eyebrow?, headline, subheadline?, items:[{icon,title,body}] ×2-4}
features.v3 {eyebrow?, headline, subheadline?, items:[{icon,title,body,size?:s|m|l}] ×3-6}   (бенто-сетка разнокалиберных ячеек, часть — тёмные)

# narrative
about.v1    {eyebrow?, headline, body, image_url?, image_alt?, reverse?}
band.v1     {background?:dark|mesh|aurora, eyebrow?, headline, subheadline?, primary_cta?}   (полноэкранная инверт-полоса-заявление; чередуй светлые/тёмные секции для ритма)

# social
testimonials.v1 {eyebrow?, headline, items:[{quote,author,role?,avatar_url?}] ×2-6}

# commerce
pricing.v1  {eyebrow?, headline, subheadline?, tiers:[{name,price,period?,features:[строки] ×2-10,cta:{label,href,style},featured?}] ×2-4}
pricing.v2  {eyebrow?, headline, subheadline?, tiers:[…как pricing.v1…] ровно 2}

# объяснения
faq.v1      {eyebrow?, headline, items:[{question,answer}] ×3-10}

# close
cta.v1      {headline, subheadline?, primary_cta:{label,href,style}}
cta.v2      {headline, subheadline?, primary_cta:{label,href,style}, secondary_cta?, image_url?}

# action
contact.v1  {eyebrow?, headline, subheadline?, address?, phone?, email?, form_cta_label}

# footer (последняя)
footer.v1   {brand, tagline?, columns:[{heading, links:[{label,href}]}] ×1-4, social?:[{label,href}], copyright}
"""


__all__ = [
    "CATALOG_BLURB",
    "CLASS_FOR",
    "REGISTRY",
    "TEMPLATE_FOR",
    "VARIANT_IDS",
]
