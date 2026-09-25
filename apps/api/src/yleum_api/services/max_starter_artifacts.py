"""Стартовые файлы приложения нельзя потерять незаметно.

У сгенерированного MAX-приложения есть четыре файла, без которых оно выглядит
исправным и не работает: две миграции, создающие технические таблицы, скрипт,
который эти миграции применяет, и конфигурация drizzle.

Потеря тихая из-за того, как миграции применяются. Скрипт берёт все ``*.sql`` из
``drizzle/``, сортирует по имени и запоминает применённые ПО ИМЕНИ в таблице
``__omnia_migrations``. Отсюда три способа сломать базу, не сломав сборку:

* файла нет — таблица не создастся, и никто не пожалуется до первого запроса;
* файл переименован — он применится повторно, уже поверх существующих объектов;
* новый файл с именем раньше применённого встанет в середину пройденного порядка
  и выполнится после того, что по замыслу шло позже.

Поэтому набор сверяется с эталоном ДО исполнения, а эталон читается с диска —
из тех же файлов шаблона, что попадают в образ. Ответ — список именованных кодов
без содержимого файлов: в отчёт не должны утекать чужие данные.
"""

from __future__ import annotations

from collections.abc import Mapping
from functools import lru_cache

from yleum_api.services.max_project_kit import _template_file

# Порядок важен: именно в нём миграции применяются, и именно после последней из
# них разрешено добавлять свои.
STARTER_MIGRATIONS: tuple[str, ...] = (
    "drizzle/0000_max_core.sql",
    "drizzle/0001_business_core.sql",
)
STARTER_APPLY_SCRIPT = "scripts/apply-migrations.mjs"
STARTER_DRIZZLE_CONFIG = "drizzle.config.ts"

_MIGRATION_DIR = "drizzle/"
_MIGRATION_SUFFIX = ".sql"
# Объявление технических таблиц. Его наличие и означает, что набор претендует на
# полноту и обязан нести миграции, которые эти таблицы создают.
_SCHEMA_PATH = "src/lib/db/schema.ts"

STARTER_MIGRATION_MISSING = "starter_migration_missing"
MIGRATION_ORDER_CHANGED = "migration_order_changed"
MATERIALIZER_SCRIPT_MISSING = "materializer_script_missing"
DRIZZLE_CONFIG_MISSING = "drizzle_config_missing"


@lru_cache(maxsize=1)
def starter_baseline() -> Mapping[str, str]:
    """Эталон с диска: те самые файлы шаблона, а не их описание в промпте."""
    return {
        path: _template_file(path)
        for path in (*STARTER_MIGRATIONS, STARTER_APPLY_SCRIPT, STARTER_DRIZZLE_CONFIG)
    }


def _present(files: Mapping[str, str], path: str) -> bool:
    # Имя в списке ничего не доказывает: пустой файл не создаст таблиц.
    return bool(files.get(path, "").strip())


def validate_starter_artifacts(
    files: Mapping[str, str],
    baseline: Mapping[str, str],
) -> list[str]:
    """Назвать всё, чем этот набор файлов отличается от рабочего эталона.

    Проверка не исполняет ничего и не открывает базу: она смотрит только на имена
    и содержимое. Возвращаются коды без дубликатов и без строк из самих файлов.
    """
    errors: list[str] = []

    def add(code: str) -> None:
        if code not in errors:
            errors.append(code)

    starter_migrations = [path for path in STARTER_MIGRATIONS if path in baseline]
    for path in starter_migrations:
        if not _present(files, path):
            add(STARTER_MIGRATION_MISSING)
        elif files[path] != baseline[path]:
            # Файл уже применён под этим именем, поэтому правка внутри него
            # никогда не выполнится — расхождение здесь молчаливо ломает схему.
            add(MIGRATION_ORDER_CHANGED)

    if starter_migrations:
        last_starter = max(starter_migrations)
        for path in files:
            if not path.startswith(_MIGRATION_DIR) or not path.endswith(_MIGRATION_SUFFIX):
                continue
            if path in starter_migrations:
                continue
            if path <= last_starter:
                # Встанет в середину уже пройденного порядка.
                add(MIGRATION_ORDER_CHANGED)

    if STARTER_APPLY_SCRIPT in baseline and not _present(files, STARTER_APPLY_SCRIPT):
        add(MATERIALIZER_SCRIPT_MISSING)
    if STARTER_DRIZZLE_CONFIG in baseline and not _present(files, STARTER_DRIZZLE_CONFIG):
        add(DRIZZLE_CONFIG_MISSING)

    return errors


_GAP_BY_CODE = {
    STARTER_MIGRATION_MISSING: (
        "The starter migrations that create the platform tables are missing. "
        "Restore them byte-for-byte: "
    ),
    MIGRATION_ORDER_CHANGED: (
        "Applied migrations are tracked by file name, so an edited or "
        "earlier-sorting migration never runs. Restore the starter files and add "
        "new migrations with a higher number instead: "
    ),
    MATERIALIZER_SCRIPT_MISSING: "Restore the migration runner: ",
    DRIZZLE_CONFIG_MISSING: "Restore the drizzle configuration: ",
}


def starter_artifact_gap(files: Mapping[str, str]) -> str | None:
    """Назвать агенту, что именно он потерял, пока это ещё можно вернуть.

    Судить можно только тот набор, который вообще претендует на полноту. Признак
    полноты — объявленная схема: если приложение объявляет технические таблицы,
    то миграции, которые их создают, обязаны быть рядом. Набор без объявления
    схемы — это фрагмент (отдельная проверка контракта, частичная правка), и
    требовать от него миграции значит ругаться не на того.

    Скрипт применения миграций и конфигурация drizzle здесь НЕ проверяются: они
    защищены от правки моделью и живут в рабочей области, а не в наборе файлов,
    которым оперирует платформа. Их сохранность проверяется там, где видна вся
    рабочая область.
    """
    if not files.get(_SCHEMA_PATH, "").strip():
        return None
    baseline = {
        path: content
        for path, content in starter_baseline().items()
        if path in STARTER_MIGRATIONS
    }
    errors = validate_starter_artifacts(files, baseline)
    if not errors:
        return None
    lost = sorted(path for path in baseline if files.get(path, "") != baseline[path])
    lead = _GAP_BY_CODE.get(errors[0], "Restore the platform starter files: ")
    return lead + ", ".join(lost) + ". Do not renumber or rewrite them."


__all__ = [
    "DRIZZLE_CONFIG_MISSING",
    "MATERIALIZER_SCRIPT_MISSING",
    "MIGRATION_ORDER_CHANGED",
    "STARTER_APPLY_SCRIPT",
    "STARTER_DRIZZLE_CONFIG",
    "STARTER_MIGRATIONS",
    "STARTER_MIGRATION_MISSING",
    "starter_artifact_gap",
    "starter_baseline",
    "validate_starter_artifacts",
]
