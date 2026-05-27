"""Pydantic IR schemas for the Section catalog.

The LLM emits one ``PageIR`` JSON document per generation; the renderer
converts it deterministically to HTML. **No HTML/CSS in the LLM output.**

Schema design:
* ``type_variant`` is the discriminator on every section. Values look like
  ``"hero.v1"``, ``"features.v2"``. One flat enum keeps the union shallow
  and Pydantic-fast.
* ``_SectionBase`` carries cross-cutting knobs (anchor id, background,
  motion). Variants pin extra props specific to their template.
* ``extra="forbid"`` everywhere — junk fields raise instead of silently
  surviving. Required for the retry-loop in #9 to give the model a clean
  error message.
* List-length constraints are intentionally tight (Hero=0 image OR exactly
  1; Features 3-6 items; Pricing 2-4 tiers; FAQ 3-10 questions). Out-of-range
  → ValidationError → retry. Forces the model to make a real layout choice
  rather than dumping arbitrary counts.

To add a variant:
1. Add Pydantic class below with a unique ``Literal["foo.vN"]`` discriminator.
2. Drop it into the ``Section`` union.
3. Add ``templates/foo/vN.html.j2`` next to its siblings.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field


# ─── Reusable primitives ──────────────────────────────────────────────────

class CTA(BaseModel):
    """Call-to-action button. ``href`` must be a working target — the
    link_validator service refuses dead anchors."""

    model_config = ConfigDict(extra="forbid")
    label: str = Field(min_length=1, max_length=40)
    href: str = Field(min_length=1, max_length=200)
    style: Literal["primary", "ghost", "outline"] = "primary"


class Feature(BaseModel):
    model_config = ConfigDict(extra="forbid")
    icon: str = Field(
        min_length=1,
        max_length=40,
        description="heroicons name (e.g. 'sparkles') OR omnia-kit svg slug",
    )
    title: str = Field(min_length=1, max_length=80)
    body: str = Field(min_length=1, max_length=400)


class PricingTier(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=40)
    price: str = Field(min_length=1, max_length=40, description="e.g. '1 200 ₽/мес'")
    period: str | None = Field(default=None, max_length=40)
    features: list[str] = Field(min_length=2, max_length=10)
    cta: CTA
    featured: bool = False


class TestimonialQuote(BaseModel):
    model_config = ConfigDict(extra="forbid")
    quote: str = Field(min_length=10, max_length=600)
    author: str = Field(min_length=1, max_length=80)
    role: str | None = Field(default=None, max_length=80)
    avatar_url: str | None = None


class FAQItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question: str = Field(min_length=3, max_length=200)
    answer: str = Field(min_length=3, max_length=1000)


class NavLink(BaseModel):
    model_config = ConfigDict(extra="forbid")
    label: str = Field(min_length=1, max_length=40)
    href: str = Field(min_length=1, max_length=200)


class StatItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    value: str = Field(min_length=1, max_length=20, description="e.g. '120+' / '99.9%'")
    label: str = Field(min_length=1, max_length=80)


class FooterColumn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    heading: str = Field(min_length=1, max_length=40)
    links: list[NavLink] = Field(min_length=1, max_length=8)


# ─── Theme / page-level ──────────────────────────────────────────────────

class Theme(BaseModel):
    """Tokens applied at the page root. Variants read them via CSS vars.

    Default tokens match the ``blank`` template's omnia-kit base so a
    blank PageIR still renders something coherent."""

    model_config = ConfigDict(extra="forbid")
    primary: str = Field(default="#6366f1", pattern=r"^#[0-9A-Fa-f]{6}$")
    accent: str = Field(default="#ec4899", pattern=r"^#[0-9A-Fa-f]{6}$")
    neutral: Literal["slate", "zinc", "stone", "gray", "neutral"] = "slate"
    background: str = Field(default="#ffffff", pattern=r"^#[0-9A-Fa-f]{6}$")
    text: str = Field(default="#0f172a", pattern=r"^#[0-9A-Fa-f]{6}$")
    font_display: str = Field(default="Space Grotesk", max_length=60)
    font_body: str = Field(default="DM Sans", max_length=60)
    dark_mode: bool = False


class PageMeta(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=3, max_length=120)
    description: str = Field(min_length=10, max_length=300)
    lang: str = Field(default="ru", min_length=2, max_length=10)
    og_image: str | None = None
    favicon_emoji: str | None = Field(default="🚀", max_length=4)


# ─── Section base ────────────────────────────────────────────────────────

class _SectionBase(BaseModel):
    """Common knobs across every section."""

    model_config = ConfigDict(extra="forbid")
    id: str | None = Field(
        default=None,
        max_length=60,
        pattern=r"^[a-z0-9-]+$",
        description="Anchor id; auto-derived from type_variant if omitted.",
    )
    background: Literal["canvas", "muted", "mesh", "aurora", "dark", "grain"] = "canvas"
    motion: Literal["reveal", "reveal-blur", "fade-up", "none"] = "reveal"


# ─── Hero variants ───────────────────────────────────────────────────────

class HeroV1(_SectionBase):
    """Split layout: copy left, product image right. The default workhorse."""

    type_variant: Literal["hero.v1"] = "hero.v1"
    eyebrow: str | None = Field(default=None, max_length=60)
    headline: str = Field(min_length=4, max_length=120)
    subheadline: str | None = Field(default=None, max_length=300)
    primary_cta: CTA
    secondary_cta: CTA | None = None
    image_url: str | None = None
    image_alt: str | None = Field(default=None, max_length=200)


class HeroV2(_SectionBase):
    """Centered, oversized typography. Apple/Linear-grade headline focus."""

    type_variant: Literal["hero.v2"] = "hero.v2"
    eyebrow: str | None = Field(default=None, max_length=60)
    headline: str = Field(min_length=4, max_length=120)
    headline_accent: str | None = Field(
        default=None,
        max_length=60,
        description="Word(s) from headline to highlight via gradient-text.",
    )
    subheadline: str | None = Field(default=None, max_length=300)
    primary_cta: CTA
    secondary_cta: CTA | None = None


class HeroV3(_SectionBase):
    """Full-bleed mesh / aurora background, dark surface, glow CTA.
    Built for SaaS / fintech / dev-tool vibes."""

    type_variant: Literal["hero.v3"] = "hero.v3"
    background: Literal["mesh", "aurora", "dark"] = "mesh"
    eyebrow: str | None = Field(default=None, max_length=60)
    headline: str = Field(min_length=4, max_length=120)
    subheadline: str | None = Field(default=None, max_length=300)
    primary_cta: CTA
    secondary_cta: CTA | None = None
    pill_label: str | None = Field(
        default=None,
        max_length=60,
        description="Small badge above the headline ('🚀 v2.0 launched').",
    )


# ─── Features ────────────────────────────────────────────────────────────

class FeaturesV1(_SectionBase):
    """3-column grid with SVG icons. Most common features layout."""

    type_variant: Literal["features.v1"] = "features.v1"
    eyebrow: str | None = Field(default=None, max_length=60)
    headline: str = Field(min_length=4, max_length=120)
    subheadline: str | None = Field(default=None, max_length=300)
    items: list[Feature] = Field(min_length=3, max_length=6)


class FeaturesV2(_SectionBase):
    """Alternating rows: text↔image, swap side per item."""

    type_variant: Literal["features.v2"] = "features.v2"
    eyebrow: str | None = Field(default=None, max_length=60)
    headline: str = Field(min_length=4, max_length=120)
    subheadline: str | None = Field(default=None, max_length=300)
    items: list[Feature] = Field(min_length=2, max_length=4)


# ─── Pricing ─────────────────────────────────────────────────────────────

class PricingV1(_SectionBase):
    """3-tier cards. Middle tier featured by default."""

    type_variant: Literal["pricing.v1"] = "pricing.v1"
    eyebrow: str | None = Field(default=None, max_length=60)
    headline: str = Field(min_length=4, max_length=120)
    subheadline: str | None = Field(default=None, max_length=300)
    tiers: list[PricingTier] = Field(min_length=2, max_length=4)


class PricingV2(_SectionBase):
    """2-tier comparison: free vs pro, side by side, feature parity grid."""

    type_variant: Literal["pricing.v2"] = "pricing.v2"
    eyebrow: str | None = Field(default=None, max_length=60)
    headline: str = Field(min_length=4, max_length=120)
    subheadline: str | None = Field(default=None, max_length=300)
    tiers: list[PricingTier] = Field(min_length=2, max_length=2)


# ─── CTA ─────────────────────────────────────────────────────────────────

class CTAV1(_SectionBase):
    """Centered band with one primary CTA. Closing-the-page block."""

    type_variant: Literal["cta.v1"] = "cta.v1"
    headline: str = Field(min_length=4, max_length=120)
    subheadline: str | None = Field(default=None, max_length=300)
    primary_cta: CTA


class CTAV2(_SectionBase):
    """Split-card: copy left + visual right, dual CTA."""

    type_variant: Literal["cta.v2"] = "cta.v2"
    headline: str = Field(min_length=4, max_length=120)
    subheadline: str | None = Field(default=None, max_length=300)
    primary_cta: CTA
    secondary_cta: CTA | None = None
    image_url: str | None = None


# ─── Testimonials ────────────────────────────────────────────────────────

class TestimonialsV1(_SectionBase):
    """3-column quote cards with avatar + role."""

    type_variant: Literal["testimonials.v1"] = "testimonials.v1"
    eyebrow: str | None = Field(default=None, max_length=60)
    headline: str = Field(min_length=4, max_length=120)
    items: list[TestimonialQuote] = Field(min_length=2, max_length=6)


# ─── FAQ ─────────────────────────────────────────────────────────────────

class FAQV1(_SectionBase):
    """Accordion. Hooks into omnia-kit .faq-item/.faq-question/.faq-answer."""

    type_variant: Literal["faq.v1"] = "faq.v1"
    eyebrow: str | None = Field(default=None, max_length=60)
    headline: str = Field(min_length=4, max_length=120)
    items: list[FAQItem] = Field(min_length=3, max_length=10)


# ─── Header ──────────────────────────────────────────────────────────────

class HeaderV1(_SectionBase):
    """Sticky top nav: brand left, links center, CTA right."""

    type_variant: Literal["header.v1"] = "header.v1"
    brand: str = Field(min_length=1, max_length=40)
    brand_href: str = Field(default="#top", max_length=200)
    links: list[NavLink] = Field(min_length=2, max_length=7)
    cta: CTA | None = None


# ─── Footer ──────────────────────────────────────────────────────────────

class FooterV1(_SectionBase):
    """Multi-column footer: brand block + link columns + social row + copyright."""

    type_variant: Literal["footer.v1"] = "footer.v1"
    brand: str = Field(min_length=1, max_length=40)
    tagline: str | None = Field(default=None, max_length=200)
    columns: list[FooterColumn] = Field(min_length=1, max_length=4)
    social: list[NavLink] | None = None
    copyright: str = Field(min_length=1, max_length=200)


# ─── Contact ─────────────────────────────────────────────────────────────

class ContactV1(_SectionBase):
    """Split layout: contact form left, address/phone/email right."""

    type_variant: Literal["contact.v1"] = "contact.v1"
    eyebrow: str | None = Field(default=None, max_length=60)
    headline: str = Field(min_length=4, max_length=120)
    subheadline: str | None = Field(default=None, max_length=300)
    address: str | None = Field(default=None, max_length=200)
    phone: str | None = Field(default=None, max_length=40)
    email: str | None = Field(default=None, max_length=80)
    form_cta_label: str = Field(default="Отправить", min_length=1, max_length=40)


# ─── Stats ───────────────────────────────────────────────────────────────

class StatsV1(_SectionBase):
    """KPI row — 3-6 numeric proofs."""

    type_variant: Literal["stats.v1"] = "stats.v1"
    eyebrow: str | None = Field(default=None, max_length=60)
    headline: str | None = Field(default=None, max_length=120)
    items: list[StatItem] = Field(min_length=3, max_length=6)


# ─── About ───────────────────────────────────────────────────────────────

class AboutV1(_SectionBase):
    """Split text + image. Use ``reverse=True`` to put image on the left."""

    type_variant: Literal["about.v1"] = "about.v1"
    eyebrow: str | None = Field(default=None, max_length=60)
    headline: str = Field(min_length=4, max_length=120)
    body: str = Field(min_length=20, max_length=1500)
    image_url: str | None = None
    image_alt: str | None = Field(default=None, max_length=200)
    reverse: bool = False


# ─── Discriminated union ─────────────────────────────────────────────────

Section = Annotated[
    HeroV1 | HeroV2 | HeroV3
    | FeaturesV1 | FeaturesV2
    | PricingV1 | PricingV2
    | CTAV1 | CTAV2
    | TestimonialsV1
    | FAQV1
    | HeaderV1
    | FooterV1
    | ContactV1
    | StatsV1
    | AboutV1,
    Field(discriminator="type_variant"),
]


# ─── Page top-level ──────────────────────────────────────────────────────

class PageIR(BaseModel):
    """The whole page. One JSON document per generation."""

    model_config = ConfigDict(extra="forbid")
    meta: PageMeta
    theme: Theme = Field(default_factory=Theme)
    sections: list[Section] = Field(min_length=1, max_length=20)
