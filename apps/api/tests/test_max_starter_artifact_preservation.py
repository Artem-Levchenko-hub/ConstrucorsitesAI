"""Приложение не имеет права объявить таблицы и потерять миграции, которые их создают.

LIVE-04: сборка зелёная, типы проверены, страница отдаётся — а подписанный запрос
к маршруту данных падает с «relation max_users does not exist». Причина была не в
коде маршрута: из набора файлов пропали стартовые миграции, и в свежей базе просто
нечему было отвечать.

Тихой эта потеря получается из-за того, как миграции применяются. Скрипт берёт все
`*.sql` из `drizzle/`, сортирует по имени и запоминает применённые ПО ИМЕНИ. Значит:
файла нет — таблицы не будет и никто не пожалуется; файл переименован — он
применится второй раз; новый файл с именем раньше применённого встанет в середину
уже пройденного порядка.

Поэтому проверка идёт ДО исполнения и сравнивает набор с эталоном, а не ищет
упоминание миграций в тексте запроса к модели.
"""

from __future__ import annotations

import pytest

from yleum_api.services.max_starter_artifacts import (
    STARTER_APPLY_SCRIPT,
    STARTER_DRIZZLE_CONFIG,
    STARTER_MIGRATIONS,
    starter_baseline,
    validate_starter_artifacts,
)


def _bundle(**overrides: str | None) -> dict[str, str]:
    """Набор файлов, совпадающий с эталоном, плюс точечные отличия."""
    files = dict(starter_baseline())
    files["src/app/page.tsx"] = "export default function Page() { return null }"
    # Объявление схемы — признак полного набора; без него судить не о чем.
    files["src/lib/db/schema.ts"] = 'export const maxUsers = pgTable("max_users", {});'
    for path, content in overrides.items():
        key = path.replace("__", "/")
        if content is None:
            files.pop(key, None)
        else:
            files[key] = content
    return files


def test_a_bundle_that_kept_everything_has_nothing_to_report() -> None:
    assert validate_starter_artifacts(_bundle(), starter_baseline()) == []


def test_declared_max_tables_require_preserved_starter_migrations() -> None:
    """Ровно LIVE-04: объявлены технические таблицы, а создающих их миграций нет."""
    files = _bundle()
    for name in STARTER_MIGRATIONS:
        files.pop(name, None)
    # Модель оставила собственную миграцию своей бизнес-таблицы — и только её.
    files["drizzle/0001_qa_clients.sql"] = 'CREATE TABLE "qa_clients" ("id" uuid PRIMARY KEY);'

    errors = validate_starter_artifacts(files, starter_baseline())

    assert "starter_migration_missing" in errors
    # Отказ обязан случиться до исполнения, а не после падения маршрута в бою.
    assert errors, "потерянная стартовая миграция не может пройти молча"


def test_each_missing_starter_migration_is_reported_on_its_own() -> None:
    for name in STARTER_MIGRATIONS:
        files = _bundle()
        files.pop(name)

        errors = validate_starter_artifacts(files, starter_baseline())

        assert "starter_migration_missing" in errors, name


def test_current_starter_keeps_apply_script_and_drizzle_config() -> None:
    """Проверяются сами файлы репозитория, а не строчка в промпте."""
    baseline = starter_baseline()

    assert STARTER_APPLY_SCRIPT in baseline
    assert STARTER_DRIZZLE_CONFIG in baseline
    assert set(STARTER_MIGRATIONS) <= set(baseline)
    # Эталон берётся с диска: если файл из шаблона исчезнет, тест упадёт здесь.
    assert baseline[STARTER_APPLY_SCRIPT].strip(), "скрипт применения миграций пуст"
    assert "drizzle-kit" in baseline[STARTER_DRIZZLE_CONFIG]


def test_a_missing_apply_script_is_named_not_guessed() -> None:
    files = _bundle()
    files.pop(STARTER_APPLY_SCRIPT)

    assert "materializer_script_missing" in validate_starter_artifacts(files, starter_baseline())


def test_a_missing_drizzle_config_is_named_not_guessed() -> None:
    files = _bundle()
    files.pop(STARTER_DRIZZLE_CONFIG)

    assert "drizzle_config_missing" in validate_starter_artifacts(files, starter_baseline())


def test_a_renamed_starter_migration_is_not_an_acceptable_substitute() -> None:
    """Применённое запоминается по имени: переименование — это повторный прогон."""
    files = _bundle()
    original = files.pop(STARTER_MIGRATIONS[0])
    files["drizzle/0000_core.sql"] = original

    errors = validate_starter_artifacts(files, starter_baseline())

    assert "starter_migration_missing" in errors


def test_changed_content_of_an_applied_migration_is_refused() -> None:
    """Уже применённый файл второй раз не запускается — правка в нём не исполнится."""
    files = _bundle()
    files[STARTER_MIGRATIONS[0]] += '\nALTER TABLE "max_users" ADD COLUMN "extra" text;'

    errors = validate_starter_artifacts(files, starter_baseline())

    assert "migration_order_changed" in errors


def test_a_new_migration_must_sort_after_the_starter_ones() -> None:
    """Новый файл раньше применённого встаёт в середину уже пройденного порядка."""
    files = _bundle()
    files["drizzle/0000a_sneaky.sql"] = 'CREATE TABLE "sneaky" ("id" uuid PRIMARY KEY);'

    errors = validate_starter_artifacts(files, starter_baseline())

    assert "migration_order_changed" in errors


def test_an_appended_migration_is_allowed() -> None:
    files = _bundle()
    files["drizzle/0002_qa_clients.sql"] = 'CREATE TABLE "qa_clients" ("id" uuid PRIMARY KEY);'

    assert validate_starter_artifacts(files, starter_baseline()) == []


def test_errors_are_named_codes_without_file_contents() -> None:
    files = _bundle()
    files.pop(STARTER_MIGRATIONS[0])
    files.pop(STARTER_APPLY_SCRIPT)
    secret = 'CREATE TABLE "private_customer_rows" ("pii" text);'
    files["drizzle/0002_private.sql"] = secret

    errors = validate_starter_artifacts(files, starter_baseline())

    assert set(errors) <= {
        "starter_migration_missing",
        "migration_order_changed",
        "materializer_script_missing",
        "drizzle_config_missing",
    }
    assert secret not in " ".join(errors)


def test_the_same_error_is_never_reported_twice() -> None:
    files = _bundle()
    for name in STARTER_MIGRATIONS:
        files.pop(name)

    errors = validate_starter_artifacts(files, starter_baseline())

    assert len(errors) == len(set(errors))


def test_the_first_build_seed_carries_the_starter_migrations() -> None:
    """Проверять можно только то, что видно.

    До этой правки миграции жили лишь в рабочей области, собранной из шаблона, а
    в наборе файлов, с которым работает платформа, их не было вовсе — поэтому их
    пропажу было некому заметить. Теперь они засеваются как обычные файлы проекта.
    """
    from yleum_api.schemas.max_studio import MaxProjectConfigPayload
    from yleum_api.services.max_project_kit import render_max_starter_files

    files = render_max_starter_files(
        MaxProjectConfigPayload(app_name="Проверка", app_type="custom", summary="тест"),
        "6cd1e70b-55b8-4025-b703-63f6d51745a8",
    )

    for name in STARTER_MIGRATIONS:
        assert name in files, name
        assert files[name] == starter_baseline()[name]


def test_a_bundle_that_dropped_a_starter_migration_is_not_complete() -> None:
    """Потеря должна остановить работу агента, а не всплыть 500-й в бою."""
    from yleum_api.services.max_generation_contract import max_source_completion_gap

    files = _bundle()
    files.pop(STARTER_MIGRATIONS[0])

    gap = max_source_completion_gap("сделай приложение", files)

    assert gap is not None
    assert STARTER_MIGRATIONS[0] in gap
    # Указание должно говорить, что делать, а не только что сломано.
    assert "restore" in gap.lower()


def test_an_intact_bundle_is_never_blocked_by_the_starter_check() -> None:
    from yleum_api.services.max_starter_artifacts import starter_artifact_gap

    assert starter_artifact_gap(_bundle()) is None


def test_a_fragment_without_a_declared_schema_is_not_judged() -> None:
    """Частичный набор — не потерянные миграции, а просто не весь проект.

    Договор завершённости зовут и на маленьких наборах (отдельная проверка,
    точечная правка). Требовать от них стартовые миграции значит ругаться не на
    того и блокировать работу, которая ничего не теряла.
    """
    from yleum_api.services.max_starter_artifacts import starter_artifact_gap

    fragment = {"src/app/page.tsx": "export default function Page() { return null }"}

    assert starter_artifact_gap(fragment) is None


def test_the_check_ignores_files_the_platform_never_carries() -> None:
    """Скрипт и конфиг защищены от модели и живут вне набора файлов платформы.

    Пока они проверялись здесь, полностью исправная сборка объявлялась незавершённой.
    """
    from yleum_api.services.max_starter_artifacts import starter_artifact_gap

    files = _bundle()
    files.pop(STARTER_APPLY_SCRIPT)
    files.pop(STARTER_DRIZZLE_CONFIG)

    assert starter_artifact_gap(files) is None


def test_the_starter_gap_names_every_lost_file_at_once() -> None:
    from yleum_api.services.max_starter_artifacts import starter_artifact_gap

    files = _bundle()
    for name in STARTER_MIGRATIONS:
        files.pop(name)

    gap = starter_artifact_gap(files)

    assert gap is not None
    for name in STARTER_MIGRATIONS:
        assert name in gap


@pytest.mark.parametrize("empty", ["", "   \n"])
def test_an_empty_starter_file_counts_as_missing_not_present(empty: str) -> None:
    # Присутствие имени ничего не доказывает: пустой файл не создаст таблиц.
    files = _bundle()
    files[STARTER_MIGRATIONS[0]] = empty

    errors = validate_starter_artifacts(files, starter_baseline())

    assert "starter_migration_missing" in errors
