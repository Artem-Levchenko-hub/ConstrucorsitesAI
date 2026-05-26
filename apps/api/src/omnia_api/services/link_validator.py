"""Серверная страховка от «мёртвых» ссылок в сгенерированной статике.

Консервативно по задумке: ловим только однозначные тупики, чтобы не дёргать
дорогой repair-pass на ложных срабатываниях (R-05 YAGNI). Проверяем лишь
HTML-файлы и только ссылки (`<a href>`): кнопки и кросс-страничные ссылки
пропускаем — там слишком легко ошибиться (submit-кнопка без href, файл из
прошлого снапшота, JS-обработчик через addEventListener).
"""

from __future__ import annotations

import re

_HTML_SUFFIXES = (".html", ".htm")
_HREF = re.compile(r'href\s*=\s*["\']([^"\']*)["\']', re.IGNORECASE)
_ID = re.compile(r'\bid\s*=\s*["\']([^"\']+)["\']', re.IGNORECASE)

# href-значения, которые никуда не ведут — placeholder вместо реальной цели.
_DEAD_HREFS = {"", "#", "javascript:void(0)", "javascript:void(0);", "javascript:;"}
# Спец-фрагменты, скроллящие к верху документа даже без элемента с таким id.
_TOP_FRAGMENTS = {"top"}


def find_dead_links(files: dict[str, str]) -> list[str]:
    """Вернуть список проблем по мёртвым ссылкам в HTML-файлах.

    Пустой список → претензий нет. Каждая строка описывает одну конкретную ссылку
    в формате, который можно прямо отдать модели на исправление.
    """
    issues: list[str] = []
    for path, content in files.items():
        if not path.lower().endswith(_HTML_SUFFIXES):
            continue
        ids = set(_ID.findall(content))
        for raw in _HREF.findall(content):
            href = raw.strip()
            if href.lower() in _DEAD_HREFS:
                issues.append(f'{path}: ссылка ведёт в никуда — href="{href}"')
                continue
            if href.startswith("#") and len(href) > 1:
                anchor = href[1:]
                if anchor not in ids and anchor.lower() not in _TOP_FRAGMENTS:
                    issues.append(
                        f'{path}: якорь href="{href}" без секции с id="{anchor}"'
                    )
    return issues


# Section-id priorities for the inline repair. When we encounter a dead-link
# placeholder (`href="#"` etc), we redirect it to the first existing section
# matching one of these — primary CTA targets first, fallbacks last.
# If NONE of these sections exist on the page → strip the <a> wrapper (the
# inner text/HTML stays, just no longer clickable). Better than a clickable
# trap that goes nowhere.
_REPAIR_PREFERRED_TARGETS: tuple[str, ...] = (
    "contacts", "contact", "cta", "order", "booking", "pricing",
    "services", "catalog", "about", "top",
)


def _pick_repair_target(ids: set[str]) -> str | None:
    """Choose the best fallback anchor from existing sections in this HTML.

    Returns the chosen id (e.g. ``"contacts"``) or None if no acceptable
    section exists. Caller falls through to stripping the link entirely.
    """
    for candidate in _REPAIR_PREFERRED_TARGETS:
        if candidate in ids:
            return candidate
    return None


_DEAD_HREF_REGEX = re.compile(
    # Match `<a ... href="..."` where href value is dead. Negative lookahead
    # keeps captures tight to the opening <a tag.
    r'(<a\b[^>]*?\bhref\s*=\s*["\'])([^"\']*)(["\'])',
    re.IGNORECASE,
)


def repair_dead_links_inline(files: dict[str, str]) -> dict[str, str]:
    """Server-side dead-link repair without an LLM call.

    For each ``<a href>`` whose target is one of ``_DEAD_HREFS`` or an
    anchor pointing at a missing id:
      * If the page has at least one CTA-class section
        (``_REPAIR_PREFERRED_TARGETS``), rewrite the href to point there.
      * Otherwise leave the original href (the LLM-repair fallback in
        ``routers/messages.py`` can still kick in).

    This replaces the previous nuclear option (full LLM re-roll on ANY
    single dead link, costing the user a second ~₽30 generation pass).
    Returns a NEW dict — original ``files`` is not mutated.

    R-05 YAGNI: keep the heuristic narrow. We only repair the cases we
    KNOW the AI gets wrong (placeholder `#`). Real broken anchors that
    point at non-existent sections still flow through to LLM repair.
    """
    out: dict[str, str] = {}
    for path, content in files.items():
        if not path.lower().endswith(_HTML_SUFFIXES):
            out[path] = content
            continue
        ids = set(_ID.findall(content))
        target = _pick_repair_target(ids)
        if target is None:
            out[path] = content
            continue

        def _replace(m: re.Match[str]) -> str:
            href = m.group(2).strip()
            if href.lower() in _DEAD_HREFS:
                return f"{m.group(1)}#{target}{m.group(3)}"
            return m.group(0)

        out[path] = _DEAD_HREF_REGEX.sub(_replace, content)
    return out
