"""V1.10 cheap-core — preset provenance & staleness ratchet (money-free).

Контекст (§5★ ROADMAP, столп 1, задача V1.10): «вкус заморожен на снапшоте
2026-06-12». Без машинного якоря этот факт = проза, которая тихо протухает,
а пресет-без-источника тихо дефолтит в REFINED-MINIMAL в проде. Этот модуль
даёт ДЕТЕРМИНИРОВАННЫЙ храповик (без браузера / LLM / сети) над таблицей
``PRESETS`` — дешёвое falsifiable-ядро задачи V1.10:

1. **CITATION** — каждый пресет ОБЯЗАН цитировать непустой, структурно-валидный
   ``reference_url`` (источник вкуса). Пресет без цитаты или с мусором падает
   в CI, а не молча уезжает в дефолт в проде.
2. **DISTINCTNESS** — два пресета не делят один ``reference_url`` (тихий
   shared-default = два «разных» пресета копируют один сайт = ложная
   палитра вкуса).
3. **STALENESS** — машинный ``TASTE_SNAPSHOT_DATE``, сверяемый с датой
   «Сгенерено …» в таблице правил (:func:`read_rule_table_snapshot_date`);
   рассинхрон = forced acknowledgement, а не тихий дрейф.
4. **COVERAGE-GAP** — ниша без детерминированного preset-покрытия эмитит
   GAP-флаг (reuse ``classify_preset_sync``, R-04), а не молча уезжает в
   дефолт-пресет.

Всё money-free by construction: читает статические декларации ``PRESETS`` +
локальный markdown. ``reference_url`` валидируется СТРУКТУРНО (схема + хост),
без сетевого хита — храповик обязан быть replayable офлайн.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from urllib.parse import urlparse

from omnia_api.services.design_presets import PRESETS, DesignPreset
from omnia_api.services.preset_classifier import classify_preset_sync

# Дата захвата корпуса вкуса (Mobbin-паттерны → правила,
# ``docs/plans/mobbin-patterns-to-rules.md``). Машинный якорь под прозу
# «вкус заморожен на снапшоте 2026-06-12». Сверяется тестом с датой
# «Сгенерено …» в самой таблице — рассинхрон обязан быть осознанным.
TASTE_SNAPSHOT_DATE = "2026-06-12"

# Путь к таблице правил относительно корня репозитория. НЕ читается на
# импорте (контейнер api может не нести docs/) — только тест передаёт
# конкретный путь в :func:`read_rule_table_snapshot_date`.
RULE_TABLE_RELPATH = "docs/plans/mobbin-patterns-to-rules.md"

# «Сгенерено 2026-06-12» в шапке таблицы правил.
_RULE_TABLE_DATE_RE = re.compile(r"Сгенерено\s+(\d{4}-\d{2}-\d{2})")

# control chars / whitespace внутри URL = мусор.
_URL_BAD_CHARS_RE = re.compile(r"\s")


def check_reference_url(url: str | None) -> str | None:
    """Структурная валидация ``reference_url``.

    Возвращает причину нарушения (короткая строка) или ``None`` если URL
    валиден. Сети НЕ касается — только схема + хост + отсутствие мусорных
    символов, чтобы храповик гонялся офлайн.
    """
    if url is None or not url.strip():
        return "empty"
    if _URL_BAD_CHARS_RE.search(url):
        return "whitespace"
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return f"bad-scheme:{parsed.scheme or '∅'}"
    if not parsed.netloc:
        return "no-host"
    return None


@dataclass(frozen=True)
class ProvenanceReport:
    """Результат прогона храповика провенанса над таблицей пресетов."""

    total: int
    violations: tuple[tuple[str, str], ...]  # (preset_id, reason)

    @property
    def ok(self) -> bool:
        return not self.violations

    @property
    def cited(self) -> int:
        """Сколько пресетов прошли citation+distinctness без нарушений."""
        bad = {pid for pid, _ in self.violations}
        return self.total - len(bad)


def evaluate(presets: dict[str, DesignPreset] | None = None) -> ProvenanceReport:
    """Прогнать citation + distinctness над таблицей пресетов.

    Money-free: читает только статические декларации. Нарушения:
      * ``reference_url`` пустой/мусорный → ``("<id>", "<reason>")``;
      * два пресета делят один ``reference_url`` → оба получают
        ``("<id>", "duplicate-reference:<url>")``.
    """
    table = PRESETS if presets is None else presets
    violations: list[tuple[str, str]] = []

    # CITATION — структурная валидность каждого reference_url.
    for pid, preset in table.items():
        reason = check_reference_url(preset.reference_url)
        if reason is not None:
            violations.append((pid, reason))

    # DISTINCTNESS — нормализованный URL не должен повторяться. Считаем
    # только по пресетам с валидной (непустой) цитатой, чтобы не дублировать
    # «empty»-нарушение шумом.
    seen: dict[str, list[str]] = {}
    for pid, preset in table.items():
        if check_reference_url(preset.reference_url) is not None:
            continue
        key = preset.reference_url.strip().rstrip("/").lower()
        seen.setdefault(key, []).append(pid)
    for key, pids in seen.items():
        if len(pids) > 1:
            for pid in pids:
                violations.append((pid, f"duplicate-reference:{key}"))

    return ProvenanceReport(total=len(table), violations=tuple(violations))


def read_rule_table_snapshot_date(doc_path: str | Path) -> str | None:
    """Достать дату «Сгенерено YYYY-MM-DD» из таблицы правил.

    Money-free: читает локальный markdown. Возвращает строку даты или
    ``None`` если маркер не найден. Тест сверяет с :data:`TASTE_SNAPSHOT_DATE`
    — рассинхрон ловит молчаливый дрейф вкуса.
    """
    path = Path(doc_path)
    if not path.is_file():
        return None
    match = _RULE_TABLE_DATE_RE.search(path.read_text(encoding="utf-8"))
    return match.group(1) if match else None


def snapshot_age_days(today: date, snapshot: str = TASTE_SNAPSHOT_DATE) -> int:
    """Возраст снапшота вкуса в днях относительно ``today`` (для STALENESS-нуджа)."""
    return (today - date.fromisoformat(snapshot)).days


def coverage_gap(
    project_name: str,
    first_prompt: str = "",
    template: str = "freeform",
) -> bool:
    """``True`` если ниша НЕ покрыта детерминированным preset-сигналом.

    Reuse :func:`classify_preset_sync` (R-04): substring + heuristic без LLM.
    Пустой результат = ни один пресет уверенно не сматчился → в проде проект
    молча уехал бы в дефолт. GAP-флаг делает это видимым, а не тихим.
    """
    return classify_preset_sync(project_name, template, first_prompt) == ""
