"""Zone-scoped edit — locate the landmark block a user pointed at and replace
ONLY it, byte-for-byte preserving the rest of the page (owner directive
2026-06-06: «выбрал секцию → меняй её, не переписывай весь сайт»).

The click-to-edit flow hands us a selected element (its CSS selector + visible
text). When a surgical ``<edit>`` can't land, instead of regenerating the WHOLE
file (which drifts layout and re-rolls images) we:

1. find the enclosing landmark (``<section>`` / ``<header>`` / ``<footer>``) of
   the selection in the committed HTML — by a distinctive anchor token,
2. hand ONLY that block to the model to rewrite with the requested change,
3. splice the new block back into the exact source span.

Everything outside the span stays identical, so other sections and their images
can't break. Pure string operations on purpose: an HTML parser would re-serialise
the whole document and lose the byte-exactness of the untouched parts.
"""

from __future__ import annotations

import re

# Landmark tags we scope to (generated sites don't nest these). Order doesn't
# matter — we pick the SMALLEST matching span regardless of tag.
_LANDMARKS = ("section", "header", "footer")

# Tailwind/utility classes too generic to identify a zone — never use as anchors.
_GENERIC_CLASSES = frozenset(
    {
        "relative", "absolute", "fixed", "sticky", "static",
        "flex", "grid", "block", "inline", "inline-block", "hidden",
        "container", "mx-auto", "w-full", "h-full", "min-h-screen",
        "inset-0", "z-0", "z-10", "z-20", "z-30", "z-40", "z-50",
        "overflow-hidden", "items-center", "justify-center", "text-center",
        "omnia-shader-over",  # the transparent overlay — not a zone identifier
    }
)

_ID_RE = re.compile(r'\bid\s*=\s*"([^"]+)"')


def _selector_classes(selector: str) -> list[str]:
    """Pull class tokens out of a CSS selector like ``div.a.b#hero > span.c``."""
    return re.findall(r"\.([A-Za-z0-9_-]+)", selector or "")


def distinctive_anchors(
    selected_elements: list[dict[str, object]] | None,
) -> list[str]:
    """Anchor strings to locate the pointed-at zone: the non-generic classes of
    each selection plus its visible text (when long enough to be unique)."""
    anchors: list[str] = []
    for el in selected_elements or []:
        selector = str(el.get("selector") or "")
        for cls in _selector_classes(selector):
            if len(cls) >= 4 and cls.lower() not in _GENERIC_CLASSES:
                anchors.append(cls)
        text = str(el.get("text") or "").strip()
        if len(text) >= 8:
            anchors.append(text)
    # Dedupe, keep order.
    seen: set[str] = set()
    out: list[str] = []
    for a in anchors:
        if a not in seen:
            seen.add(a)
            out.append(a)
    return out


def _landmark_spans(html: str) -> list[tuple[int, int]]:
    """``(start, end)`` char spans of every landmark block. Open tag → its first
    matching close (landmarks aren't nested in generated pages)."""
    spans: list[tuple[int, int]] = []
    for tag in _LANDMARKS:
        for m in re.finditer(rf"<{tag}\b[^>]*>", html, re.I):
            close = html.find(f"</{tag}>", m.end())
            if close != -1:
                spans.append((m.start(), close + len(f"</{tag}>")))
    return spans


def find_enclosing_block(
    html: str, anchors: list[str]
) -> tuple[int, int] | None:
    """The SMALLEST landmark span that contains one of ``anchors``, or ``None``
    when nothing matches or the match is ambiguous (several equally-small spans).
    Smallest = most specific (a section inside main wins over main)."""
    if not anchors:
        return None
    matches = [
        (s, e)
        for (s, e) in _landmark_spans(html)
        if any(a and a in html[s:e] for a in anchors)
    ]
    if not matches:
        return None
    matches.sort(key=lambda se: se[1] - se[0])
    smallest = matches[0]
    # Ambiguous if a second span is the exact same size (can't tell them apart).
    if len(matches) > 1 and (matches[1][1] - matches[1][0]) == (
        smallest[1] - smallest[0]
    ):
        return None
    return smallest


def root_id(block_html: str) -> str | None:
    """The ``id`` of the block's ROOT (first) tag, if any."""
    head = block_html[: block_html.find(">") + 1] if ">" in block_html else block_html
    m = _ID_RE.search(head)
    return m.group(1) if m else None


def extract_block(text: str) -> str | None:
    """Pull a single landmark block out of model output (tolerating ```fences /
    surrounding prose). Returns ``<tag …>…</tag>`` or ``None``."""
    t = text.strip()
    fence = re.search(r"```(?:html)?\s*(.*?)```", t, re.S | re.I)
    if fence and "<" in fence.group(1):
        t = fence.group(1).strip()
    for tag in _LANDMARKS:
        m = re.search(rf"<{tag}\b[^>]*>", t, re.I)
        if not m:
            continue
        close = t.rfind(f"</{tag}>")
        if close != -1 and close > m.start():
            return t[m.start() : close + len(f"</{tag}>")]
    return None


def splice(html: str, span: tuple[int, int], new_block: str) -> str:
    """Replace ``html[span]`` with ``new_block`` — everything else byte-identical."""
    start, end = span
    return html[:start] + new_block + html[end:]


__all__ = [
    "distinctive_anchors",
    "extract_block",
    "find_enclosing_block",
    "root_id",
    "splice",
]
