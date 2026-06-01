"""Deterministic contrast guard for generated HTML (model-independent).

The freeform writer (any model — Opus, DeepSeek, Haiku) emits its own inline
``<style>`` with ``:root`` custom-props and a ``body { color; background }`` rule.
Nothing downstream checks the body text is readable on the body background, so a
careless model can ship dark text on a dark background — prod bug 2026-06-01:
``--fg:#1F1F1F`` declared but ``body{background:#0F1419}`` hardcoded → near
black-on-black, the user could not read anything.

This is the single safety net, run on the FINAL HTML right before commit (so the
snapshot / GitHub export / rollback all carry the fixed page). It:

1. parses the effective body text + background colour (resolving ``var(--x)``
   against ``:root``),
2. if their WCAG-AA contrast is below 4.5:1, repairs the page —
   * **prefer the model's OWN palette**: if ``:root`` declares a coherent
     ``--bg``/``--fg``-style pair (good contrast), force ``body`` to use it
     (the 2026-06-01 bug is exactly a hardcoded body bg overriding a fine
     declared palette);
   * **fallback**: luminance-flip the body text colour to a readable neutral.

Pure, idempotent, fail-soft: colours we cannot read (gradients, images, unknown
functions) are left untouched rather than guessed. A page that already passes is
returned byte-for-byte unchanged.
"""

from __future__ import annotations

import logging
import re

from omnia_api.sections.palettes import contrast_ratio

log = logging.getLogger(__name__)

__all__ = ["enforce_contrast", "repair_html"]

# WCAG 2.x AA for normal body text. Large display text only needs 3:1, but the
# body rule colours all running text, so we hold it to the stricter bar.
_AA_BODY = 4.5

# Readable neutrals for the luminance-flip fallback (softened, not pure
# #000/#fff — Albers: pure black on pure white "vibrates"; see palettes.py).
_DARK_INK = "#111827"   # zinc-900-ish
_LIGHT_INK = "#F5F5F4"  # stone-100-ish

_NAMED_COLORS = {
    "white": "#ffffff",
    "black": "#000000",
    "red": "#ff0000",
    "green": "#008000",
    "blue": "#0000ff",
    "gray": "#808080",
    "grey": "#808080",
    "silver": "#c0c0c0",
}

_HEX_RE = re.compile(r"^#(?:[0-9a-fA-F]{3,4}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$")
_RGB_RE = re.compile(
    r"^rgba?\(\s*([0-9.]+)\s*[, ]\s*([0-9.]+)\s*[, ]\s*([0-9.]+)", re.IGNORECASE
)
_VAR_RE = re.compile(r"var\(\s*(--[\w-]+)\s*(?:,\s*([^)]+))?\)", re.IGNORECASE)

# `:root { ... }` body (first one wins; models rarely emit two).
_ROOT_RE = re.compile(r":root\s*\{([^}]*)\}", re.IGNORECASE | re.DOTALL)
# `body { ... }` rule — capture the inner declarations so we can rewrite them.
_BODY_RE = re.compile(r"(body\s*\{)([^}]*)(\})", re.IGNORECASE | re.DOTALL)
_DECL_RE = re.compile(r"(--[\w-]+|[\w-]+)\s*:\s*([^;]+)\s*;?", re.IGNORECASE)

# Custom-prop name heuristics for the "use the declared palette" repair.
_BG_HINTS = ("bg", "background", "surface", "base", "paper", "canvas")
_FG_HINTS = ("fg", "foreground", "text", "ink", "body", "copy", "content")


def _expand_hex(h: str) -> str | None:
    h = h.lstrip("#")
    if len(h) in (3, 4):  # #rgb / #rgba
        h = "".join(c * 2 for c in h[:3])
    elif len(h) in (6, 8):  # #rrggbb / #rrggbbaa
        h = h[:6]
    else:
        return None
    return "#" + h.lower()


def _to_hex(token: str) -> str | None:
    """Best-effort CSS colour token → ``#rrggbb``. None if not a solid colour."""
    if not token:
        return None
    t = token.strip().lower()
    if t in _NAMED_COLORS:
        return _NAMED_COLORS[t]
    if _HEX_RE.match(t):
        return _expand_hex(t)
    m = _RGB_RE.match(t)
    if m:
        try:
            r, g, b = (max(0, min(255, int(round(float(x))))) for x in m.groups())
            return f"#{r:02x}{g:02x}{b:02x}"
        except (ValueError, TypeError):
            return None
    return None


def _parse_root_vars(html: str) -> dict[str, str]:
    """Map ``--name`` → ``#rrggbb`` for every solid-colour custom prop in :root."""
    out: dict[str, str] = {}
    m = _ROOT_RE.search(html)
    if not m:
        return out
    for name, value in _DECL_RE.findall(m.group(1)):
        if not name.startswith("--"):
            continue
        hexv = _to_hex(value)
        if hexv:
            out[name.lower()] = hexv
    return out


def _resolve(value: str, root: dict[str, str]) -> str | None:
    """Resolve a declaration value (maybe ``var(--x)``) to ``#rrggbb`` or None."""
    if not value:
        return None
    v = value.strip()
    vm = _VAR_RE.search(v)
    if vm:
        name = vm.group(1).lower()
        if name in root:
            return root[name]
        fallback = vm.group(2)
        return _to_hex(fallback) if fallback else None
    # Take the first colour-looking token (handles `background: #fff url(...)`).
    for tok in re.split(r"\s+", v):
        hexv = _to_hex(tok)
        if hexv:
            return hexv
    return None


def _body_colors(
    decls: str, root: dict[str, str]
) -> tuple[str | None, str | None]:
    """Return (resolved_fg_hex, resolved_bg_hex) from body declarations."""
    fg_hex = None
    bg_hex = None
    for name, value in _DECL_RE.findall(decls):
        ln = name.strip().lower()
        if ln == "color":
            fg_hex = _resolve(value, root)
        elif ln in ("background", "background-color"):
            resolved = _resolve(value, root)
            # Only treat as a real bg if it's a solid colour; a gradient/image
            # leaves bg_hex None so we stay conservative.
            if resolved:
                bg_hex = resolved
    return fg_hex, bg_hex


def _coherent_palette(root: dict[str, str]) -> tuple[str, str] | None:
    """Find a (bg_hex, fg_hex) pair the model declared that already passes AA."""
    bg_candidates = [v for n, v in root.items() if any(h in n for h in _BG_HINTS)]
    fg_candidates = [v for n, v in root.items() if any(h in n for h in _FG_HINTS)]
    for bg in bg_candidates:
        for fg in fg_candidates:
            if contrast_ratio(fg, bg) >= _AA_BODY:
                return bg, fg
    return None


def _is_dark(hex_color: str) -> bool:
    """True when light text reads better than dark text on this background."""
    return contrast_ratio("#ffffff", hex_color) >= contrast_ratio("#000000", hex_color)


def _set_decl(decls: str, prop: str, value: str) -> str:
    """Replace ``prop: ...;`` inside a rule body, or append it if absent."""
    pat = re.compile(rf"(\b{re.escape(prop)}\s*:\s*)([^;]+)(;?)", re.IGNORECASE)
    if pat.search(decls):
        return pat.sub(rf"\g<1>{value};", decls, count=1)
    sep = "" if (not decls.strip() or decls.rstrip().endswith(";")) else ";"
    return f"{decls.rstrip()}{sep} {prop}: {value}; "


def repair_html(html: str) -> tuple[str, bool]:
    """Repair body text/bg contrast in one HTML string. Returns (html, changed)."""
    root = _parse_root_vars(html)
    body_m = _BODY_RE.search(html)
    if not body_m:
        return html, False

    decls = body_m.group(2)
    fg_hex, bg_hex = _body_colors(decls, root)

    # Browser defaults: unset text is black, unset bg is white. Only treat the
    # page as problematic when at least one colour is explicit.
    if fg_hex is None and bg_hex is None:
        return html, False
    eff_fg = fg_hex or "#000000"
    eff_bg = bg_hex or "#ffffff"
    if contrast_ratio(eff_fg, eff_bg) >= _AA_BODY:
        return html, False  # already readable

    # ── Repair ──────────────────────────────────────────────────────────────
    new_decls = decls
    pair = _coherent_palette(root)
    if pair is not None:
        # The model DID declare a readable palette; the body just overrode it
        # with a clashing hardcoded colour. Pin body to the declared pair.
        bg, fg = pair
        new_decls = _set_decl(new_decls, "background", bg)
        new_decls = _set_decl(new_decls, "color", fg)
        strategy = "declared-palette"
    elif bg_hex is not None:
        # Keep the model's background, flip the text to a readable neutral.
        ink = _LIGHT_INK if _is_dark(bg_hex) else _DARK_INK
        new_decls = _set_decl(new_decls, "color", ink)
        strategy = "flip-text"
    else:
        # Light text but no readable bg (only `color` set) → give body a dark
        # surface so the light text becomes visible.
        new_decls = _set_decl(new_decls, "background", "#0B0F17")
        new_decls = _set_decl(new_decls, "color", _LIGHT_INK)
        strategy = "add-bg"

    if new_decls == decls:
        return html, False
    new_html = html[: body_m.start(2)] + new_decls + html[body_m.end(2) :]
    log.info("contrast_guard: repaired body contrast via %s", strategy)
    return new_html, True


def enforce_contrast(files: dict[str, str]) -> dict[str, str]:
    """Repair body text/bg contrast across every HTML file. Chainable + safe.

    Returns a new dict; non-HTML files and already-readable pages pass through
    unchanged. Never raises — a parse failure on one file leaves it as-is.
    """
    out = dict(files)
    fixed = 0
    for path, content in files.items():
        if not path.lower().endswith((".html", ".htm")):
            continue
        try:
            new_content, changed = repair_html(content)
        except Exception as exc:  # noqa: BLE001 — a guard must never break the build
            log.warning("contrast_guard: skipped %s (%r)", path, exc)
            continue
        if changed:
            out[path] = new_content
            fixed += 1
    if fixed:
        log.info("contrast_guard: fixed %d/%d html file(s)", fixed, len(files))
    return out
