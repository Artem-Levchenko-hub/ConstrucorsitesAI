"""Deterministic brand-token injector for entity/.tsx app builds.

The entity template (``globals.css``) is theme-token driven by design:
``:root`` carries the LIGHT canvas, ``:root.dark`` (specificity 0,2,0) carries
the dark neutrals, and the art-director is expected to override ONLY the brand
tokens — ``--primary`` / ``--primary-foreground`` / ``--ring`` — in one inline
``:root {}`` ``<style>`` in ``(app)/layout.tsx``. The neutrals deliberately stay
with the template so the dark canvas always wins over a per-brand ``:root``; see
the load-bearing specificity note at the top of ``globals.css``. So the ONLY
brand knob is ``--primary`` (+ its foreground + ring) — overriding the canvas
neutrals here would fight that specificity contract and break the dark toggle.

The writer model authors that override by eye and routinely picks a ``--primary``
that is INVISIBLE on the light canvas: a dark palette's ``primary`` field is a
near-white tint (e.g. ``luxury-burgundy`` primary = ``#FEF2F2``), so a near-white
button on the near-white app surface makes the whole brand "disappear" — the
washed-out, design-lost look (prod bug 2026-06-15). The writer's HEX→oklch
conversion was faithful; the input colour was simply the wrong one for the canvas.

This module makes the brand override DETERMINISTIC and model-independent: from
the project's curated palette we pick a ``--primary`` that is actually visible on
the canvas (the palette's brand colour, falling back to its ``accent`` pop when
``primary`` is a near-white tint), compute a readable ``--primary-foreground``,
and rewrite the ``(app)/layout.tsx`` ``:root`` brand block — preserving the
brief-driven ``--radius`` / ``--omnia-ease`` / ``--omnia-dur`` the writer set and
leaving any sibling rules (``.accent-gold`` helpers, etc.) untouched. Pure,
idempotent, fail-soft: a build with no entity layout, or a layout we cannot
parse, is returned unchanged (R-10 — a wrong brand colour is worse than the
template's confident near-black default).
"""

from __future__ import annotations

import logging
import re

from omnia_api.sections.palettes import CuratedPalette, contrast_ratio

log = logging.getLogger(__name__)

__all__ = ["apply_app_palette", "pick_app_primary"]

# The brand override lives in this file only (see globals.css contract).
_APP_LAYOUT_SUFFIX = "(app)/layout.tsx"
# The first ``:root{ … }`` block in the layout = the art-director's brand override.
# No nested braces inside a token block, so ``[^}]*`` captures it exactly; we
# replace only this substring and leave the enclosing ``<style>{"…"}</style>``
# (and any sibling rules like ``.accent-gold``) byte-for-byte intact.
_ROOT_BLOCK = re.compile(r":root\s*\{[^}]*\}")

# Softened inks for button text — never pure #000/#fff (Albers: pure pairs
# "vibrate"; see palettes.py). Whichever reads better on the chosen primary wins.
_DARK_INK = "#171717"
_LIGHT_INK = "#FAFAFA"

# The entity app canvas is LIGHT by default, so a brand colour must clear this
# contrast bar against white to register as a fill. A dark palette's near-white
# ``primary`` field (#FEF2F2 ≈ 1.09:1) falls far below and triggers the accent
# fallback; real brand colours (teal, navy, gold, near-black) clear it easily.
_CANVAS = "#FFFFFF"
_MIN_PRIMARY_VS_CANVAS = 2.0

# Brief-driven tokens to carry over from the writer's override (FORM-DNA /
# MOTION-DNA — they re-shape and re-time the whole kit; not ours to invent).
_PRESERVE = ("--radius", "--omnia-ease", "--omnia-dur")


def _ink_on(color: str) -> str:
    """The softened ink that reads best on ``color`` (button label legibility)."""
    return (
        _LIGHT_INK
        if contrast_ratio(_LIGHT_INK, color) >= contrast_ratio(_DARK_INK, color)
        else _DARK_INK
    )


def pick_app_primary(palette: CuratedPalette) -> tuple[str, str]:
    """``(primary_hex, primary_foreground_hex)`` for the app brand token.

    Prefer the palette's ``primary``; if it's near-invisible on the light canvas
    (a dark palette's near-white ``primary`` field), use the ``accent`` pop
    instead; if neither clears the bar, keep whichever contrasts most so the
    brand is at least present. ``primary_foreground`` is the softened ink that
    reads on the chosen fill."""
    candidates = (palette.primary, palette.accent)
    primary = next(
        (c for c in candidates if contrast_ratio(c, _CANVAS) >= _MIN_PRIMARY_VS_CANVAS),
        None,
    )
    if primary is None:
        primary = max(candidates, key=lambda c: contrast_ratio(c, _CANVAS))
    return primary, _ink_on(primary)


def _preserved_decls(root_css: str) -> str:
    """Re-emit the brief-driven tokens found in the old ``:root`` block, in order.

    Returns ``";--radius:…;--omnia-ease:…"`` (leading separator) or ``""``."""
    parts: list[str] = []
    for tok in _PRESERVE:
        m = re.search(re.escape(tok) + r"\s*:\s*([^;}]+)", root_css)
        if m:
            parts.append(f"{tok}:{m.group(1).strip()}")
    return (";" + ";".join(parts)) if parts else ""


def _root_block(palette: CuratedPalette, preserved: str) -> str:
    primary, fg = pick_app_primary(palette)
    return (
        f":root{{--primary:{primary};--primary-foreground:{fg};"
        f"--ring:{primary}{preserved}}}"
    )


def apply_app_palette(
    files: dict[str, str], palette: CuratedPalette
) -> dict[str, str]:
    """Rewrite the brand ``:root`` override in ``(app)/layout.tsx`` from ``palette``.

    Returns a new dict. No-op for non-entity builds (no ``(app)/layout.tsx``) and
    when the layout carries no ``:root`` block to rewrite (fail-soft: the template
    default theme stays). Never raises — a parse failure leaves the file as-is.
    """
    out = dict(files)
    for path, code in files.items():
        if not (isinstance(code, str) and path.endswith(_APP_LAYOUT_SUFFIX)):
            continue
        m = _ROOT_BLOCK.search(code)
        if not m:
            log.info("app_theme: no :root brand block in %r — left as-is", path)
            continue
        try:
            new_block = _root_block(palette, _preserved_decls(m.group(0)))
        except Exception as exc:  # a guard must never break the build
            log.warning("app_theme: skipped %r (%r)", path, exc)
            continue
        if new_block == m.group(0):
            continue
        out[path] = code[: m.start()] + new_block + code[m.end() :]
        log.info(
            "app_theme: rebranded :root --primary in %r from palette %s",
            path, palette.id,
        )
    return out
