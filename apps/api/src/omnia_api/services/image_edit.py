"""Direct image-generation edit — when the user points at a zone and asks for an
image, go straight to the GRAPHICS model instead of routing an HTML edit through
the heavy text LLM (owner directive 2026-06-06: «логично вызвать модель которая
генерит графику»).

Pure helpers only: detect the intent, find the ``<img>`` in the pointed zone, and
rebuild it as a ``data-omnia-gen`` tag (which ``image_resolver`` turns into a real
flux image). The orchestration — craft the prompt, splice, resolve — lives in the
caller (``routers/messages.py``) where the LLM/gateway clients are available.
"""

from __future__ import annotations

import re

# Explicit "make me a picture" — fires on its own.
_STRONG_GEN_RE = re.compile(
    r"сгенер|генерац|нарисуй|generate (an )?image|generate (a )?picture", re.IGNORECASE
)
# An image NOUN + a change VERB together also count ("поменяй фото", "добавь
# изображение"). Kept conservative so a plain text edit that merely mentions a
# photo doesn't get hijacked.
_IMG_NOUN_RE = re.compile(
    r"картинк|изображени|иллюстрац|\bфото\b|фотограф|\bimage\b|picture|рисунок",
    re.IGNORECASE,
)
_CHANGE_VERB_RE = re.compile(
    r"добав|замен|постав|поменя|обнови|сделай нов|другую|свеж|new", re.IGNORECASE
)

_IMG_TAG_RE = re.compile(r"<img\b[^>]*>", re.IGNORECASE)
_ALT_RE = re.compile(r'\balt\s*=\s*"([^"]*)"', re.IGNORECASE)
_CLASS_RE = re.compile(r'\bclass\s*=\s*"([^"]*)"', re.IGNORECASE)


def is_image_request(prompt: str | None) -> bool:
    """True when the user is asking to generate / change an IMAGE."""
    t = prompt or ""
    if _STRONG_GEN_RE.search(t):
        return True
    return bool(_IMG_NOUN_RE.search(t) and _CHANGE_VERB_RE.search(t))


def find_first_img(block: str) -> tuple[int, int, str] | None:
    """``(start, end, tag)`` of the first ``<img>`` in ``block``, or ``None``."""
    m = _IMG_TAG_RE.search(block)
    return (m.start(), m.end(), m.group(0)) if m else None


def rebuild_img_with_gen(old_img: str, gen_prompt: str) -> str:
    """Turn an existing ``<img src=…>`` into ``<img data-omnia-gen=…>`` — keeping
    its ``alt`` and ``class`` (so layout/styling are unchanged) and dropping the
    old ``src``. ``image_resolver`` will fill in the freshly generated photo."""
    alt_m = _ALT_RE.search(old_img)
    cls_m = _CLASS_RE.search(old_img)
    alt = alt_m.group(1) if alt_m else "изображение"
    cls = cls_m.group(1) if cls_m else ""
    gp = gen_prompt.replace('"', "'").strip()
    cls_attr = f' class="{cls}"' if cls else ""
    return f'<img data-omnia-gen="{gp}" alt="{alt}"{cls_attr} />'


def alt_of(img_tag: str) -> str:
    """The ``alt`` text of an ``<img>`` tag (empty string if none)."""
    m = _ALT_RE.search(img_tag)
    return m.group(1) if m else ""


def is_fullbleed_bg(img_tag: str) -> bool:
    """True when the ``<img>`` is a full-bleed BACKGROUND layer (covers the whole
    section), i.e. ``absolute inset-0 … object-cover`` — as opposed to a framed
    foreground photo. A full-bleed bg is usually masked by a dark overlay we must
    lighten so a regenerated image is actually visible."""
    cls_m = _CLASS_RE.search(img_tag)
    cls = cls_m.group(1) if cls_m else ""
    return "absolute" in cls and "inset-0" in cls and "object-cover" in cls


_GRADIENT_DIV_RE = re.compile(r"<div\b[^>]*\bbg-gradient[^>]*>", re.IGNORECASE)
_OPACITY_SUFFIX_RE = re.compile(r"/(\d{2,3})\b")


def _reduce_opacity(m: "re.Match[str]") -> str:
    n = int(m.group(1))
    # Only soften the HEAVY dark stops (≥50). The subtle accent stops (e.g. a
    # gold /5) are single-digit and never match this 2–3 digit pattern anyway.
    return "/" + str(max(30, round(n * 0.6))) if n >= 50 else m.group(0)


def lighten_overlay_edits(zone_html: str) -> list[tuple[str, str]]:
    """``(old_div, new_div)`` SEARCH/REPLACE pairs that LIGHTEN the dark gradient
    overlay(s) in a zone (e.g. ``from-[#0C0A09]/70 … to-[#0C0A09]/90`` → ``…/42 …
    …/54``) so a full-bleed background image shows through while text stays
    readable. Touches only Tailwind ``bg-gradient`` divs; the colour HEXes,
    structure and low-opacity accents are preserved."""
    edits: list[tuple[str, str]] = []
    for m in _GRADIENT_DIV_RE.finditer(zone_html):
        old = m.group(0)
        new = _OPACITY_SUFFIX_RE.sub(_reduce_opacity, old)
        if new != old:
            edits.append((old, new))
    return edits


_SHADER_DIV_RE = re.compile(
    r'<div\b[^>]*\bclass="[^"]*\bomnia-shader\b[^"]*"[^>]*>', re.IGNORECASE
)


def dim_shader_edits(zone_html: str) -> list[tuple[str, str]]:
    """``(old, new)`` pairs that DIM the WebGL ``.omnia-shader`` backdrop (add
    ``opacity-40``) so a full-bleed background image isn't darkened by the
    animated shader on top of it. Targets ONLY the shader backdrop — never the
    ``.omnia-shader-over`` content layer — and skips an already-dimmed shader."""
    edits: list[tuple[str, str]] = []
    for m in _SHADER_DIV_RE.finditer(zone_html):
        old = m.group(0)
        cls_m = _CLASS_RE.search(old)
        cls = cls_m.group(1) if cls_m else ""
        toks = cls.split()
        # exact-token check excludes "omnia-shader-over" (a different single token)
        if "omnia-shader" not in toks:
            continue
        if any(t.startswith("opacity-") for t in toks):
            continue
        new = old.replace(f'class="{cls}"', f'class="{cls} opacity-40"', 1)
        if new != old:
            edits.append((old, new))
    return edits


__all__ = [
    "alt_of",
    "dim_shader_edits",
    "find_first_img",
    "is_fullbleed_bg",
    "is_image_request",
    "lighten_overlay_edits",
    "rebuild_img_with_gen",
]
