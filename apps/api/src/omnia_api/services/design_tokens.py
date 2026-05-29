"""Design tokens for freeform generation (Phase 11, Sprint 1.3).

The old freeform prompt anchored every site to the SAME palette (the first
of the curated set) and let fonts be free text — so two projects looked
identical and the model drifted back to its indigo+violet training default.

This module fixes the *foundation* (palette + font pairing) with **spread**:
the choice is seeded by `project_id`, so a project is stable across re-prompts
but different projects land on different, curated, WCAG-checked combinations.
That is the "freedom in composition, rigidity in the foundation" principle —
we don't fix the layout, we fix the tokens the layout is built from.

Output: a `prompt_block()` (an authoritative palette+font anchor for the
system prompt) and `css_vars()` (a ready `:root{}` snippet the model can paste).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from omnia_api.sections.palettes import (
    CuratedPalette,
    all_palettes,
    palettes_for_vibe,
)

# Curated display+body Google-font pairings. Each pair is known to read well
# for a landing (clear display/body contrast, Cyrillic coverage). The pool is
# intentionally diverse so seeded selection visibly varies site-to-site — the
# whole point of Phase 11 is killing the "every AI site uses Inter" look.
_FONT_PAIRINGS: tuple[tuple[str, str], ...] = (
    ("Space Grotesk", "DM Sans"),
    ("Manrope", "Inter"),
    ("Sora", "Inter"),
    ("Unbounded", "Manrope"),
    ("Plus Jakarta Sans", "Inter"),
    ("Playfair Display", "Karla"),
    ("Fraunces", "Inter"),
    ("Bricolage Grotesque", "DM Sans"),
    ("Archivo", "Archivo"),
    ("Syne", "Inter"),
    ("Onest", "Onest"),
    ("Instrument Serif", "DM Sans"),
    ("Libre Franklin", "Lora"),
    ("Outfit", "Inter"),
    ("Spectral", "Work Sans"),
    ("Cormorant Garamond", "Mulish"),
)

# Industry hint (the project's classified design preset id) → preferred vibe.
# Best-effort: substring match against the preset id; no hit → no narrowing
# (we just pick from the whole curated set). Vibe strings match the `vibe`
# field on CuratedPalette in sections/palettes.py.
_INDUSTRY_VIBE: tuple[tuple[str, str], ...] = (
    ("fintech", "fintech-trust"),
    ("finance", "fintech-trust"),
    ("bank", "fintech-trust"),
    ("saas", "swiss-minimal"),
    ("b2b", "swiss-minimal"),
    ("dev", "linear-dark"),
    ("tech", "apple-tech"),
    ("luxury", "editorial-luxury"),
    ("premium", "editorial-luxury"),
    ("editorial", "editorial-luxury"),
    ("restaurant", "editorial-luxury"),
    ("food", "editorial-luxury"),
    ("beauty", "glassmorphism"),
    ("spa", "glassmorphism"),
    ("creative", "brutalist"),
    ("agency", "brutalist"),
    ("portfolio", "brutalist"),
)


def _seed(project_id: str, salt: str) -> int:
    """Stable, process-independent seed from a project id.

    `hash()` is salted per-process in CPython, so it can't give a project a
    *stable* look across restarts. SHA-256 of the id + a per-axis salt does —
    and the salt decorrelates the palette pick from the font pick so they
    don't move together.
    """
    digest = hashlib.sha256(f"{salt}:{project_id}".encode()).digest()
    return int.from_bytes(digest[:8], "big")


def _google_fonts_url(display: str, body: str) -> str:
    """Build a Google Fonts CSS2 link for the pairing (weights baked in)."""
    fams = []
    seen: set[str] = set()
    for name, weights in ((display, "400;500;600;700"), (body, "400;500;600")):
        if name in seen:
            continue
        seen.add(name)
        fams.append(f"family={name.replace(' ', '+')}:wght@{weights}")
    return "https://fonts.googleapis.com/css2?" + "&".join(fams) + "&display=swap"


@dataclass(frozen=True)
class DesignTokens:
    """A resolved palette + font pairing for one project."""

    palette: CuratedPalette
    display_font: str
    body_font: str
    google_fonts_url: str

    def css_vars(self) -> str:
        """A `:root{}` block the model can paste verbatim."""
        p = self.palette
        return (
            ":root{\n"
            f"  --bg: {p.bg};\n"
            f"  --bg-alt: {p.surface};\n"
            f"  --fg: {p.text};\n"
            f"  --muted: {p.muted};\n"
            f"  --primary: {p.primary};\n"
            f"  --accent: {p.accent};\n"
            f"  --border: {p.border};\n"
            f"  --font-display: '{self.display_font}';\n"
            f"  --font-body: '{self.body_font}';\n"
            "}"
        )

    def prompt_block(self) -> str:
        """Authoritative palette+font anchor for the freeform system prompt.

        Mirrors the imperative shape of prompt_builder._format_palette_anchor
        so the model treats it as a hard constraint, not a suggestion — but
        these tokens are seeded-per-project, not a fixed preset, which is what
        gives sites their visual spread.
        """
        p = self.palette
        return f"""\
ОБЯЗАТЕЛЬНАЯ ПАЛИТРА И ШРИФТЫ (design tokens проекта) — anchor, читай ПЕРЕД остальным промптом.
Это РАЗНЫЕ токены для разных проектов — НЕ скатывайся в дефолтный indigo/violet.

Вайб палитры: {p.vibe} · «{p.name}»

ЦВЕТА — используй ТОЛЬКО эти HEX (вынеси в :root и Tailwind config), никаких других:
  bg     = {p.bg}     bg-alt = {p.surface}
  fg     = {p.text}     muted  = {p.muted}
  primary= {p.primary}     accent = {p.accent}     border = {p.border}

ШРИФТЫ — подключи ИМЕННО эти Google Fonts, без подмен:
  display: {self.display_font}   ·   body: {self.body_font}
  <link rel="stylesheet" href="{self.google_fonts_url}">

Готовый :root (вставь как есть и используй переменные по всей странице):
{self.css_vars()}

КАТЕГОРИЧЕСКИ ЗАПРЕЩЕНЫ (это training-data дефолты, здесь они БРАК):
  • indigo (#4f46e5 / #6366f1 / #818cf8 / indigo-500/600/700)
  • violet / purple (#7c3aed / #8b5cf6 / #a855f7)
  • градиенты from-indigo-* to-violet-*, from-purple-* to-pink-*
  • Inter+Space Grotesk если их нет в паре выше
Любой цвет/шрифт вне этого блока без явной просьбы пользователя — брак."""


def _normalize(text: str | None) -> str:
    return (text or "").strip().lower()


def tokens_for_project(
    project_id: str,
    *,
    vibe: str | None = None,
    dark_mode: bool | None = None,
    industry_hint: str | None = None,
) -> DesignTokens:
    """Resolve seeded design tokens for a project.

    Selection is deterministic per `project_id` (stable across re-prompts and
    restarts) but spread across the curated pool so different projects differ.
    Optional `vibe` / `dark_mode` / `industry_hint` narrow the candidate pool
    first; anything that would empty the pool is ignored (we always return a
    valid palette — R-10 fail-safe).
    """
    pool: list[CuratedPalette] = list(all_palettes())

    if vibe:
        narrowed = list(palettes_for_vibe(vibe))
        if narrowed:
            pool = narrowed
    elif industry_hint:
        hint = _normalize(industry_hint)
        target_vibe = next((v for key, v in _INDUSTRY_VIBE if key in hint), None)
        if target_vibe:
            narrowed = [p for p in pool if p.vibe == target_vibe]
            if narrowed:
                pool = narrowed

    if dark_mode is not None:
        narrowed = [p for p in pool if p.dark_mode == dark_mode]
        if narrowed:
            pool = narrowed

    palette = pool[_seed(project_id, "palette") % len(pool)]
    display, body = _FONT_PAIRINGS[_seed(project_id, "font") % len(_FONT_PAIRINGS)]
    return DesignTokens(
        palette=palette,
        display_font=display,
        body_font=body,
        google_fonts_url=_google_fonts_url(display, body),
    )


__all__ = ["DesignTokens", "tokens_for_project"]
