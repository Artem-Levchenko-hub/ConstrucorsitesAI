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


# Real in-page anchors the document itself navigates to: ``href="#id"`` with at
# least one letter after ``#`` (so it never matches a dead ``href="#"``).
_NAV_ANCHOR = re.compile(r'href\s*=\s*["\']#([A-Za-z][^"\']*)["\']', re.IGNORECASE)
# ``#top`` scrolls to the top of ANY document even without an ``id="top"``
# element (HTML spec) — a universal, always-valid redirect target.
_UNIVERSAL_FALLBACK_TARGET = "top"


def _pick_dead_link_target(ids: set[str], content: str) -> str:
    """Pick where to redirect a dead placeholder link in the full-page pass.

    Unlike ``_pick_repair_target`` (used by the conservative edit-orphan fixer,
    which leaves a link alone when no CTA section exists), the full-page
    dead-link pass must NEVER leave a placeholder clickable-but-dead. It always
    returns a target, in priority order:
      1. a commercial CTA-class section (conversion intent, as before);
      2. the first section the page itself navigates to (an ``id`` that is
         present AND referenced by an in-page ``href="#id"``) — niche-agnostic,
         so editorial pages (blog/portfolio sections named
         ``hero``/``archive``/``manifesto``) repair to a real section instead of
         no-op;
      3. ``"top"`` — scrolls to the top of any document, valid even with no
         ``id="top"`` element (footer/policy/tag links on a detail page that has
         no local sections to point at).

    Dogfood run #41 (BS-41): a travel blog's sections were
    ``hero``/``archive``/``manifesto``/``subscribe`` — none commercial — so
    ``_pick_repair_target`` returned None and the inline pass repaired 0 of 28
    dead links. Tiers 2-3 make the repair work on any archetype.
    """
    commercial = _pick_repair_target(ids)
    if commercial is not None:
        return commercial
    for anchor in _NAV_ANCHOR.findall(content):
        if anchor in ids:
            return anchor
    return _UNIVERSAL_FALLBACK_TARGET


_DEAD_HREF_REGEX = re.compile(
    # Match `<a ... href="..."` where href value is dead. Negative lookahead
    # keeps captures tight to the opening <a tag.
    r'(<a\b[^>]*?\bhref\s*=\s*["\'])([^"\']*)(["\'])',
    re.IGNORECASE,
)


def repair_dead_links_inline(files: dict[str, str]) -> dict[str, str]:
    """Server-side dead-link repair without an LLM call.

    For each ``<a href>`` whose target is one of ``_DEAD_HREFS``, rewrite the
    href to the best available anchor (``_pick_dead_link_target``): a CTA-class
    section, else the page's own first nav-referenced section, else ``#top``.
    A placeholder is therefore never left dead, on any archetype.

    This replaces the previous nuclear option (full LLM re-roll on ANY
    single dead link, costing the user a second ~₽30 generation pass).
    Returns a NEW dict — original ``files`` is not mutated.

    R-05 YAGNI: keep the heuristic narrow. We only repair the cases we
    KNOW the AI gets wrong (placeholder `#`). Real broken anchors that
    point at non-existent sections still flow through to LLM repair.

    The redirect target is chosen niche-agnostically (``_pick_dead_link_target``):
    a commercial CTA section, else the page's own first nav-referenced section,
    else ``#top``. So a placeholder never survives just because the page is an
    editorial/content archetype rather than a storefront (dogfood run #41).
    """
    out: dict[str, str] = {}
    for path, content in files.items():
        if not path.lower().endswith(_HTML_SUFFIXES):
            out[path] = content
            continue
        ids = set(_ID.findall(content))
        target = _pick_dead_link_target(ids, content)

        def _replace(m: re.Match[str]) -> str:
            href = m.group(2).strip()
            if href.lower() in _DEAD_HREFS:
                return f"{m.group(1)}#{target}{m.group(3)}"
            return m.group(0)

        out[path] = _DEAD_HREF_REGEX.sub(_replace, content)
    return out


def repair_orphaned_anchors_inline(
    old_files: dict[str, str], new_files: dict[str, str]
) -> dict[str, str]:
    """Repair in-page anchors that an EDIT orphaned, deterministically (no LLM).

    A surgical edit like «убери секцию отзывов» deletes a section's ``id`` but
    leaves nav links ``href="#reviews"`` pointing at it — a dead anchor the
    normal dead-link pass never sees (it's skipped on surgical edits to avoid
    touching links the user didn't mention). We repair ONLY anchors that were
    VALID before this edit (the id existed in ``old``) and became dangling after
    (the id is gone in ``new``) — strictly the side-effect of the requested
    change, never a pre-existing dead link. Each such ``<a href="#X">`` is
    redirected to the best surviving CTA-class section (same fallback policy as
    ``repair_dead_links_inline``); if none exists the link is left untouched.
    Returns a NEW dict — inputs are not mutated.
    """
    out: dict[str, str] = {}
    for path, content in new_files.items():
        if not path.lower().endswith(_HTML_SUFFIXES):
            out[path] = content
            continue
        old_ids = set(_ID.findall(old_files.get(path, "")))
        new_ids = set(_ID.findall(content))
        # ids the edit removed — anchors pointing here were valid before, dead now.
        orphaned = old_ids - new_ids
        if not orphaned:
            out[path] = content
            continue
        target = _pick_repair_target(new_ids)
        if target is None or target in orphaned:
            out[path] = content
            continue

        def _replace(m: re.Match[str]) -> str:
            href = m.group(2).strip()
            if href.startswith("#") and href[1:] in orphaned:
                return f"{m.group(1)}#{target}{m.group(3)}"
            return m.group(0)

        out[path] = _DEAD_HREF_REGEX.sub(_replace, content)
    return out
