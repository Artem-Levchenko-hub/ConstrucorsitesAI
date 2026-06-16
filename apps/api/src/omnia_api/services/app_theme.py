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

GLOBAL surface (the load-bearing fix, prod bug 2026-06-16). A ``<style>`` brand
``:root`` only re-themes the route that renders the file it lives in. Cheap
writers routinely scope the override to ``page.tsx`` (the public LANDING) — so
the landing reads correctly branded while the authenticated ``(app)/*`` dashboard
reverts to the template's near-black default (the "landing is teal, the app is
monochrome grey" look). And the ``(app)/layout.tsx`` rewrite above can't help
when the writer authored no block there. So we ALSO pin the brand onto a GLOBAL
surface — a trailing ``:root { --primary … }`` appended to ``globals.css`` (the
one file imported by the root layout, so it cascades to EVERY route). The block
is appended last → at equal specificity it wins source-order over the template
default ``:root`` and the ``.dark { --primary }`` fallback, while the neutral
canvas (``:root.dark`` at 0,2,0) still wins for the dark background. We source
the colour by HARVESTING the writer's own visible landing override (so a correct
hand-picked brand like the user-requested teal carries through verbatim), falling
back to the deterministic palette pick when the writer themed nothing visible.
"""

from __future__ import annotations

import logging
import re

from omnia_api.sections.palettes import CuratedPalette, contrast_ratio

log = logging.getLogger(__name__)

__all__ = ["apply_app_palette", "pick_app_primary"]

# The brand override lives in this file only (see globals.css contract).
_APP_LAYOUT_SUFFIX = "(app)/layout.tsx"
# The global cascade surface — imported by the root layout, so a trailing
# ``:root{}`` here re-themes every route (landing + authed app).
_GLOBALS_SUFFIX = "globals.css"
# Marker so the appended global-brand block is idempotent across re-runs.
_GLOBAL_BRAND_MARKER = "/* omnia:global-brand */"
# The first ``:root{ … }`` block in the layout = the art-director's brand override.
# No nested braces inside a token block, so ``[^}]*`` captures it exactly; we
# replace only this substring and leave the enclosing ``<style>{"…"}</style>``
# (and any sibling rules like ``.accent-gold``) byte-for-byte intact.
_ROOT_BLOCK = re.compile(r":root\s*\{[^}]*\}")
# A ``:root{}`` block that actually sets ``--primary`` (the writer's brand override
# embedded in a landing ``<style>``), used to harvest the hand-picked brand.
_BRAND_ROOT_RE = re.compile(r":root\s*\{([^{}]*--primary[^{}]*)\}")


def _decl(name: str, css: str) -> str | None:
    """Value of one ``--token`` declaration in a token block, or None."""
    m = re.search(re.escape(name) + r"\s*:\s*([^;}]+)", css)
    return m.group(1).strip() if m else None


def _is_visible_fill(value: str) -> bool:
    """True when ``value`` reads as a real fill on the LIGHT canvas (not a
    near-white tint that vanishes). Handles the two forms writers emit: oklch
    (gate on lightness L) and hex (gate on WCAG contrast vs white)."""
    v = value.strip().lower()
    m = re.match(r"oklch\(\s*([0-9.]+)", v)
    if m:
        try:
            return float(m.group(1)) <= 0.82
        except ValueError:
            return False
    if v.startswith("#"):
        try:
            return contrast_ratio(value, _CANVAS) >= _MIN_PRIMARY_VS_CANVAS
        except Exception:
            return False
    # rgb()/hsl()/named — accept unless it is obviously white.
    return v not in ("white", "#fff", "#ffffff", "rgb(255,255,255)")

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


def _harvest_writer_brand(files: dict[str, str]) -> dict[str, str] | None:
    """The writer's own VISIBLE ``--primary`` (+ foreground/ring) from a landing
    ``<style>`` ``:root``, if it hand-picked a real brand. None otherwise.

    Scans only the writer's ``.tsx`` (never ``globals.css`` — that's the template
    default — nor ``(app)/layout.tsx``, which is the deterministic guard's own
    target). The first visible ``--primary`` wins. Used so a correct hand-picked
    brand (e.g. the user-requested teal the writer scoped to ``page.tsx``) carries
    through verbatim instead of being replaced by a generic palette pick."""
    for path, code in files.items():
        if not (isinstance(code, str) and path.endswith(".tsx")):
            continue
        if path.endswith(_APP_LAYOUT_SUFFIX):
            continue
        for m in _BRAND_ROOT_RE.finditer(code):
            body = m.group(1)
            primary = _decl("--primary", body)
            if not primary or not _is_visible_fill(primary):
                continue
            return {
                "primary": primary,
                "fg": _decl("--primary-foreground", body) or "oklch(0.985 0 0)",
                "ring": _decl("--ring", body) or primary,
            }
    return None


def _resolve_brand(files: dict[str, str], palette: CuratedPalette) -> dict[str, str]:
    """The single brand triple to pin app-wide: the writer's visible override if
    present, else the deterministic palette pick (always present, fail-soft)."""
    harvested = _harvest_writer_brand(files)
    if harvested:
        return harvested
    primary, fg = pick_app_primary(palette)
    return {"primary": primary, "fg": fg, "ring": primary}


def _ensure_global_brand(
    files: dict[str, str], brand: dict[str, str]
) -> dict[str, str]:
    """Pin the brand on a GLOBAL surface so EVERY route is themed, not just the
    one file the writer happened to style. Appends (or refreshes) a trailing
    ``:root{}`` brand block in ``globals.css``. Plain text → cannot break a build;
    idempotent via the marker. No-op when there is no ``globals.css`` (freeform)."""
    block = (
        f"\n{_GLOBAL_BRAND_MARKER}\n"
        f":root{{--primary:{brand['primary']};"
        f"--primary-foreground:{brand['fg']};--ring:{brand['ring']}}}\n"
    )
    out = dict(files)
    for path, code in files.items():
        if not (isinstance(code, str) and path.endswith(_GLOBALS_SUFFIX)):
            continue
        if _GLOBAL_BRAND_MARKER in code:
            out[path] = re.sub(
                re.escape(_GLOBAL_BRAND_MARKER) + r"\s*\n:root\s*\{[^}]*\}\n?",
                block.lstrip("\n"),
                code,
            )
        else:
            out[path] = code.rstrip() + "\n" + block
        log.info("app_theme: pinned global brand --primary:%s", brand["primary"])
    return out


def apply_app_palette(
    files: dict[str, str], palette: CuratedPalette
) -> dict[str, str]:
    """Pin the entity app's brand ``--primary`` (+ foreground/ring) app-wide.

    1. Resolve ONE brand triple — the writer's visible landing override if present
       (so a hand-picked brand carries verbatim), else the deterministic palette
       pick that is guaranteed visible on the light canvas.
    2. Pin it on the GLOBAL surface (``globals.css``) so the landing AND the
       authenticated ``(app)/*`` dashboard share it — the actual fix for the
       "landing branded, app monochrome grey" bug.
    3. Keep the ``(app)/layout.tsx`` ``:root`` block (when the writer authored one)
       in sync with the same triple, preserving its brief-driven radius/motion.

    Returns a new dict. No-op for non-entity builds (no ``globals.css`` /
    ``(app)/layout.tsx``). Never raises — a parse failure leaves files as-is.
    """
    out = dict(files)
    try:
        brand = _resolve_brand(out, palette)
    except Exception as exc:  # a guard must never break the build
        log.warning("app_theme: brand resolve failed (%r) — left as-is", exc)
        return out

    out = _ensure_global_brand(out, brand)

    for path, code in list(out.items()):
        if not (isinstance(code, str) and path.endswith(_APP_LAYOUT_SUFFIX)):
            continue
        m = _ROOT_BLOCK.search(code)
        if not m:
            log.info("app_theme: no :root brand block in %r — global brand covers it", path)
            continue
        try:
            new_block = (
                f":root{{--primary:{brand['primary']};"
                f"--primary-foreground:{brand['fg']};"
                f"--ring:{brand['ring']}{_preserved_decls(m.group(0))}}}"
            )
        except Exception as exc:
            log.warning("app_theme: skipped %r (%r)", path, exc)
            continue
        if new_block == m.group(0):
            continue
        out[path] = code[: m.start()] + new_block + code[m.end() :]
        log.info("app_theme: synced (app)/layout :root brand in %r", path)
    return out
