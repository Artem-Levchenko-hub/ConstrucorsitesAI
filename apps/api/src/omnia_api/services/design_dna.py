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


def design_mood_directive(project_id: str, industry_hint: str | None = None) -> str:
    """A per-project DESIGN MOOD the agent must build the UI in — so every app is
    visually UNIQUE instead of the baked dark zinc/indigo template look.

    Reuses the curated, WCAG-vetted palette + font pairing (60+ palettes / 16
    pairs already seeded per project) and adds seeded density + heading
    personality. Unlike CSS-token injection (inert on the hardcoded realtime
    template), this steers what the AGENT WRITES, so it works for EVERY container
    stack. Seeded by project_id → stable across re-prompts, distinct across
    projects. The curated colours are pre-checked for AA contrast, so honoring
    them keeps text readable.
    """
    t = tokens_for_project(project_id, industry_hint=industry_hint)
    p = t.palette
    density = _DENSITY[_seed(project_id, "density") % len(_DENSITY)]
    heading = _HEADINGS[_seed(project_id, "heading") % len(_HEADINGS)]
    radius = _RADII[_seed(project_id, "radius") % len(_RADII)]
    return (
        "\n\nДИЗАЙН-НАСТРОЕНИЕ ЭТОГО ПРОЕКТА — собери весь интерфейс ИМЕННО в нём. "
        "НЕ используй дефолтный тёмный zinc/indigo вид шаблона: каждое приложение "
        "должно выглядеть уникально.\n"
        f"• Вайб: {p.vibe}\n"
        f"• Холст: фон {p.bg}, основной текст {p.text}, поверхности/карточки "
        f"{p.surface}, приглушённый текст {p.muted}, границы {p.border}\n"
        f"• Акцент (кнопки/ссылки/активные пузыри): {p.primary}; вторичный {p.accent}\n"
        f"• Скругления: {radius}\n"
        f"• Плотность: {density}\n"
        f"• Заголовки: {heading}\n"
        f"• Шрифты: display «{t.display_font}», body «{t.body_font}» — подключи их "
        f'через <link rel="stylesheet" href="{t.google_fonts_url}"> в layout и '
        "примени font-family по интерфейсу.\n"
        "Применяй эти цвета/отступы/типографику ко ВСЕМ экранам (шапка, сайдбар, "
        "список бесед, карточки, инпуты, пузыри сообщений). Под запретом "
        "training-дефолты indigo/violet/purple вне указанного акцента."
    )


__all__ = [
    "MARKER",
    "design_dna_css",
    "inject_into_globals",
    "design_mood_directive",
]
