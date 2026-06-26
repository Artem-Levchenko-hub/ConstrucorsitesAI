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

# Our managed block: the marker + the :root{...} that follows it.
_BLOCK_RE = re.compile(re.escape(MARKER) + r"\s*\n:root\s*\{[^}]*\}\s*", re.MULTILINE)
# Clean up ANY google-fonts @import — including a prior BROKEN injection that landed
# mid-file and fails Turbopack's "@import must precede all rules" CSS parse.
_FONT_IMPORT_RE = re.compile(
    r"(?m)^[ \t]*@import url\(['\"]?https://fonts\.googleapis\.com[^\n]*\n"
)

# Seeded corner radius — a second, cheap axis of visible variation beyond colour.
_RADII = ("0.25rem", "0.375rem", "0.5rem", "0.625rem", "0.75rem", "1rem")


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
    return (
        f"{MARKER}\n"
        ":root{"
        f"--primary:{p.primary};"
        f"--accent:{p.accent};"
        f"--ring:{p.primary};"
        f"--radius:{radius}"
        "}"
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


__all__ = ["MARKER", "design_dna_css", "inject_into_globals"]
