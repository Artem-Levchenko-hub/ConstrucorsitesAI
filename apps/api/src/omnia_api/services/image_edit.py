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


__all__ = ["alt_of", "find_first_img", "is_image_request", "rebuild_img_with_gen"]
