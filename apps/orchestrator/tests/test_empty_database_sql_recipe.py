"""Приложение с пустой базой должно откатываться, а не упираться в файл.

24.09.2026: откат исправного MAX-приложения с пустой базой отказывал с
формулировкой «В выбранной версии не хватает файлов: настройка базы
(drizzle.config.ts)». Проверено на живом приложении: из четырёх требуемых
файлов три есть, а `drizzle.config.ts` нет и быть не может — современный шаблон
его не содержит, миграции применяет доверенный контейнер из своего образа.
Описание схемы было на месте, просто в другом виде: файлы `drizzle/*.sql`.

Прежние два рецепта требовали либо исполнить исторический служебный скрипт,
либо запустить drizzle-kit. Третий не исполняет ничего: контроллер сам
применяет SQL по порядку имён. Риск у него строго меньше обоих прежних, потому
что чужой код не запускается вовсе.

Закрепляется и то, что рецепт работает, и то, что он не подменяет собой отказ
там, где восстанавливать действительно нечего.
"""

from __future__ import annotations

import pytest

from yleum_orchestrator.services.code_restoration_engine import (
    empty_materializer_blocker,
    historical_sql_migrations,
)

_SCHEMA = "src/lib/db/schema.ts"
# Ровно тот набор, что лежит в рабочей области живого приложения.
_MODERN_APP = {
    "drizzle/0000_max_core.sql": 'CREATE TABLE IF NOT EXISTS "max_users" (id uuid);',
    "drizzle/0001_business_core.sql": 'CREATE TABLE IF NOT EXISTS "max_consents" (id uuid);',
    "drizzle/0002_notes.sql": 'CREATE TABLE IF NOT EXISTS "notes" (id uuid);',
    _SCHEMA: "export const notes = pgTable('notes', {});",
    "package.json": '{"name":"app"}',
    "pnpm-lock.yaml": "lockfileVersion: '9.0'",
}


def test_a_modern_app_without_drizzle_config_is_restorable() -> None:
    """Живой случай 24.09: файла нет, а схема есть — откат обязан идти."""
    migrations = historical_sql_migrations(_MODERN_APP)

    assert migrations is not None
    assert [name for name, _ in migrations] == [
        "drizzle/0000_max_core.sql",
        "drizzle/0001_business_core.sql",
        "drizzle/0002_notes.sql",
    ]


def test_the_owner_is_no_longer_blocked_by_a_file_that_cannot_exist() -> None:
    assert empty_materializer_blocker(_MODERN_APP) is None


def test_migrations_are_applied_in_name_order() -> None:
    """Порядок имён — это и есть порядок применения; перепутать его нельзя."""
    shuffled = dict(reversed(list(_MODERN_APP.items())))

    names = [name for name, _ in historical_sql_migrations(shuffled) or []]

    assert names == sorted(names)


def test_a_version_without_migrations_is_still_refused() -> None:
    # Нечего применять — значит нечего и восстанавливать; молчать нельзя.
    files = {k: v for k, v in _MODERN_APP.items() if not k.startswith("drizzle/")}

    assert historical_sql_migrations(files) is None
    assert empty_materializer_blocker(files) is not None


def test_an_empty_migration_file_counts_as_loss_not_as_nothing_to_do() -> None:
    """Пустой файл миграции не создаёт объектов — это потеря, а не пустая работа."""
    files = dict(_MODERN_APP, **{"drizzle/0002_notes.sql": "   \n"})

    assert historical_sql_migrations(files) is None


def test_a_version_without_a_schema_declaration_is_refused() -> None:
    # Набор без объявления схемы — фрагмент, а не полная версия.
    files = dict(_MODERN_APP, **{_SCHEMA: ""})

    assert historical_sql_migrations(files) is None


@pytest.mark.parametrize("path", ["drizzle/readme.md", "drizzle/meta/_journal.json"])
def test_only_sql_files_are_treated_as_migrations(path: str) -> None:
    files = dict(_MODERN_APP, **{path: "не миграция"})

    names = [name for name, _ in historical_sql_migrations(files) or []]

    assert path not in names
