"""Deterministic palette guard for generated HTML (model-independent).

The freeform writer is *given* an authoritative, WCAG-checked, project-seeded
palette (services/design_tokens.py → `prompt_block()`), but it is free to
ignore it — and it does. Prod bug 2026-06-01: the sushi page shipped an invented
`--brand:#DC2626` instead of the curated `--primary`, and on "Auto" models drift
straight back to their training-default indigo/violet. Nothing downstream snaps
the colours back, so off-palette / generic-AI colour ships.

This guard is the foundation-enforcement net (the sibling of `contrast_guard`):
run on the FINAL HTML right before commit, it makes the page's `:root` palette
*equal* the project's seeded `CuratedPalette` — "freedom in composition,
rigidity in the foundation". Concretely it:

1. rewrites every recognised `:root` colour custom-prop (under any common alias
   — `--brand`/`--background`/`--text`/… ) to the curated palette value, so a
   var-driven page (what the prompt pushes) is fully on-palette and keeps its
   per-project spread;
2. replaces banned training-default colour LITERALS anywhere (indigo #4f46e5,
   violet #8b5cf6, purple #a855f7, …) with the curated primary, killing the
   "every AI site is indigo/violet" look even when the model hardcodes a hex.

Pure, idempotent, fail-soft: a page already on-palette is returned unchanged; a
parse failure on one file leaves it untouched rather than risking the build.
Pairs with `contrast_guard` (run palette first to snap colours, then contrast to
guarantee body readability against the snapped palette).
"""

from __future__ import annotations

import logging
import re

from omnia_api.sections.palettes import CuratedPalette

log = logging.getLogger(__name__)

__all__ = ["enforce_palette", "repair_html", "BANNED_HEXES"]

# Training-default colours that scream "generic AI landing". Lowercased, 6-digit.
# Mapped wholesale to the curated PRIMARY — the page's dominant brand colour.
BANNED_HEXES: frozenset[str] = frozenset(
    {
        # indigo
        "#4f46e5", "#6366f1", "#818cf8", "#4338ca", "#3730a3", "#a5b4fc",
        # violet
        "#7c3aed", "#8b5cf6", "#a78bfa", "#6d28d9", "#5b21b6", "#c4b5fd",
        # purple / fuchsia
        "#a855f7", "#9333ea", "#c084fc", "#d946ef", "#c026d3", "#e879f9",
    }
)

# `:root` custom-prop name → which CuratedPalette field it must equal. Many
# aliases map to the same role because models name the same thing differently
# (--brand vs --primary, --background vs --bg, --text vs --fg). Only colour
# props are listed; font/radius/shadow vars are left untouched.
_VAR_ROLE: dict[str, str] = {
    # backgrounds
    "--bg": "bg", "--background": "bg", "--bg-base": "bg", "--base": "bg",
    "--bg-primary": "bg", "--color-bg": "bg", "--page": "bg",
    # alt surface
    "--bg-alt": "surface", "--surface": "surface", "--card": "surface",
    "--panel": "surface", "--bg-secondary": "surface", "--bg-soft": "surface",
    "--surface-alt": "surface", "--bg-elevated": "surface",
    # foreground / text
    "--fg": "text", "--foreground": "text", "--text": "text", "--ink": "text",
    "--body": "text", "--text-primary": "text", "--color-text": "text",
    "--copy": "text",
    # muted
    "--muted": "muted", "--muted-foreground": "muted", "--text-muted": "muted",
    "--fg-muted": "muted", "--subtle": "muted", "--text-secondary": "muted",
    "--muted-fg": "muted",
    # primary / brand
    "--primary": "primary", "--brand": "primary", "--brand-primary": "primary",
    "--color-primary": "primary", "--accent-primary": "primary",
    # accent
    "--accent": "accent", "--brand-accent": "accent", "--secondary": "accent",
    "--color-accent": "accent", "--highlight": "accent",
    # border
    "--border": "border", "--line": "border", "--divider": "border",
    "--border-color": "border", "--color-border": "border", "--stroke": "border",
}

_ROOT_RE = re.compile(r":root\s*\{([^}]*)\}", re.IGNORECASE | re.DOTALL)
_DECL_RE = re.compile(r"(--[\w-]+)\s*:\s*([^;]+)\s*;", re.IGNORECASE)
# 3- or 6-digit hex as a whole token (not part of a longer hex/word).
_HEX_TOKEN_RE = re.compile(r"#[0-9a-fA-F]{6}\b|#[0-9a-fA-F]{3}\b")


def _palette_value(palette: CuratedPalette, role: str) -> str | None:
    return {
        "bg": palette.bg,
        "surface": palette.surface,
        "text": palette.text,
        "muted": palette.muted,
        "primary": palette.primary,
        "accent": palette.accent,
        "border": palette.border,
    }.get(role)


def _expand_hex(h: str) -> str:
    """Normalise #rgb → #rrggbb, lowercased."""
    h = h.lower()
    if len(h) == 4:  # #rgb
        return "#" + "".join(c * 2 for c in h[1:])
    return h


def _snap_root(css_inner: str, palette: CuratedPalette) -> tuple[str, bool]:
    """Rewrite recognised colour vars in a :root body to the curated palette."""
    changed = False

    def repl(m: re.Match[str]) -> str:
        nonlocal changed
        name, value = m.group(1), m.group(2).strip()
        role = _VAR_ROLE.get(name.lower())
        if role is None:
            return m.group(0)  # not a colour var we manage (font/radius/etc.)
        target = _palette_value(palette, role)
        if target is None:
            return m.group(0)
        # Only rewrite if the value is a solid hex that differs (leave
        # var()/gradients/keywords alone — can't safely snap those).
        hexes = _HEX_TOKEN_RE.findall(value)
        if len(hexes) != 1 or value.lower() != hexes[0].lower():
            return m.group(0)
        if _expand_hex(hexes[0]) == target.lower():
            return m.group(0)  # already on-palette
        changed = True
        return f"{name}: {target};"

    return _DECL_RE.sub(repl, css_inner), changed


def _strip_banned(html: str, palette: CuratedPalette) -> tuple[str, bool]:
    """Replace banned training-default hex literals with the curated primary."""
    primary = palette.primary
    changed = False

    def repl(m: re.Match[str]) -> str:
        nonlocal changed
        if _expand_hex(m.group(0)) in BANNED_HEXES:
            changed = True
            return primary
        return m.group(0)

    return _HEX_TOKEN_RE.sub(repl, html), changed


# omnia-kit reads these custom-props to colour its CTA / focus-ring / glow /
# selection / tinted-shadow. If the page never sets them, the kit falls back to
# its baked indigo default (`var(--brand-primary, #6366f1)`) — the exact violet
# button the owner flagged 2026-06-03. _snap_root only rewrites vars the page
# ALREADY declared, so a page that omits them leaks indigo. This block force-sets
# them from the curated palette, appended LAST so it wins regardless of the kit.
_BRAND_STYLE_ID = "omnia-brand-vars"
_HEAD_CLOSE_RE = re.compile(r"</head\s*>", re.IGNORECASE)
_BODY_CLOSE_RE = re.compile(r"</body\s*>", re.IGNORECASE)


def _rgba(hex_str: str, alpha: float) -> str:
    """#rrggbb → `rgba(r, g, b, alpha)`. Falls back to a neutral on a bad hex."""
    h = _expand_hex(hex_str.strip()).lstrip("#")
    try:
        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    except (ValueError, IndexError):
        return f"rgba(15, 23, 42, {alpha})"
    return f"rgba({r}, {g}, {b}, {alpha})"


def _inject_brand_vars(html: str, palette: CuratedPalette) -> tuple[str, bool]:
    """Append a final `:root{}` that pins omnia-kit's brand custom-props to the
    palette, so the CTA / ring / glow can never default to the kit's indigo.
    Idempotent (skips if already injected). `--brand-grad-to` is left unset so
    the kit auto-lightens `--brand-primary` tone-in-tone (no 2nd hue on the CTA).
    """
    if _BRAND_STYLE_ID in html:
        return html, False
    p, a = palette.primary, palette.accent
    style = (
        f'<style id="{_BRAND_STYLE_ID}">:root{{'
        f"--brand-primary:{p};--ring:{a};"
        f"--shadow-color:{_rgba(p, 0.24)};--sel-bg:{_rgba(a, 0.18)};"
        f"--tint:{_rgba(a, 0.28)};--glow:{_rgba(a, 0.36)};"
        f"--cursor-blob-color:{a};"
        f"}}</style>"
    )
    if _HEAD_CLOSE_RE.search(html):
        return _HEAD_CLOSE_RE.sub(style + "</head>", html, count=1), True
    if _BODY_CLOSE_RE.search(html):
        return _BODY_CLOSE_RE.sub(style + "</body>", html, count=1), True
    return style + html, True


def repair_html(html: str, palette: CuratedPalette) -> tuple[str, bool]:
    """Snap one HTML string to the curated palette. Returns (html, changed)."""
    out = html
    changed = False

    # 1) Snap :root colour vars (first :root only — models rarely emit two).
    m = _ROOT_RE.search(out)
    if m:
        new_inner, root_changed = _snap_root(m.group(1), palette)
        if root_changed:
            out = out[: m.start(1)] + new_inner + out[m.end(1) :]
            changed = True

    # 2) Replace banned default hexes everywhere (incl. inline styles / classes
    #    written as arbitrary values like bg-[#6366f1]).
    out, banned_changed = _strip_banned(out, palette)
    changed = changed or banned_changed

    # 3) Force-set omnia-kit brand vars from the palette (kills the kit's indigo
    #    fallback on pages that never declared --brand-primary). HTML files only.
    if "<" in out and "</" in out:
        out, brand_changed = _inject_brand_vars(out, palette)
        changed = changed or brand_changed

    return out, changed


def enforce_palette(
    files: dict[str, str], palette: CuratedPalette
) -> dict[str, str]:
    """Snap every HTML file to the project's curated palette. Chainable + safe.

    `palette` is the project's seeded `CuratedPalette` (resolve via
    `design_tokens.tokens_for_project(project_id, industry_hint=preset_id)`),
    i.e. the SAME palette the freeform prompt handed the model — so this just
    enforces what was already asked. Non-HTML files pass through; never raises.
    """
    out = dict(files)
    fixed = 0
    for path, content in files.items():
        if not path.lower().endswith((".html", ".htm")):
            continue
        try:
            new_content, changed = repair_html(content, palette)
        except Exception as exc:  # noqa: BLE001 — a guard must never break the build
            log.warning("palette_guard: skipped %s (%r)", path, exc)
            continue
        if changed:
            out[path] = new_content
            fixed += 1
    if fixed:
        log.info(
            "palette_guard: snapped %d/%d html file(s) to palette '%s'",
            fixed, len(files), palette.id,
        )
    return out
