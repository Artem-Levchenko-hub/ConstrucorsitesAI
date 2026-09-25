"""Упавший запуск не имеет права оставить след в схеме живой базы.

NEW-27. Команда сборки, которую вызывает агент, выполняет применение миграций по
живой базе ячейки. Договор миграций при этом проверялся ПОСЛЕ того, как агент
закончил работу. Между этими двумя моментами и живёт дефект: агент пишет
недопустимую миграцию, зовёт сборку, миграция применяется, потом договор
отклоняет результат, запуск падает, версия не публикуется — а схема уже другая.

Снаружи это выглядит как «ничего не произошло, попробуйте ещё раз»: ни новой
версии, ни списания, ни изменений в бизнес-данных. Незаверсионированной остаётся
только схема, и именно поэтому такой след находят последним.

Проверка здесь одна и прямая: при нарушении договора команда сборки не должна
уйти в ячейку вовсе. Ноль отправленных команд — ноль DDL.
"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from yleum_api.services.agent_builder import Action

from .test_project_cell_executor import _prepare_executor

pytestmark = pytest.mark.asyncio

_STARTER = {
    "drizzle/0000_max_core.sql": 'CREATE TABLE IF NOT EXISTS "max_users" ("id" uuid PRIMARY KEY);',
    "drizzle/0001_business_core.sql": (
        'CREATE TABLE IF NOT EXISTS "max_catalog_items" ("id" uuid PRIMARY KEY);'
    ),
    "scripts/apply-migrations.mjs": "// platform-owned",
    "src/lib/db/schema.ts": 'export const maxUsers = pgTable("max_users", {});',
    "src/app/page.tsx": "export default function Page() { return null }",
}


async def _executor(monkeypatch: pytest.MonkeyPatch, db_session: AsyncSession, engine: AsyncEngine):
    return await _prepare_executor(
        monkeypatch, db_session, engine, snapshot_files=dict(_STARTER)
    )


async def test_failed_append_only_repair_does_not_mutate_live_schema(
    monkeypatch: pytest.MonkeyPatch, db_session: AsyncSession, test_engine: AsyncEngine
) -> None:
    """Миграция, встающая раньше уже применённой, не должна дойти до базы."""
    harness = await _executor(monkeypatch, db_session, test_engine)
    await harness.handle.execute(
        Action(
            "write_file",
            {
                "path": "drizzle/0000a_sneaky.sql",
                "content": 'ALTER TABLE "max_users" ADD COLUMN "sneaky" text;',
            },
        )
    )
    before = len(harness.exec_calls)

    result = await harness.handle.execute(Action("build", {}))

    assert result["ok"] is False
    assert len(harness.exec_calls) == before, "сборка не имела права уйти в ячейку"


async def test_contract_rejection_has_zero_source_ddl(
    monkeypatch: pytest.MonkeyPatch, db_session: AsyncSession, test_engine: AsyncEngine
) -> None:
    """Правка уже применённой миграции тоже останавливается до базы."""
    harness = await _executor(monkeypatch, db_session, test_engine)
    await harness.handle.execute(
        Action(
            "write_file",
            {
                "path": "drizzle/0000_max_core.sql",
                "content": _STARTER["drizzle/0000_max_core.sql"] + "\n-- изменено",
            },
        )
    )
    before = len(harness.exec_calls)

    result = await harness.handle.execute(Action("build", {}))

    assert result["ok"] is False
    assert len(harness.exec_calls) == before


async def test_the_refusal_names_the_offending_file_without_its_contents(
    monkeypatch: pytest.MonkeyPatch, db_session: AsyncSession, test_engine: AsyncEngine
) -> None:
    secret = 'CREATE TABLE "private_rows" ("pii" text); -- Иван Синтетический'
    harness = await _executor(monkeypatch, db_session, test_engine)
    await harness.handle.execute(
        Action("write_file", {"path": "drizzle/0000a_sneaky.sql", "content": secret})
    )

    result = await harness.handle.execute(Action("build", {}))

    detail = str(result.get("detail", ""))
    assert "0000a_sneaky.sql" in detail
    assert "Иван" not in detail and "private_rows" not in detail


async def test_an_appended_migration_still_builds(
    monkeypatch: pytest.MonkeyPatch, db_session: AsyncSession, test_engine: AsyncEngine
) -> None:
    """Ложных срабатываний быть не должно: обычная новая миграция проходит."""
    harness = await _executor(monkeypatch, db_session, test_engine)
    await harness.handle.execute(
        Action(
            "write_file",
            {
                "path": "drizzle/0002_orders.sql",
                "content": 'CREATE TABLE IF NOT EXISTS "orders" ("id" uuid PRIMARY KEY);',
            },
        )
    )
    before = len(harness.exec_calls)

    result = await harness.handle.execute(Action("build", {}))

    assert result["ok"] is True
    assert len(harness.exec_calls) > before, "исправная сборка обязана дойти до ячейки"


async def test_a_build_without_migration_changes_is_untouched(
    monkeypatch: pytest.MonkeyPatch, db_session: AsyncSession, test_engine: AsyncEngine
) -> None:
    harness = await _executor(monkeypatch, db_session, test_engine)
    await harness.handle.execute(
        Action("write_file", {"path": "src/app/page.tsx", "content": "export default () => null"})
    )
    before = len(harness.exec_calls)

    result = await harness.handle.execute(Action("build", {}))

    assert result["ok"] is True
    assert len(harness.exec_calls) > before
