"""Pinned ReUI composition catalog for the existing design-agent pass.

Only upstream metadata is vendored.  ReUI source code is never executed and
generated projects never install ReUI, shadcn, or its dependencies.  The
retriever turns a large local catalog into at most three short composition
references that the current stack can adapt safely.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

REUI_REPOSITORY = "https://github.com/keenthemes/reui"
REUI_COMMIT = "0daf79dff3ebe0ede7fa05bedcaefeaac93a8949"
REUI_LICENSE = "MIT"
REUI_SOURCE = f"keenthemes/reui@{REUI_COMMIT}"

_CATALOG_PATH = Path(__file__).resolve().parents[3] / "data" / "reui_catalog.json"

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class ReuiPattern:
    """One upstream composition reference, without executable source."""

    id: str
    category: str
    name: str
    title: str
    description: str
    source_url: str


_ARCHETYPE_DEFAULTS: dict[str, tuple[str, ...]] = {
    "fitness-health": (
        "card/c-card-15",
        "progress/c-progress-4",
        "timeline/c-timeline-11",
    ),
    "commerce": (
        "card/c-card-7",
        "filters/c-filters-3",
        "sheet/c-sheet-4",
    ),
    "booking-service": (
        "calendar/c-calendar-27",
        "stepper/c-stepper-10",
        "sheet/c-sheet-1",
    ),
    "communication": (
        "item/c-item-12",
        "empty/c-empty-17",
        "tabs/c-tabs-7",
    ),
    "learning-content": (
        "progress/c-progress-7",
        "stepper/c-stepper-10",
        "tabs/c-tabs-9",
    ),
    "operations": (
        "data-grid/c-data-grid-22",
        "filters/c-filters-7",
        "timeline/c-timeline-6",
    ),
    "analytics": (
        "card/c-card-15",
        "chart/c-chart-13",
        "filters/c-filters-5",
    ),
    "productivity": (
        "item/c-item-4",
        "empty/c-empty-4",
        "sheet/c-sheet-1",
    ),
}

# Russian and English product language mapped to verified upstream examples.
# Scores accumulate, so a pattern relevant to both the archetype and the brief
# wins over a merely keyword-matched decorative example.
_INTENTS: tuple[tuple[tuple[str, ...], tuple[str, ...]], ...] = (
    (("профил", "аккаунт", "profile", "account"), ("sheet/c-sheet-1",)),
    (("форм", "анкета", "настройк", "form", "settings"), ("field/c-field-9",)),
    (
        ("ошибк", "валидац", "invalid", "validation"),
        ("field/c-field-11", "input-group/c-input-group-3"),
    ),
    (
        ("календар", "запис", "бронь", "слот", "calendar", "booking", "appointment"),
        ("calendar/c-calendar-27", "stepper/c-stepper-10"),
    ),
    (("таблиц", "реестр", "crud", "table", "grid"), ("data-grid/c-data-grid-22",)),
    (("фильтр", "поиск", "filter", "search"), ("filters/c-filters-5",)),
    (("пуст", "нет данных", "empty", "no data"), ("empty/c-empty-4",)),
    (("истори", "активност", "лента", "history", "activity"), ("timeline/c-timeline-11",)),
    (
        ("статист", "аналит", "метрик", "график", "progress", "analytics", "chart"),
        ("card/c-card-15", "chart/c-chart-13"),
    ),
    (("сообщен", "чат", "диалог", "message", "chat"), ("empty/c-empty-17", "item/c-item-12")),
)

_CATEGORY_GUIDANCE = {
    "calendar": (
        "дата слева/сверху, доступные слоты рядом, выбранное состояние видно до подтверждения"
    ),
    "card": "один главный показатель, короткое сравнение и только одно контекстное действие",
    "chart": (
        "график отвечает на один вопрос, имеет подписи и текстовый итог; "
        "без декоративной 3D-графики"
    ),
    "data-grid": (
        "поиск/фильтр рядом с таблицей, явные состояния loading/empty и действия на строке"
    ),
    "empty": (
        "тематическая иконка, ясная причина, одна следующая кнопка; не оставлять голый пустой экран"
    ),
    "field": "видимая подпись, подсказка до ввода, ошибка рядом с полем и сохранение внизу формы",
    "filters": "показывать активные условия чипами, давать сброс и не прятать результат фильтрации",
    "input-group": "объединять только связанные ввод и действие; сохранять подпись и ошибку поля",
    "item": (
        "вся строка читается как единый объект: иконка/аватар, заголовок, метаданные, одно действие"
    ),
    "progress": "текущее значение и цель читаются без цвета; статус объясняет следующий шаг",
    "sheet": "короткая мобильная задача, фиксированный заголовок и доступные действия Save/Cancel",
    "stepper": (
        "видны текущий, завершённые и будущие шаги; контент меняется без потери введённых данных"
    ),
    "tabs": (
        "2–4 короткие вкладки, активная различима не только цветом, контент не дублирует навигацию"
    ),
    "timeline": (
        "обратная хронология, компактные метки времени, важное событие сильнее служебного текста"
    ),
}


def _as_pattern(raw: dict[str, Any]) -> ReuiPattern | None:
    required = ("id", "category", "name", "title", "description", "source_url")
    if not all(isinstance(raw.get(key), str) and raw[key] for key in required):
        return None
    return ReuiPattern(**{key: raw[key] for key in required})


@lru_cache(maxsize=1)
def load_reui_catalog() -> tuple[ReuiPattern, ...]:
    """Load and validate the pinned metadata snapshot; fail soft when absent."""
    try:
        raw = json.loads(_CATALOG_PATH.read_text(encoding="utf-8"))
        source = raw.get("source", {})
        if source.get("commit") != REUI_COMMIT or source.get("license") != REUI_LICENSE:
            raise ValueError("ReUI catalog provenance does not match the runtime pin")
        patterns = tuple(
            pattern
            for item in raw.get("items", ())
            if isinstance(item, dict) and (pattern := _as_pattern(item)) is not None
        )
        if not patterns:
            raise ValueError("ReUI catalog has no valid patterns")
        return patterns
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        log.warning("ReUI catalog unavailable: %s", exc)
        return ()


def _pattern_index() -> dict[str, ReuiPattern]:
    return {pattern.id: pattern for pattern in load_reui_catalog()}


def select_reui_patterns(
    *, brief: str, archetype: str, mobile: bool, limit: int = 3
) -> tuple[ReuiPattern, ...]:
    """Return a deterministic, bounded composition set for one product brief."""
    if limit <= 0:
        return ()
    index = _pattern_index()
    if not index:
        return ()

    scores: dict[str, int] = {}
    for position, pattern_id in enumerate(
        _ARCHETYPE_DEFAULTS.get(archetype, _ARCHETYPE_DEFAULTS["productivity"])
    ):
        scores[pattern_id] = max(scores.get(pattern_id, 0), 60 - position)

    low = (brief or "").casefold()
    explicit_table = any(cue in low for cue in ("таблиц", "реестр", "table", "grid"))
    for cues, pattern_ids in _INTENTS:
        matches = sum(cue in low for cue in cues)
        if not matches:
            continue
        for position, pattern_id in enumerate(pattern_ids):
            scores[pattern_id] = scores.get(pattern_id, 0) + 80 + matches * 5 - position

    ranked: list[tuple[int, str, ReuiPattern]] = []
    for pattern_id, score in scores.items():
        pattern = index.get(pattern_id)
        if pattern is None:
            continue
        # A desktop grid is useful in a MAX brief only when the owner explicitly
        # asked for tabular data. The prompt then requires a stacked mobile form.
        if mobile and pattern.category == "data-grid" and not explicit_table:
            continue
        ranked.append((score, pattern.id, pattern))
    ranked.sort(key=lambda row: (-row[0], row[1]))
    return tuple(row[2] for row in ranked[: min(limit, 3)])


def format_reui_prompt(patterns: tuple[ReuiPattern, ...], *, mobile: bool) -> str:
    """Format selected patterns as compact, stack-safe agent guidance."""
    if not patterns:
        return ""
    lines = [
        f"REUI COMPOSITION REFERENCES (`{REUI_SOURCE}`, local metadata snapshot):",
        "  use composition ideas only; DO NOT run shadcn/ReUI CLI, install packages, "
        "or copy imports",
        "  implement with the components and dependencies already present in this project",
    ]
    for pattern in patterns:
        guidance = _CATEGORY_GUIDANCE.get(pattern.category, pattern.description)
        mobile_note = (
            "; at 390px use one column/bottom sheet and turn wide rows into stacked items"
            if mobile and pattern.category in {"data-grid", "filters", "sheet"}
            else ""
        )
        lines.append(f"  • {pattern.id} — {pattern.title}: {guidance}{mobile_note}")
    return "\n".join(lines)


def format_reui_markdown(patterns: tuple[ReuiPattern, ...]) -> str:
    """Persist selected references in DESIGN.md for later edits."""
    if not patterns:
        return ""
    lines = [
        "## ReUI composition references",
        "",
        f"Metadata source: [`{REUI_SOURCE}`]({REUI_REPOSITORY}/tree/{REUI_COMMIT}) (MIT).",
        "Use these as composition references only; keep the project's existing UI stack.",
        "",
    ]
    for pattern in patterns:
        guidance = _CATEGORY_GUIDANCE.get(pattern.category, pattern.description)
        lines.append(f"- [`{pattern.id}`]({pattern.source_url}) — {pattern.title}: {guidance}.")
    return "\n".join(lines)


__all__ = [
    "REUI_COMMIT",
    "REUI_LICENSE",
    "REUI_REPOSITORY",
    "REUI_SOURCE",
    "ReuiPattern",
    "format_reui_markdown",
    "format_reui_prompt",
    "load_reui_catalog",
    "select_reui_patterns",
]
