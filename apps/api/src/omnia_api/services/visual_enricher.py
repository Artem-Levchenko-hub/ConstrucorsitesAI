"""Post-process: гарантировать декор в каждой `<section>` сгенерённого HTML.

Модели (особенно Haiku) часто игнорят «положи .bg-aurora/.blob/SVG-pattern»
из system-prompt и выдают голые `<section class="py-24">`. Owner видит «плоский
AI-сайт». Этот pass проходит каждый `<section>` и, если в первых 600 байт нет
ни одного маркера визуального слоя (bg-aurora, bg-mesh, blob, orb, <svg,
radial-gradient, linear-gradient), вставляет сразу после открывающего тега
декоративный блок (mesh-gradient / SVG-паттерн / blob-кластер / волны).
Приёмы чередуются циклически, чтобы соседние секции не выглядели одинаково.

Запускается только для статических HTML-файлов. JSX/TSX не трогает —
fullstack-режим уходит через next/image + dev-container, там декор
достигается через prompt + дальнейшие итерации.
"""

from __future__ import annotations

import logging
import re
from itertools import cycle

log = logging.getLogger(__name__)

# Маркеры — если найден хоть один в первых ``_HEAD_BYTES`` символов после
# открывающего <section>, считаем что декор уже есть и не трогаем секцию.
_HEAD_BYTES = 600
_DECOR_MARKERS = re.compile(
    r"""(
        bg-aurora | bg-mesh | bg-grain |
        bg-gradient-to | bg-gradient | from-\w+ |
        \bblob\b | \borb\b | \.grain |
        <svg | radial-gradient | linear-gradient |
        data-decor= | pattern\s+id=
    )""",
    re.IGNORECASE | re.VERBOSE,
)

# Один <section …> тег с захваченными атрибутами.
_SECTION_OPEN = re.compile(
    r"<section\b(?P<attrs>[^>]*)>",
    re.IGNORECASE,
)


# Чередующиеся декоры — каждый блок добавляется первым ребёнком секции,
# абсолютно позиционирован, pointer-events:none, z-index ниже контента.
# Цвета через CSS-переменные (--brand/--accent) — подхватят палитру проекта.
_DECOR_VARIANTS: list[str] = [
    # 1. Mesh-gradient через два radial-gradient — самый дорогой эффект.
    """\
<div aria-hidden="true" data-decor="mesh" class="pointer-events-none absolute inset-0 -z-0 overflow-hidden">
  <div class="absolute inset-0 opacity-60" style="background:radial-gradient(circle at 18% 24%, var(--brand,#6366f1) 0%, transparent 45%), radial-gradient(circle at 82% 76%, var(--accent,#ec4899) 0%, transparent 50%); filter:blur(70px)"></div>
</div>""",
    # 2. SVG dot-pattern по всей секции — лёгкий tech-фон.
    """\
<svg aria-hidden="true" data-decor="dots" class="pointer-events-none absolute inset-0 -z-0 h-full w-full opacity-25">
  <defs>
    <pattern id="dec-dots" x="0" y="0" width="28" height="28" patternUnits="userSpaceOnUse">
      <circle cx="2" cy="2" r="1.4" fill="currentColor"/>
    </pattern>
  </defs>
  <rect width="100%" height="100%" fill="url(#dec-dots)"/>
</svg>""",
    # 3. Кластер двух гради-blob'ов по углам.
    """\
<div aria-hidden="true" data-decor="blobs" class="pointer-events-none absolute inset-0 -z-0 overflow-hidden">
  <div class="absolute -left-32 top-1/4 h-[28rem] w-[28rem] rounded-full opacity-30" style="background:radial-gradient(circle, var(--brand,#6366f1), transparent 70%); filter:blur(60px)"></div>
  <div class="absolute -right-24 bottom-1/4 h-[24rem] w-[24rem] rounded-full opacity-30" style="background:radial-gradient(circle, var(--accent,#ec4899), transparent 70%); filter:blur(60px)"></div>
</div>""",
    # 4. SVG-волны на нижней кромке секции — для разделителей.
    """\
<svg aria-hidden="true" data-decor="waves" class="pointer-events-none absolute inset-x-0 bottom-0 -z-0 w-full" viewBox="0 0 1440 200" preserveAspectRatio="none" style="height:140px">
  <path d="M0,80 C360,160 720,0 1440,100 L1440,200 L0,200 Z" fill="currentColor" opacity="0.06"/>
  <path d="M0,120 C480,40 960,200 1440,140 L1440,200 L0,200 Z" fill="currentColor" opacity="0.04"/>
</svg>""",
    # 5. Диагональные линии — geometry/architecture vibe.
    """\
<svg aria-hidden="true" data-decor="lines" class="pointer-events-none absolute inset-0 -z-0 h-full w-full opacity-20">
  <defs>
    <pattern id="dec-lines" x="0" y="0" width="60" height="60" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
      <line x1="0" y1="0" x2="0" y2="60" stroke="currentColor" stroke-width="1"/>
    </pattern>
  </defs>
  <rect width="100%" height="100%" fill="url(#dec-lines)"/>
</svg>""",
    # 6. Один крупный orb + сетка концентрических кругов — fintech/hero feel.
    """\
<div aria-hidden="true" data-decor="orb" class="pointer-events-none absolute inset-0 -z-0 overflow-hidden">
  <div class="absolute right-[-10%] top-[-15%] h-[36rem] w-[36rem] rounded-full opacity-40" style="background:radial-gradient(circle, var(--accent,#f59e0b), transparent 65%); filter:blur(50px)"></div>
  <div class="absolute inset-0 opacity-[0.05]" style="background-image:repeating-radial-gradient(circle at 50% 50%, transparent 0, currentColor 1px, transparent 2px, transparent 6px)"></div>
</div>""",
]


def _has_visual_layer(open_tag: str, body_head: str) -> bool:
    """True если в открывающем теге секции или первых ``_HEAD_BYTES`` body
    уже встречается маркер визуального слоя — тогда не трогаем секцию."""
    haystack = f"{open_tag} {body_head}"
    return _DECOR_MARKERS.search(haystack) is not None


def _ensure_relative(open_tag_attrs: str) -> str:
    """Section с абсолютным декором должна иметь position:relative.

    Если в class= уже есть `relative` — оставляем. Иначе добавляем класс.
    """
    if re.search(r"\brelative\b", open_tag_attrs):
        return open_tag_attrs
    m = re.search(r'class\s*=\s*"([^"]*)"', open_tag_attrs)
    if m:
        existing = m.group(1)
        prefixed = f'class="relative {existing}"'.replace("  ", " ")
        return open_tag_attrs.replace(m.group(0), prefixed)
    return f' class="relative"{open_tag_attrs}'


def enrich_html(content: str) -> tuple[str, int, int]:
    """Inject декор в каждый `<section>` без визуального слоя.

    Returns ``(new_content, enriched_count, total_sections)``. Не падает
    никогда — на любой ошибке возвращает оригинал.
    """
    try:
        sections = list(_SECTION_OPEN.finditer(content))
        if not sections:
            return content, 0, 0

        variants = cycle(_DECOR_VARIANTS)
        new_content = content
        enriched = 0
        total = len(sections)
        for m in reversed(sections):
            attrs = m.group("attrs") or ""
            open_end = m.end()
            body_head = new_content[open_end : open_end + _HEAD_BYTES]
            if _has_visual_layer(attrs, body_head):
                continue
            new_attrs = _ensure_relative(attrs)
            new_open = f"<section{new_attrs}>"
            decor = next(variants)
            replacement = new_open + "\n" + decor + "\n"
            new_content = (
                new_content[: m.start()] + replacement + new_content[open_end:]
            )
            enriched += 1
        return new_content, enriched, total
    except Exception:  # noqa: BLE001 — pipeline не должен падать на enricher
        log.exception("visual_enricher.enrich_html failed; returning original")
        return content, 0, 0


def enrich_files(files: dict[str, str]) -> tuple[dict[str, str], int, int]:
    """Прогнать enrich_html по всем .html/.htm файлам.

    JSX/TSX не трогаем — fullstack-режим уходит через next/image и
    отдельные prompt-инструкции.
    """
    new_files = dict(files)
    total_enriched = 0
    total_sections = 0
    for path, content in files.items():
        low = path.lower()
        if not (low.endswith(".html") or low.endswith(".htm")):
            continue
        new_content, enriched, total = enrich_html(content)
        if enriched:
            new_files[path] = new_content
        total_enriched += enriched
        total_sections += total
    return new_files, total_enriched, total_sections
