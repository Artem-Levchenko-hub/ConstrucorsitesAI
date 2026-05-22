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
