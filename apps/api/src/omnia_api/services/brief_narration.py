"""Bake the art-director brief into a shared static page so it narrates its
own birth (ONE BRIEF, EVERY SURFACE — pillar 3 + 4).

WHY this exists: the art-director brief (palette / fonts / sections / motion) is
the single source of design truth, but until now it reached ONLY the workspace
chat and the live container iframe (postMessage). A *shared* freeform `/p/<slug>`
link — the most-shared public surface (freeform is the prod default) — was born
SILENT: a colleague pasting the link saw a finished, static page with none of
the hypnotic "AI is designing this" reveal that hooks the viral loop.

This module bakes, at commit time (when the brief is known), a tiny payload +
self-contained reveal script into the generated `index.html`:

    <script>window.__omniaBrief = {…};</script>
    <script id="omnia-brief-narration">/* baked reveal, plays once */</script>

The reveal mirrors `apps/web/src/lib/brief-narration.ts` and the entity
template's `public/omnia-brief-narration.js` (same line copy, same role order)
so the workspace narration and the on-surface narration tell the IDENTICAL
story. The Python `brief_lines` below is the canonical line logic for this side
and is pinned by tests so the Russian copy can't drift.

Fail-soft: a brief that yields zero lines injects nothing (the page ships
unchanged). Idempotent: a page already carrying the reveal is returned as-is.
Self-contained (no CDN), reduced-motion-safe, plays once per browser session.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

#: Valid CSS hex (#rgb…#rrggbbaa). Mirror of brief-narration.ts HEX_RE.
_HEX_RE = re.compile(r"^#[0-9a-fA-F]{3,8}$")

#: Marker id — presence means the reveal is already injected (idempotent).
_MARKER = 'id="omnia-brief-narration"'


def _palette_role(key: str) -> int:
    """accent → primary → background → rest. Mirror of brief-narration.ts."""
    u = (key or "").upper()
    if "АКЦЕНТ" in u or "ACCENT" in u:
        return 0
    if "PRIMARY" in u:
        return 1
    if "ФОН" in u or "BACKGROUND" in u:
        return 2
    return 3


def _pick_hexes(palette: dict[str, Any], limit: int) -> list[str]:
    """Up to ``limit`` distinct valid hex from the palette, ordered by role."""
    entries = sorted(
        (
            (k, v)
            for k, v in (palette or {}).items()
            if isinstance(v, str) and _HEX_RE.match(v.strip())
        ),
        key=lambda kv: _palette_role(kv[0]),
    )
    out: list[str] = []
    for _, v in entries:
        hexv = v.strip()
        if hexv not in out:
            out.append(hexv)
        if len(out) >= limit:
            break
    return out


def _short_motion(motion: str) -> str:
    """First meaningful fragment of a motion spec (mirror of shortMotion)."""
    m = (motion or "").strip()
    if len(m) <= 48:
        return m
    cut = m[:48]
    sp = cut.rfind(" ")
    return (cut[:sp] if sp > 24 else cut) + "…"


def brief_lines(brief: dict[str, Any] | None) -> list[str]:
    """Ordered narration lines from the brief — the art-director's train of
    thought: palette → font → section frame → motion. A line is included only
    when its field is non-empty; the result is de-duplicated.

    Byte-for-byte the same copy as apps/web brief-narration.ts (briefNarration)
    and the template omnia-brief-narration.js (briefLines).
    """
    if not brief:
        return []
    lines: list[str] = []

    hexes = _pick_hexes(brief.get("palette") or {}, 2)
    if hexes:
        lines.append("Подбираю палитру — " + " и ".join(hexes))

    fonts = brief.get("fonts") or {}
    display = (fonts.get("display") or "").strip()
    text = (fonts.get("text") or "").strip()
    if display:
        lines.append(f"Беру шрифт «{display}» для заголовков")
    elif text:
        lines.append(f"Беру шрифт «{text}» для текста")

    names = [
        (s.get("name") or "").strip()
        for s in (brief.get("sections") or [])
        if isinstance(s, dict) and (s.get("name") or "").strip()
    ]
    if names:
        shown = names[:4]
        suffix = " …" if len(names) > len(shown) else ""
        lines.append("Компоную секции: " + " → ".join(shown) + suffix)

    motion = (brief.get("motion") or "").strip()
    if motion:
        lines.append("Оживляю движением — " + _short_motion(motion))

    # De-dup, preserve order.
    return list(dict.fromkeys(lines))


# Self-contained baked reveal, read once at import (mirror of public.py's
# omnia-inspector.js pattern). Reads window.__omniaBrief, recomputes the lines
# from the brief (FALSIFIABLE proof the brief surfaced — a hardcoded list would
# not change with the brief), renders a swatch row + cadenced lines, then fades.
# A missing file fails loudly at startup rather than silently per-request.
_NARRATION_SCRIPT = (
    Path(__file__).resolve().parent.parent / "static" / "omnia-brief-narration.js"
).read_text(encoding="utf-8")


def inject_brief_narration(html: str, brief: dict[str, Any] | None) -> str:
    """Return ``html`` with the baked brief payload + reveal script injected.

    Inserts before ``</head>`` (fallback ``</body>``, fallback append). The
    brief is JSON-encoded (a subset of JS object syntax) so Cyrillic / quotes /
    backslashes can never break the literal; ``</`` is escaped so the payload
    can't terminate the <script> early.

    Fail-soft: a brief yielding zero lines (or ``None``) returns ``html``
    unchanged — the page ships silent. Idempotent: a page already carrying the
    reveal is returned untouched.
    """
    if not html or _MARKER in html:
        return html
    if not brief_lines(brief):
        return html
    payload = json.dumps(brief, ensure_ascii=False).replace("</", "<\\/")
    tag = (
        f"<script>window.__omniaBrief={payload};</script>\n"
        f'<script {_MARKER}>{_NARRATION_SCRIPT}</script>'
    )
    for marker in ("</head>", "</body>"):
        idx = html.rfind(marker)
        if idx != -1:
            return html[:idx] + tag + html[idx:]
    return html + tag


__all__ = ["brief_lines", "inject_brief_narration"]
