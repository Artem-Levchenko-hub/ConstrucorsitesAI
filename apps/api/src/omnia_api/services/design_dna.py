"""Per-project Design DNA for ENTITY/agent apps — kills "дизайн одинаковый".

Freeform sites already vary (design_tokens seeds a curated palette + font pairing
per project). Entity/agent apps did NOT: the only brand hook (app_theme) sets just
``--primary`` and needs ``globals.css`` in the build files — but the agent never
writes globals.css (it is a FIXED, baked file), so every entity app shipped the
template default → all looked the same.

This module reuses the SAME seeded tokens (curated + WCAG-checked + stable per
project) and injects a distinct identity — accent color + corner radius — as a
trailing ``:root{}`` override appended to the container's baked ``globals.css``.

SAFE BY CONSTRUCTION: it appends ONLY a normal ``:root{}`` rule at the END of the
file. It does NOT use ``@import`` — a font ``@import`` placed anywhere but the very
top fails Turbopack's "@import must precede all rules" (the prod breakage 2026-06-26
this module CAUSED and now also cleans up). It touches only safe brand knobs
(``--primary`` / ``--accent`` / ``--ring`` / ``--radius``), never the canvas neutrals,
so the dark-canvas specificity contract (see app_theme.py / globals.css) stays intact.

Custom fonts (the bigger lever) are loaded separately via a <link> in layout — NOT
via a CSS @import — see the follow-up. Pure + idempotent: re-running refreshes the
managed block, never stacks.
"""

from __future__ import annotations

import hashlib
import re

from omnia_api.services.design_tokens import tokens_for_project

MARKER = "/* omnia:design-dna */"

# Our managed block: the marker + the one-or-more :root/:root.dark blocks after it.
_BLOCK_RE = re.compile(
    re.escape(MARKER) + r"\s*\n(?::root[^{]*\{[^}]*\}\s*)+", re.MULTILINE
)
# Clean up ANY google-fonts @import — including a prior BROKEN injection that landed
# mid-file and fails Turbopack's "@import must precede all rules" CSS parse.
_FONT_IMPORT_RE = re.compile(
    r"(?m)^[ \t]*@import url\(['\"]?https://fonts\.googleapis\.com[^\n]*\n"
)

# Seeded corner radius — a second, cheap axis of visible variation beyond colour.
_RADII = ("0.25rem", "0.375rem", "0.5rem", "0.625rem", "0.75rem", "1rem")

# Extra per-project axes fed to the AGENT (layout + typographic personality), so
# apps differ in more than colour — the «дизайн всегда одинаковый» complaint.
_DENSITY = (
    "компактная — плотные отступы (p-2/p-3, gap-2), много на экране",
    "сбалансированная — средние отступы (p-4, gap-3)",
    "просторная — много воздуха (p-6/p-8, gap-5/gap-6), крупные зоны",
)
_HEADINGS = (
    "крупные жирные заголовки, плотный трекинг (text-2xl/3xl font-bold tracking-tight)",
    "лёгкие тонкие заголовки, обычный трекинг (font-medium)",
    "заголовки КАПСОМ с широким трекингом (uppercase tracking-wide text-sm font-semibold)",
    "контрастные: огромный display-заголовок + мелкие подписи (text-4xl + text-xs)",
)


def _seed(project_id: str, salt: str) -> int:
    return int.from_bytes(
        hashlib.sha256(f"{salt}:{project_id}".encode()).digest()[:8], "big"
    )


def design_dna_css(project_id: str, industry_hint: str | None = None) -> str:
    """The per-project ``:root{}`` brand override (accent + radius), seeded by id.

    Different projects land on different curated palettes/radii; a project is stable
    across reprompts. No ``@import`` — safe to place anywhere.
    """
    t = tokens_for_project(project_id, industry_hint=industry_hint)
    p = t.palette
    radius = _RADII[_seed(project_id, "radius") % len(_RADII)]
    brand = f"--primary:{p.primary};--accent:{p.accent};--ring:{p.primary};"
    # Override the brand in BOTH light (:root) and dark (:root.dark): the template
    # sets a dark-mode --primary at specificity (0,2,0) that beats a plain :root, so
    # without the :root.dark rule the theme silently loses in dark mode (the indigo
    # auth gradient bug). Brand tokens only — never the canvas neutrals.
    return (
        f"{MARKER}\n"
        f":root{{{brand}--radius:{radius}}}\n"
        f":root.dark{{{brand}}}"
    )


def inject_into_globals(
    css: str, project_id: str, industry_hint: str | None = None
) -> str:
    """Append (or refresh) the per-project Design DNA at the END of globals.css.

    Idempotent: strips our prior managed block first. ALSO strips any google-fonts
    ``@import`` (cleaning up the earlier broken font injection), then appends the
    safe ``:root`` block last so it wins source-order over the template default.
    """
    block = design_dna_css(project_id, industry_hint)
    css = _BLOCK_RE.sub("", css)
    css = _FONT_IMPORT_RE.sub("", css)
    return css.rstrip() + "\n\n" + block + "\n"


# Explicit theme cues in the USER's brief override the random seed — a user who
# asks for «тёмный / премиум / ночной» must NOT get a seeded light palette
# (2026-07-09: «Премиум тёмный» produced a white brutalist app because the mood
# was seeded from project_id and ignored the words).
_DARK_CUES = ("тёмн", "темн", "dark", "ночн", "чёрн", "черн", "midnight", "неон", "премиум", "luxe")
_LIGHT_CUES = ("светл", "light", "белый фон", "на белом", "минимал", "воздушн", "пастельн")


def _theme_override_from_brief(brief: str | None) -> str | None:
    """Return "dark"/"light" when the brief states a clear preference, else None
    (keep the seeded palette's own nature). Dark wins ties (explicit > implicit)."""
    b = (brief or "").lower()
    if any(c in b for c in _DARK_CUES):
        return "dark"
    if any(c in b for c in _LIGHT_CUES):
        return "light"
    return None


def design_mood_directive(project_id: str, industry_hint: str | None = None) -> str:
    """A per-project DESIGN MOOD the agent PERSONALISES the token system to — so
    every app is visually UNIQUE, WITHOUT abandoning the template's design tokens.

    Reuses the curated, WCAG-vetted palette + font pairing (seeded per project for
    uniqueness), but honours an explicit dark/light cue in the brief, and — key —
    frames every colour as a TOKEN VALUE to set in globals.css `:root`, NOT hex to
    hardcode in components. That keeps `@theme`/`bg-primary`/Design-DNA working
    (the old version handed raw hex → the agent hardcoded `bg-[#fff]` + inline
    styles, killing the per-brand accent). Steers what the AGENT WRITES, so it
    works for every container stack.
    """
    t = tokens_for_project(project_id, industry_hint=industry_hint)
    p = t.palette
    density = _DENSITY[_seed(project_id, "density") % len(_DENSITY)]
    heading = _HEADINGS[_seed(project_id, "heading") % len(_HEADINGS)]
    radius = _RADII[_seed(project_id, "radius") % len(_RADII)]

    override = _theme_override_from_brief(industry_hint)
    if override == "dark":
        bg, text, surface, muted, border = "#0f1115", "#f4f5f7", "#1a1d24", "#9aa1ac", "#2a2e37"
        theme_line = (
            "• Тема: ТЁМНАЯ — пользователь попросил тёмный/премиум вид. НЕ делай "
            "светлый фон.\n"
        )
    elif override == "light":
        bg, text, surface, muted, border = "#ffffff", "#141619", "#f6f7f9", "#6b7280", "#e5e7eb"
        theme_line = (
            "• Тема: СВЕТЛАЯ — пользователь попросил светлый/минималистичный вид.\n"
        )
    else:
        bg, text, surface, muted, border = p.bg, p.text, p.surface, p.muted, p.border
        theme_line = ""

    return (
        "\n\nДИЗАЙН-НАСТРОЕНИЕ ЭТОГО ПРОЕКТА — персонализируй под него, НЕ ломая "
        "систему токенов шаблона. РЕДАКТИРУЙ ЗНАЧЕНИЯ переменных в "
        "src/app/globals.css (`:root` и `:root.dark`) — НЕ переписывай файл "
        "хардкодом и НЕ убирай `@theme`. В компонентах используй ТОЛЬКО токены "
        "(bg-background / bg-card / bg-primary / text-foreground / "
        "text-muted-foreground / border-border) — НИКОГДА не `bg-[#hex]`, не "
        "inline `style={{color:…}}`, не `neutral-*`.\n"
        + theme_line
        + f"• Вайб: {p.vibe}\n"
        f"• Токены холста → задай: --background: {bg}; --foreground: {text}; "
        f"--card/--popover: {surface}; --muted-foreground: {muted}; "
        f"--border/--input: {border}\n"
        f"• Токены акцента → задай: --primary: {p.primary}; --accent: {p.accent} "
        "(кнопки/ссылки/свои пузыри = bg-primary)\n"
        f"• Скругления: задай --radius под {radius}\n"
        f"• Плотность: {density}\n"
        f"• Заголовки: {heading}\n"
        f"• Шрифты: display «{t.display_font}», body «{t.body_font}» — подключи "
        f'через <link rel="stylesheet" href="{t.google_fonts_url}"> в layout и '
        "задай --font-sans.\n"
        "Применяй тему ко ВСЕМ экранам (шапка, сайдбар, список бесед, карточки, "
        "инпуты, пузыри). Под запретом training-дефолты indigo/violet вне "
        "заданного акцента."
    )


__all__ = [
    "MARKER",
    "design_dna_css",
    "design_mood_directive",
    "inject_into_globals",
]
