"""Лабораторная проверка SQL-политик шаблона под ограниченной ролью.

Приложение пишет агент, и фильтр «только мои записи» в новом маршруте он может
забыть. Поэтому запрет живёт в PostgreSQL: политики уровня строк шаблона
(``templates/max-miniapp-nextjs/drizzle/0002_row_level_security.sql``).

Эта проверка НЕ доказывает изоляцию production. Docker/K8s public core пока
запускают миграции и сервер с одним DATABASE_URL пользователя postgres;
суперпользователь обходит даже FORCE. Отрицательный контроль ниже показывает
этот обход. Для production нужны проверка фактической роли и независимый A/B-probe.

В лабораторной конфигурации:
  * схема принадлежит роли NOSUPERUSER NOBYPASSRLS — поэтому политикам нужен
    FORCE: обычный владелец таблицы иначе обходит RLS;
  * миграции подаются тем же текстом, что подаёт ``scripts/apply-migrations.mjs``
    (без маркеров шагов и без квалификатора ``"public".``);
  * личность запроса приходит в ``app.max_user_id``, как её ставит ``withMaxUser``.

Тест требует одноразовую базу в ``TEMPLATE_RLS_TEST_DATABASE_URL`` и без неё
пропускается: своей базы он не создаёт и в чужие не пишет.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest

TEMPLATE = Path(__file__).resolve().parents[1] / "templates" / "max-miniapp-nextjs"
MIGRATIONS = TEMPLATE / "drizzle"

USER_A = "1000000000000001"
USER_B = "1000000000000002"

# Таблицы без владельца остаются открытыми намеренно: витрину заведения
# показывают всем, а ключи защиты от повторной обработки принадлежат не человеку.
TABLES_WITHOUT_OWNER = ("max_catalog_items", "max_webhook_events")


def _migration_sql(name: str) -> str:
    """Тот же текст, что получает база от накатчика миграций приложения."""
    raw = (MIGRATIONS / name).read_text(encoding="utf-8")
    return raw.replace("--> statement-breakpoint", "").replace('"public".', "")


class Probe:
    """psql одноразовой базы: администратор и ограниченная лабораторная роль."""

    def __init__(self, dsn: str, schema: str, role: str) -> None:
        self.dsn = dsn
        self.schema = schema
        self.role = role

    def _psql(self, script: str) -> subprocess.CompletedProcess[bytes]:
        return subprocess.run(
            ["psql", "-X", "-qAt", "-v", "ON_ERROR_STOP=1", self.dsn],
            input=script.encode(),
            capture_output=True,
            timeout=120,
            check=False,
        )

    def superuser(self, script: str) -> str:
        outcome = self._psql(f"SET search_path TO {self.schema};\n{script}")
        if outcome.returncode != 0:
            raise AssertionError(outcome.stderr.decode()[-800:])
        return outcome.stdout.decode().strip()

    def app(self, script: str, *, identity: str | None) -> str:
        """Запрос ограниченной лабораторной роли с заданной личностью."""
        prologue = f"SET ROLE {self.role};\nSET search_path TO {self.schema};\n"
        if identity is not None:
            # PERFORM внутри DO ничего не печатает: иначе значение личности
            # попадало бы в ответ psql и мешало сравнивать результат запроса.
            prologue += (
                "DO $ident$ BEGIN PERFORM set_config('app.max_user_id', "
                f"'{identity}', false); END $ident$;\n"
            )
        outcome = self._psql(prologue + script)
        if outcome.returncode != 0:
            raise AssertionError(outcome.stderr.decode()[-800:])
        return outcome.stdout.decode().strip()

    def app_expect_refusal(self, script: str, *, identity: str | None) -> str:
        prologue = f"SET ROLE {self.role};\nSET search_path TO {self.schema};\n"
        if identity is not None:
            # PERFORM внутри DO ничего не печатает: иначе значение личности
            # попадало бы в ответ psql и мешало сравнивать результат запроса.
            prologue += (
                "DO $ident$ BEGIN PERFORM set_config('app.max_user_id', "
                f"'{identity}', false); END $ident$;\n"
            )
        outcome = self._psql(prologue + script)
        if outcome.returncode == 0:
            raise AssertionError("база приняла запись от чужого имени — политика не сработала")
        return outcome.stderr.decode()


@pytest.fixture
def probe() -> Iterator[Probe]:
    dsn = os.environ.get("TEMPLATE_RLS_TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("requires a disposable TEMPLATE_RLS_TEST_DATABASE_URL")
    if shutil.which("psql") is None:
        pytest.skip("requires the psql client")
    suffix = uuid.uuid4().hex[:10]
    schema = f"rls_probe_{suffix}"
    role = f"rls_probe_app_{suffix}"
    probe = Probe(dsn, schema, role)
    # Модель ограниченного владельца; текущие dedicated runtime DB используют postgres.
    subprocess.run(
        ["psql", "-X", "-qAt", "-v", "ON_ERROR_STOP=1", dsn],
        input=(
            f"CREATE ROLE {role} NOSUPERUSER NOBYPASSRLS;\n"
            f"CREATE SCHEMA {schema} AUTHORIZATION {role};\n"
            f"GRANT USAGE, CREATE ON SCHEMA {schema} TO {role};\n"
        ).encode(),
        capture_output=True,
        timeout=60,
        check=True,
    )
    try:
        # Миграции накатывает сама роль приложения: она же станет владельцем таблиц.
        for name in sorted(p.name for p in MIGRATIONS.glob("*.sql")):
            probe.app(_migration_sql(name), identity=None)
        yield probe
    finally:
        subprocess.run(
            ["psql", "-X", "-qAt", "-v", "ON_ERROR_STOP=1", dsn],
            input=(
                f"DROP SCHEMA IF EXISTS {schema} CASCADE;\n"
                f"DROP ROLE IF EXISTS {role};\n"
            ).encode(),
            capture_output=True,
            timeout=60,
            check=False,
        )


def _seed_two_users(probe: Probe) -> None:
    for identity in (USER_A, USER_B):
        probe.app(
            f"INSERT INTO max_users (max_user_id, first_name) VALUES ('{identity}', '');",
            identity=identity,
        )
        probe.app(
            "INSERT INTO max_business_actions (max_user_id, action_type, payload) "
            f"VALUES ('{identity}', 'order', '{{\"note\":\"{identity}\"}}'::jsonb);",
            identity=identity,
        )


def test_every_owner_scoped_table_has_forced_row_security(probe: Probe) -> None:
    """Таблица с колонкой владельца без принудительной политики — это дыра."""
    rows = probe.superuser(
        "SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity, "
        "       (SELECT count(*) FROM pg_policy p WHERE p.polrelid = c.oid) "
        "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE n.nspname = current_schema() AND c.relkind = 'r' "
        "  AND EXISTS (SELECT 1 FROM information_schema.columns col "
        "              WHERE col.table_schema = n.nspname AND col.table_name = c.relname "
        "                AND col.column_name = 'max_user_id') "
        "ORDER BY c.relname;"
    )
    assert rows, "в схеме не нашлось ни одной таблицы с колонкой владельца"
    for line in rows.splitlines():
        name, enabled, forced, policies = line.split("|")
        assert enabled == "t", f"{name}: политики уровня строк не включены"
        assert forced == "t", (
            f"{name}: нет FORCE — владелец таблицы (роль приложения) обойдёт политику, "
            "и включение окажется тихой заглушкой"
        )
        assert int(policies) >= 1, f"{name}: RLS включён, но политик нет — таблица закрыта наглухо"


def test_tables_without_an_owner_stay_open_on_purpose(probe: Probe) -> None:
    for table in TABLES_WITHOUT_OWNER:
        forced = probe.superuser(
            "SELECT c.relrowsecurity FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
            f"WHERE n.nspname = current_schema() AND c.relname = '{table}';"
        )
        assert forced == "f", (
            f"{table} закрыли политикой — витрина заведения и ключи защиты от повторной "
            "обработки должны остаться общими, иначе приложение сломается"
        )


def test_one_user_never_sees_another_users_rows(probe: Probe) -> None:
    _seed_two_users(probe)
    mine = probe.app("SELECT count(*) FROM max_business_actions;", identity=USER_A)
    assert mine == "1", f"пользователь A видит {mine} строк вместо своей одной"
    owners = probe.app("SELECT DISTINCT max_user_id FROM max_business_actions;", identity=USER_A)
    assert owners == USER_A, f"в выборке A оказались чужие владельцы: {owners}"
    # Запрос БЕЗ фильтра по владельцу — именно то, что забудет новый маршрут.
    theirs = probe.app(
        f"SELECT count(*) FROM max_business_actions WHERE payload->>'note' = '{USER_B}';",
        identity=USER_A,
    )
    assert theirs == "0", "запрос без фильтра достал чужую строку — изоляции нет"


def test_one_user_cannot_change_or_delete_another_users_rows(probe: Probe) -> None:
    _seed_two_users(probe)
    updated = probe.app(
        "WITH changed AS (UPDATE max_business_actions SET status = 'hijacked' "
        f"WHERE max_user_id = '{USER_B}' RETURNING 1) SELECT count(*) FROM changed;",
        identity=USER_A,
    )
    assert updated == "0", "A изменил строку B"
    deleted = probe.app(
        "WITH removed AS (DELETE FROM max_business_actions "
        f"WHERE max_user_id = '{USER_B}' RETURNING 1) SELECT count(*) FROM removed;",
        identity=USER_A,
    )
    assert deleted == "0", "A удалил строку B"
    intact = probe.app("SELECT status FROM max_business_actions;", identity=USER_B)
    assert intact == "new", f"строка B пострадала: {intact}"


def test_a_row_cannot_be_written_under_another_users_name(probe: Probe) -> None:
    _seed_two_users(probe)
    error = probe.app_expect_refusal(
        "INSERT INTO max_business_actions (max_user_id, action_type) "
        f"VALUES ('{USER_B}', 'forged');",
        identity=USER_A,
    )
    assert "row-level security" in error.lower() or "политик" in error.lower(), error[-300:]


def test_without_an_identity_the_app_sees_nothing(probe: Probe) -> None:
    """Забыли обёртку — пусто. Отказ закрытый, а не «видно всё»."""
    _seed_two_users(probe)
    visible = probe.app("SELECT count(*) FROM max_business_actions;", identity=None)
    assert visible == "0", (
        f"без личности видно {visible} строк — это утечка: забытая обёртка должна давать "
        "пустой ответ, а не чужие данные"
    )


def test_the_platform_reader_still_sees_every_row(probe: Probe) -> None:
    """Снимки, резервные копии и сверка при откате читают под суперпользователем."""
    _seed_two_users(probe)
    total = probe.superuser("SET row_security = off; SELECT count(*) FROM max_business_actions;")
    assert total.splitlines()[-1] == "2", (
        f"платформенный читатель видит {total} вместо двух строк — политики задели снимки и "
        "сверку данных при откате"
    )


@pytest.mark.parametrize("identity", [None, USER_A])
def test_superuser_bypasses_forced_row_security_even_with_identity(
    probe: Probe, identity: str | None
) -> None:
    """FORCE и личность не защищают запросы с runtime-правами суперпользователя."""
    _seed_two_users(probe)
    assert probe.superuser(
        "SELECT rolsuper FROM pg_roles WHERE rolname = current_user;"
    ) == "t", "negative control requires a disposable superuser connection"
    identity_sql = ""
    if identity is not None:
        identity_sql = (
            "DO $ident$ BEGIN PERFORM set_config('app.max_user_id', "
            f"'{identity}', false); END $ident$;\n"
        )
    visible = probe.superuser(
        "SET row_security = on;\n"
        + identity_sql
        + "SELECT DISTINCT max_user_id FROM max_business_actions ORDER BY max_user_id;"
    )
    assert visible.splitlines() == [USER_A, USER_B], (
        "negative control must expose both owners under superuser privileges; "
        "passing restricted-role tests alone cannot prove runtime isolation"
    )


def test_an_empty_identity_matches_nothing(probe: Probe) -> None:
    """Пустая строка — это значение, и оно не должно совпадать с «не задано».

    Замечание пришло со стороны откатов при сверке схемы: политика без nullif
    пустила бы пользователя с пустым max_user_id к строкам без выставленной личности.
    """
    probe.superuser(
        "SET row_security = off;\n"
        "INSERT INTO max_users (max_user_id, first_name) VALUES ('', '');\n"
        "INSERT INTO max_business_actions (max_user_id, action_type) VALUES ('', 'ghost');"
    )
    _seed_two_users(probe)
    visible = probe.app("SELECT count(*) FROM max_business_actions;", identity="")
    assert visible == "0", (
        f"с пустой личностью видно {visible} строк — пустая строка совпала с «не задано»"
    )

