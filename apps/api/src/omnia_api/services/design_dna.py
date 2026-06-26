"""Per-project Design DNA for ENTITY/agent apps — kills "дизайн одинаковый".

Freeform sites already vary (design_tokens seeds a curated palette + font pairing
per project). Entity/agent apps did NOT: the only brand hook (app_theme) sets just
``--primary`` and needs ``globals.css`` in the build files — but the agent never
writes globals.css (it is a FIXED, baked file), so every entity app shipped the
template default palette + Inter → all looked the same.

This module reuses the SAME seeded tokens (so the choice is curated + WCAG-checked
+ stable per project) and injects a distinct identity — accent color + font pairing
— straight into the container's baked ``globals.css``. It deliberately touches ONLY
the SAFE brand knobs (``--primary`` / ``--accent`` / ``--ring`` / fonts), never the
canvas neutrals (``--background`` etc), to keep the load-bearing dark-canvas
specificity contract intact (see app_theme.py / globals.css).

Pure + idempotent: re-running replaces its own managed block, never stacks.
"""

from __future__ import annotations

import re

from omnia_api.services.design_tokens import tokens_for_project

MARKER = "/* omnia:design-dna */"

# A prior managed block: the marker + the :root{...} that follows it.
_BLOCK_RE = re.compile(re.escape(MARKER) + r"\s*\n:root\s*\{[^}]*\}\s*", re.MULTILINE)
# The font @import we add (so re-runs swap it instead of stacking).
_FONT_IMPORT_RE = re.compile(
    r"^@import url\('https://fonts\.googleapis\.com[^\n]*\n", re.MULTILINE
)


def design_dna_css(project_id: str, industry_hint: str | None = None) -> tuple[str, str]:
    """Return ``(font_import_line, root_block)`` — the per-project identity.

    Seeded by project id via ``tokens_for_project`` so different projects land on
    different curated palettes/fonts while a project stays stable across reprompts.
    """
    t = tokens_for_project(project_id, industry_hint=industry_hint)
    p = t.palette
    font_import = f"@import url('{t.google_fonts_url}');"
    root_block = (
        f"{MARKER}\n"
        ":root{"
        f"--primary:{p.primary};"
        f"--accent:{p.accent};"
        f"--ring:{p.primary};"
        f"--font-sans:'{t.body_font}',ui-sans-serif,system-ui,sans-serif;"
        f"--font-display:'{t.display_font}','{t.body_font}',ui-sans-serif,sans-serif;"
        "}"
    )
    return font_import, root_block


def inject_into_globals(
    css: str, project_id: str, industry_hint: str | None = None
) -> str:
    """Inject (or refresh) the per-project Design DNA in a globals.css string.

    Idempotent: strips any prior managed block + our prior font @import first, so
    re-running on an already-themed file just swaps the values. The font @import is
    placed right after ``@import "tailwindcss";`` (CSS requires @import at the top);
    the brand ``:root`` block is appended last so it wins source-order over the
    template default at equal specificity (the documented app_theme cascade trick).
    """
    font_import, root_block = design_dna_css(project_id, industry_hint)

    # Remove our previous injections (idempotent).
    css = _BLOCK_RE.sub("", css)
    css = _FONT_IMPORT_RE.sub("", css)
    css = css.rstrip()

    # Place the font @import directly after the tailwind import (must stay at top).
    lines = css.split("\n")
    inserted = False
    for i, line in enumerate(lines):
        s = line.strip()
        if s.startswith("@import") and "tailwindcss" in s:
            lines.insert(i + 1, font_import)
            inserted = True
            break
    if not inserted:
        lines.insert(0, font_import)
    css = "\n".join(lines)

    return css.rstrip() + "\n\n" + root_block + "\n"


__all__ = ["MARKER", "design_dna_css", "inject_into_globals"]
