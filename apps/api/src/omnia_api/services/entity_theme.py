"""Deterministic theme-token guard for entity/.tsx app builds.

The app-UI doctrine bans hardcoded neutral colour utilities (``text-gray-800``,
``bg-gray-100``, ``bg-white`` …) in app components: the art-director re-maps
``--primary`` / ``--foreground`` / ``--muted`` in one ``<style>``, so code that
uses *theme tokens* re-themes for free, while code that uses raw Tailwind neutral
scales stays a frozen grey regardless of the brief. The writer is told this, but
cheap writer models routinely leak ``gray-*`` anyway (they obey a ``zinc`` ban by
switching to ``gray``).

This module is the deterministic enforcement — the ``.tsx`` analogue of the
freeform ``palette_guard``. It rewrites neutral named-colour utilities to the
matching theme token AFTER generation, so the shipped app actually honours the
art-director's palette and the ``structure_audit`` hardcoded-colour class clears.

Intentionally NOT touched:
* semantic status colours (``green`` / ``red`` / ``yellow`` / ``blue`` / ``amber``
  / ``emerald`` / ``rose`` / ``sky`` …) — the brief prescribes them for paid /
  pending / cancelled badges; they are not neutrals.
* the fixed kit (``src/components/ui|omnia``, ``src/lib``, ``src/app/api``) — only
  the writer's own app code is rewritten.
* ``globals.css`` — the frozen Tailwind-v4 token file (not ``.tsx``).
"""

from __future__ import annotations

import re

from omnia_api.services.structure_audit import _FIXED_PREFIXES

# Tailwind neutral scales the doctrine bans in app components.
_NEUTRAL = r"(?:zinc|slate|gray|neutral|stone)"

# A neutral colour utility, with its optional variant prefix (hover:/dark:/…) and
# optional /opacity suffix preserved by the surrounding capture. Matches both the
# numbered scales and the bare white/black, mirroring structure_audit._HARDCODED_COLOR.
_NEUTRAL_UTIL = re.compile(
    rf"\b(bg|text|border|ring|fill|stroke)-"
    rf"(?:(?P<scale>{_NEUTRAL})-(?P<shade>\d{{2,3}})|(?P<bw>white|black))\b"
)


def _token_for(prefix: str, shade: int | None, bw: str | None) -> str:
    """Map one neutral utility to its nearest theme token, role-aware by prefix.

    Dark text/icons → ``foreground``; mid/light → ``muted-foreground``. Light
    surfaces → ``muted``; near-white → ``card``; dark surfaces → ``foreground``.
    Borders → ``border``; focus rings → ``ring``."""
    if prefix == "border":
        return "border-border"
    if prefix == "ring":
        return "ring-ring"
    if bw == "white":
        # White is almost always copy/icon on a coloured fill, or a card surface.
        return "bg-card" if prefix == "bg" else f"{prefix}-primary-foreground"
    if bw == "black":
        return "bg-foreground" if prefix == "bg" else f"{prefix}-foreground"
    n = shade if shade is not None else 500
    if prefix in ("text", "fill", "stroke"):
        return f"{prefix}-{'foreground' if n >= 700 else 'muted-foreground'}"
    # prefix == "bg"
    return "bg-foreground" if n >= 700 else "bg-muted"


def _rewrite(code: str) -> tuple[str, int]:
    """Rewrite neutral utilities in one source string; return (new, count)."""

    def sub(m: re.Match[str]) -> str:
        shade = m.group("shade")
        return _token_for(
            m.group(1),
            int(shade) if shade is not None else None,
            m.group("bw"),
        )

    return _NEUTRAL_UTIL.subn(sub, code)


def tokenize_neutrals(files: dict[str, str]) -> tuple[dict[str, str], int]:
    """Rewrite neutral colour utilities → theme tokens across the writer's .tsx.

    Returns ``(files, total_replacements)``. Files are copied only when changed;
    the fixed kit, non-.tsx files, and semantic status colours are left untouched.
    Side-effect-free and fail-safe — any single-file error leaves that file as-is.
    """
    out: dict[str, str] = {}
    total = 0
    for path, code in files.items():
        if (
            isinstance(code, str)
            and path.endswith(".tsx")
            and not path.startswith(_FIXED_PREFIXES)
        ):
            try:
                new, n = _rewrite(code)
            except Exception:
                out[path] = code
                continue
            if n:
                total += n
                out[path] = new
                continue
        out[path] = code
    return out, total


__all__ = ["tokenize_neutrals"]
