"""Section catalog — JSON IR + deterministic renderer.

Phase L (Sprint 2). Implements item #2/#3 of awwwards plan:
**Component-as-primitive + JSON IR**. Cheap LLMs no longer write JSX —
they emit `{type_variant: "hero.v3", props: {...}}` and a deterministic
Jinja renderer compiles it to HTML. Saves ~70% of tokens on layout
markup, eliminates class hallucinations, gives every output a known
visual ceiling.

Catalog totals (MVP launch — Phase L1):
  hero          v1..v3  (3)
  features      v1..v2  (2)
  pricing       v1..v2  (2)
  cta           v1..v2  (2)
  testimonials  v1      (1)
  faq           v1      (1)
  header        v1      (1)
  footer        v1      (1)
  contact       v1      (1)
  stats         v1      (1)
  about         v1      (1)
  ─────────────────────
  TOTAL                 16 variants

Covers ~80% of landing-page shapes. Extend by adding Pydantic class
+ Jinja template; registry auto-discovers via subclasses.
"""

from omnia_api.sections.ir import (
    CTA,
    Feature,
    PageIR,
    PageMeta,
    PricingTier,
    Section,
    TestimonialQuote,
    Theme,
)
from omnia_api.sections.renderer import render_page

__all__ = [
    "CTA",
    "Feature",
    "PageIR",
    "PageMeta",
    "PricingTier",
    "Section",
    "TestimonialQuote",
    "Theme",
    "render_page",
]
