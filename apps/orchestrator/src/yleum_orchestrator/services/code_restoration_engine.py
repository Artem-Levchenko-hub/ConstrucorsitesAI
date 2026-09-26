"""Isolated code preparation and code-volume activation. No live data import exists here."""

from __future__ import annotations

import fnmatch
import hashlib
import io
import json
import logging
import os
import re
import shutil
import socket
import tarfile
import traceback
from collections.abc import Callable
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any, Literal, cast
from uuid import UUID, uuid5

from yleum_orchestrator.core.cell_resources import (
    CellIdentityConflict,
    CellResourceError,
    LifecycleMutation,
)
from yleum_orchestrator.core.project_machine import MachineManifest
from yleum_orchestrator.core.workspace_provider import WorkspaceSpec
from yleum_orchestrator.schemas.code_restoration import (
    CodeRestorationApply,
    CodeRestorationCancel,
    CodeRestorationPrepare,
    RestorationDatabaseState,
    RestorationSourceBindingV2,
    RestorationSourceBindingV3,
)
from yleum_orchestrator.services.cell_admission import CellAdmissionGate
from yleum_orchestrator.services.cell_state import legacy_release_serving_epoch
from yleum_orchestrator.services.machine_environment import MachineEnvironmentRef
from yleum_orchestrator.services.project_machine import (
    machine_budget,
    machine_effect,
    write_controller_json,
)
from yleum_orchestrator.services.restoration_adaptation_probe import initial_witness_hints
from yleum_orchestrator.services.restoration_binding import (
    canonical_digest,
    exact_inventory_partition_digests,
    observe_live_source,
    serving_fencing_epoch,
)
from yleum_orchestrator.services.restoration_catalog import (
    candidate_contract,
    catalog_contract,
    describe_live_catalog,
)
from yleum_orchestrator.services.restoration_data_contract import (
    ContractAssessment,
    DataContract,
    assess_contract,
    normalize_check,
)
from yleum_orchestrator.services.restoration_database import (
    admin_args,
    admin_sql,
    close_controller_socket,
    read_controller_output,
)
from yleum_orchestrator.services.restoration_empty import (
    EmptyDatabaseWitness,
    observe_empty_database,
    same_empty_source,
)
from yleum_orchestrator.services.restoration_execution import (
    RestorationExecutionCancelled,
    RestorationExecutionJournal,
    dedicated_docker_api_factory,
)
from yleum_orchestrator.services.versioning.compatibility import (
    capability_diff,
    checks_from_diagnostics,
    checks_from_unsupported,
    delete_warnings,
    describe_capability,
)
from yleum_orchestrator.services.versioning.contracts import (
    CapabilityDiff,
    CompatibilityCheck,
    InventoryReport,
)
from yleum_orchestrator.services.versioning.inventory import observe_inventory


class PreparationNeedsChanges(ValueError):
    """The historical version cannot be prepared as is.

    ``details`` (optional) is the machine-readable outcome of the stage that
    refused — stage name, argv, exit code, timeout, the tail of its output. It
    goes into the operation's attempt journal so an operator no longer has to
    find ``restoration-check.log`` on the host to learn WHY (24.09.2026: the
    platform knew the reason, the journal and the API did not).
    """

    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.details = dict(details) if details else None


_OUTPUT_TAIL_CHARS = 1200
_OWNER_TAIL_LINES = 3
_OWNER_TAIL_CHARS = 300


def stage_output_tail(output: bytes, limit: int = _OUTPUT_TAIL_CHARS) -> str:
    """The last ``limit`` characters of a stage's output, printable text only."""
    text = output[-(limit * 4) :].decode("utf-8", errors="replace")
    cleaned = "".join(ch if ch == "\n" or ch >= " " else " " for ch in text)
    return cleaned[-limit:].strip()


def stage_failure_details(
    *,
    stage: str,
    argv: list[str],
    cwd: str,
    exit_code: int,
    timeout_seconds: int,
    output: bytes,
) -> dict[str, Any]:
    return {
        "result": "failed",
        "stage": stage,
        "argv": list(argv),
        "cwd": cwd,
        "exit_code": exit_code,
        "timeout_seconds": timeout_seconds,
        "timed_out": exit_code == _TIMEOUT_EXIT_CODE,
        "output_tail": stage_output_tail(output),
    }


def owner_visible_tail(output_tail: str) -> str:
    """The last few non-empty lines, short enough for a blocker line in the UI."""
    lines = [line.strip() for line in output_tail.splitlines() if line.strip()]
    joined = " | ".join(lines[-_OWNER_TAIL_LINES:])
    if len(joined) > _OWNER_TAIL_CHARS:
        joined = "…" + joined[-_OWNER_TAIL_CHARS:]
    return joined


def _write_activation_journal(path: Path, payload: dict[str, Any]) -> None:
    """Persist a phase before its external effect, including the rename."""

    write_controller_json(path, payload)
    if os.name == "nt":
        return
    flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    fd = os.open(path.parent, flags)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


_MIGRATION_RUNNER_PATH = "scripts/apply-migrations.mjs"
# Нормализованные отпечатки ВСЕХ версий исполнителя миграций, когда-либо
# существовавших в шаблоне (три: 712df4e8, 7c925f89, 5f694772). Файлы приходят
# из исторического снимка проекта, а снимок может быть старше запрета на правку
# служебных файлов — значит, доверять одному лишь имени нельзя: это исполнение
# произвольного кода из снимка, а не восстановление. Список закрыт содержимым,
# а не именем, и покрывает все законные старые версии, чтобы привязка не сломала
# восстановление июльских проектов.
_MIGRATION_RUNNERS = frozenset(
    {
        "f8fa7703fd732f9d7e341c3f2262ba03e90a7d3ed828cf9a611ca64be1ad42c8",
        "1c22beb5d3dceda8fc106f1dc6e73dcd4cfd3a82b660aa018845629d6dfcc192",
        "be180e28bd6904102a6f6cd533750fa193d9e00d6af68206ae6392b04c7678cd",
    }
)


def _normalized_digest(text: str) -> str:
    return hashlib.sha256(text.replace("\r\n", "\n").strip().encode()).hexdigest()


_DRIZZLE_CONFIGS = {
    '''import type { Config } from "drizzle-kit";

export default {
  schema: "./src/lib/db/schema.ts",
  out: "./drizzle",
  dialect: "postgresql",
  dbCredentials: {
    url: process.env.DATABASE_URL as string,
  },
} satisfies Config;''',
    '''import { defineConfig } from "drizzle-kit";

export default defineConfig({
  schema: "./src/lib/db/schema.ts",
  out: "./drizzle",
  dialect: "postgresql",
  dbCredentials: { url: process.env.DATABASE_URL! },
  verbose: true,
  strict: true,
});''',
    '''import { defineConfig } from "drizzle-kit";

export default defineConfig({
  schema: "./src/lib/db/schema.ts",
  out: "./drizzle",
  dialect: "postgresql",
  dbCredentials: {
    url: process.env.DATABASE_URL!,
  },
  verbose: true,
  strict: true,
});''',
}

_MANAGED_MAX_TABLES = frozenset(
    {
        "max_users",
        "max_webhook_events",
        "max_catalog_items",
        "max_business_actions",
        "max_consents",
        "max_analytics_events",
        "max_bot_outbox",
        "max_audit_log",
    }
)


def _project_data_contract(contract: DataContract) -> DataContract:
    """Exclude only the controller-owned MAX core from a project schema proof."""
    return DataContract(
        version=1,
        tables=[table for table in contract.tables if table.name not in _MANAGED_MAX_TABLES],
    )


def _mapping_body(value: str, *, header: str, indent: int) -> str | None:
    prefix = " " * indent
    matches = list(re.finditer(rf"(?m)^{re.escape(prefix + header)}:\r?$", value))
    if len(matches) != 1:
        return None
    start = matches[0].end()
    sibling = re.search(rf"(?m)^{re.escape(prefix)}\S.*:\r?$", value[start:])
    return value[start : start + sibling.start()] if sibling else value[start:]


def _root_lock_importer(lockfile: str) -> str | None:
    if re.fullmatch(r"(?s).*^lockfileVersion: '9\.0'\r?\n.*", lockfile) is None:
        return None
    importers = _mapping_body(lockfile, header="importers", indent=0)
    if importers is None:
        return None
    return _mapping_body(importers, header=".", indent=2)


def _root_lock_version(
    importer: str,
    dependency_group: str,
    package: str,
    specifier: str,
    version: str,
) -> bool:
    dependencies = _mapping_body(importer, header=dependency_group, indent=4)
    if dependencies is None:
        return False
    matches = re.findall(
        rf"(?m)^      {re.escape(package)}:\r?\n"
        rf"        specifier: {re.escape(specifier)}\r?\n"
        rf"        version: ({version})(?:\([^\r\n]*\))?\r?$",
        dependencies,
    )
    return len(matches) == 1


def _contract_checks(table: Any) -> tuple[str, ...]:
    return tuple(
        sorted(
            [normalize_check(item) for item in table.checks]
            + [normalize_check(item.definition) for item in table.check_constraints]
        )
    )


def _declaration_only_enum_hint(column_type: str) -> bool:
    return (
        re.fullmatch(
            r"(?:text|character varying(?:\([0-9]+\))?|character(?:\([0-9]+\))?)(?:\[\])?",
            column_type,
        )
        is not None
    )


def structural_materialization_matches(
    expected: DataContract, actual: DataContract
) -> bool:
    """Prove physical schema creation without applying data-compatibility semantics.

    Historical extraction does not measure ownership/read-only/JSON-key metadata,
    defaults, or identity declarations. A known historical default/identity is still
    required exactly; an absent declaration stays unmeasured rather than "absent".
    """
    expected_tables = {table.name: table for table in expected.tables}
    actual_tables = {table.name: table for table in actual.tables}
    if expected_tables.keys() != actual_tables.keys():
        return False
    for name, table in expected_tables.items():
        observed = actual_tables[name]
        if (
            table.primary_key != observed.primary_key
            or sorted(map(tuple, table.unique_keys))
            != sorted(map(tuple, observed.unique_keys))
            or sorted(
                (
                    item.column,
                    item.table,
                    item.target,
                    item.on_delete,
                    item.on_update,
                )
                for item in table.foreign_keys
            )
            != sorted(
                (
                    item.column,
                    item.table,
                    item.target,
                    item.on_delete,
                    item.on_update,
                )
                for item in observed.foreign_keys
            )
            or _contract_checks(table) != _contract_checks(observed)
        ):
            return False
        declared_columns = {column.name: column for column in table.columns}
        materialized_columns = {column.name: column for column in observed.columns}
        if declared_columns.keys() != materialized_columns.keys():
            return False
        for column_name, declared in declared_columns.items():
            materialized = materialized_columns[column_name]
            if (
                declared.type != materialized.type
                or declared.nullable != materialized.nullable
                or (
                    declared.values != materialized.values
                    and not (
                        materialized.values is None
                        and _declaration_only_enum_hint(declared.type)
                    )
                )
                or (
                    declared.default is not None
                    and declared.default != materialized.default
                )
                or (
                    declared.identity is not None
                    and declared.identity != materialized.identity
                )
            ):
                return False
    return True


def empty_database_materializer(files: dict[str, str]) -> list[str] | None:
    """Select a bounded historical-schema recipe for a new isolated empty DB."""
    if _MIGRATION_RUNNER_PATH in files:
        # Привязка по содержимому, а не по имени: подменённый исполнитель не
        # исполняется и не уступает место другому рецепту — отказ здесь и сейчас.
        if _normalized_digest(files[_MIGRATION_RUNNER_PATH]) not in _MIGRATION_RUNNERS:
            return None
        return ["node", _MIGRATION_RUNNER_PATH]
    required = {
        "package.json",
        "pnpm-lock.yaml",
        "drizzle.config.ts",
        "src/lib/db/schema.ts",
    }
    if not required.issubset(files) or not files["src/lib/db/schema.ts"].strip():
        return None
    if files["drizzle.config.ts"].replace("\r\n", "\n").strip() not in _DRIZZLE_CONFIGS:
        return None
    try:
        package = json.loads(files["package.json"])
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(package, dict) or package.get("packageManager") != "pnpm@9.15.0":
        return None
    dependencies = package.get("dependencies")
    development = package.get("devDependencies")
    if (
        not isinstance(dependencies, dict)
        or not isinstance(development, dict)
        or dependencies.get("drizzle-orm") != "^0.36.0"
        or development.get("drizzle-kit") != "^0.28.0"
    ):
        return None
    importer = _root_lock_importer(files["pnpm-lock.yaml"])
    if importer is None or not _root_lock_version(
        importer,
        "dependencies",
        "drizzle-orm",
        "^0.36.0",
        r"0\.36\.[0-9]+",
    ):
        return None
    if not _root_lock_version(
        importer,
        "devDependencies",
        "drizzle-kit",
        "^0.28.0",
        r"0\.28\.[0-9]+",
    ):
        return None
    return [
        "pnpm",
        "exec",
        "drizzle-kit",
        "push",
        "--config=drizzle.config.ts",
        "--force",
    ]


_MIGRATION_DIR_PREFIX = "drizzle/"


def historical_sql_migrations(files: dict[str, str]) -> list[tuple[str, str]] | None:
    """Исторические миграции как есть — самый безопасный рецепт из трёх.

    Два прежних рецепта требуют от приложения либо служебный скрипт применения
    миграций, либо `drizzle.config.ts`. Современный шаблон MAX не содержит ни
    того, ни другого: миграции применяет доверенный контейнер из своего образа,
    а в рабочей области владельца лежат только сами файлы `drizzle/*.sql`.
    Из-за этого 24.09 откат исправного приложения с пустой базой отказывал с
    формулировкой «не хватает файлов: настройка базы» — при том, что описание
    схемы было на месте, просто в другом виде.

    Этот рецепт ничего не исполняет: контроллер сам применяет SQL по порядку
    имён — ровно так, как их применяет штатный исполнитель. Чужой код при этом
    не запускается вовсе, то есть риск здесь строго меньше, чем у обоих
    прежних путей.
    """
    migrations = sorted(
        (path, files[path])
        for path in files
        if path.startswith(_MIGRATION_DIR_PREFIX) and path.endswith(".sql")
    )
    if not migrations:
        return None
    # Пустой файл миграции не создаёт объектов и означает потерю, а не «нечего
    # делать»: такую версию восстанавливать нельзя.
    if any(not sql.strip() for _, sql in migrations):
        return None
    if not files.get("src/lib/db/schema.ts", "").strip():
        return None
    return migrations


def empty_materializer_blocker(files: dict[str, str]) -> str | None:
    """Назвать владельцу, ЧЕГО не хватает для восстановления исторической схемы.

    Одна общая фраза «нет поддерживаемого описания схемы» не даёт действовать:
    по ней не отличить изменённый служебный файл от пропавшей настройки и от
    неподдерживаемых версий библиотек, а это три разных разговора с поддержкой.
    Содержимое файлов проекта в текст не попадает — только названия и причина.
    """
    if empty_database_materializer(files) is not None:
        return None
    if historical_sql_migrations(files) is not None:
        return None
    if _MIGRATION_RUNNER_PATH in files:
        return (
            "Служебный файл применения миграций в выбранной версии отличается от "
            "известных — восстановление по нему не выполняется."
        )
    missing = [
        name
        for name, human in (
            ("drizzle.config.ts", "настройка"),
            ("src/lib/db/schema.ts", "описание схемы"),
            ("package.json", "описание пакета"),
            ("pnpm-lock.yaml", "список зависимостей"),
        )
        if name not in files or not files[name].strip()
    ]
    if missing:
        human = {
            "drizzle.config.ts": "настройка базы (drizzle.config.ts)",
            "src/lib/db/schema.ts": "описание схемы (src/lib/db/schema.ts)",
            "package.json": "описание пакета (package.json)",
            "pnpm-lock.yaml": "список зависимостей (pnpm-lock.yaml)",
        }
        listed = ", ".join(human[name] for name in missing)
        return f"В выбранной версии не хватает файлов: {listed}."
    if files["drizzle.config.ts"].replace("\r\n", "\n").strip() not in _DRIZZLE_CONFIGS:
        return (
            "Настройка базы (drizzle.config.ts) в выбранной версии не совпадает ни с "
            "одной известной, поэтому схему нельзя восстановить без изменений."
        )
    return (
        "В выбранной версии версии библиотек работы с базой не поддерживаются "
        "восстановлением без изменений."
    )


def verify_source_inventory(actual: dict[str, bytes], expected: list[dict[str, Any]]) -> None:
    """Unknown persistent files must be classified before replacing a code volume."""
    from yleum_orchestrator.services.docker_py_cell_backend import (
        _WORKSPACE_SOURCE_ARCHIVE_EXCLUDES,
    )

    def included(path: str) -> bool:
        return Path(path).name != "next-env.d.ts" and not any(
            fnmatch.fnmatchcase("./" + path, pattern)
            for pattern in _WORKSPACE_SOURCE_ARCHIVE_EXCLUDES
        )

    observed = {
        path: hashlib.sha256(data).hexdigest() for path, data in actual.items() if included(path)
    }
    wanted = {item["path"]: item["sha256"] for item in expected if included(item["path"])}
    if not wanted or wanted != observed:
        raise PreparationNeedsChanges(
            "Текущие файлы отличаются от сохранённой версии. Сохраните правки; "
            "пользовательские файлы перенесите в отдельное постоянное хранилище."
        )


def source_text(request: CodeRestorationPrepare) -> dict[str, str]:
    result = {}
    for item in request.files:
        try:
            result[item.path] = item.decoded().decode("utf-8")
        except UnicodeDecodeError:
            continue
    return result


def source_archive(request: CodeRestorationPrepare) -> bytes:
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w") as archive:
        for item in request.files:
            data = item.decoded()
            info = tarfile.TarInfo(item.path)
            info.size, info.mode = len(data), int(item.mode, 8) & 0o777
            archive.addfile(info, io.BytesIO(data))
    return output.getvalue()


def validate_supported_runtime(files: dict[str, str]) -> MachineManifest:
    manifest = MachineManifest.from_files(files)
    if manifest is None:
        raise PreparationNeedsChanges(
            "Для этой версии нужно подготовить описание запуска приложения."
        )
    package = json.loads(files.get("package.json", "{}"))
    if "next" not in package.get("dependencies", {}):
        raise PreparationNeedsChanges(
            "Нужно адаптировать запуск этого приложения к проверяемой среде."
        )
    if "pnpm-lock.yaml" not in files:
        raise PreparationNeedsChanges("Не сохранён файл точных зависимостей pnpm-lock.yaml.")
    if len(manifest.services) != 1 or manifest.data_stores:
        raise PreparationNeedsChanges(
            "Нужна отдельная проверка фоновых служб и файловых хранилищ этой версии."
        )
    service = manifest.services[0]
    scripts = package.get("scripts", {})
    start = scripts.get("start", "")
    if (
        service.argv not in (["pnpm", "start"], ["pnpm", "run", "start"])
        or service.cwd != "."
        or service.mounts
        or not re.fullmatch(
            r"next start(?:\s+(?:-[Hp]|--hostname|--port)\s+[A-Za-z0-9.:_-]+)*", start
        )
    ):
        raise PreparationNeedsChanges(
            "Команду запуска нужно отделить от миграций и фоновых действий."
        )
    if not any(task.role in {"full_build", "build"} for task in manifest.tasks):
        raise PreparationNeedsChanges("Не найдена проверяемая команда сборки выбранной версии.")
    return manifest


def observe_database(
    backend: Any, *, observed_on: Literal["source", "candidate_copy"]
) -> InventoryReport:
    """Read-only row inventory of one project database (source or isolated copy)."""
    return observe_inventory(
        lambda sql: admin_sql(backend, sql, max_bytes=256 * 1024), observed_on=observed_on
    )


def _inventory_lines(inventory: InventoryReport | None) -> list[str]:
    if inventory is None:
        return []
    counted = [
        f"{item.object.removeprefix('public.')} — "
        + (f"{item.row_count} " if item.count_kind == "exact" else f"более {item.row_count - 1} ")
        + "записей"
        for item in inventory.objects
        if item.classification == "business" and item.row_count is not None
    ]
    return ["Данные в базе: " + "; ".join(counted) + "."] if counted else []


def preparation_report(
    *,
    blockers: list[str] | None = None,
    retained: list[str] | None = None,
    cascading_deletes: list[str] | None = None,
    observed_database_state: RestorationDatabaseState = "unknown",
    inventory: InventoryReport | None = None,
    checks: list[CompatibilityCheck] | None = None,
    capabilities: CapabilityDiff | None = None,
    observed_contract: DataContract | None = None,
) -> dict[str, Any]:
    blocked = blockers or []
    if inventory is not None:
        # The independent inventory is the source of truth for presence; an
        # analysis failure elsewhere never turns observed rows into "unknown".
        observed_database_state = inventory.presence
    lost = [describe_capability(item) for item in capabilities.lost] if capabilities else []
    report: dict[str, Any] = {
        "revision": 1,
        "mode": "adapted" if blocked else "exact",
        "database_state": observed_database_state,
        "changes": [
            "Код выбранной версии собирается с её сохранёнными зависимостями.",
            "Приложение работает с текущей базой проекта без переноса старых данных.",
            *([] if blocked else ["Исторический код подготовлен без запуска AI-агента."]),
        ],
        "retained_data": [
            "Текущая база и действующая публикация не заменяются.",
            *_inventory_lines(inventory),
            *["Поле сохраняется в базе: " + name for name in retained or []],
        ],
        "unavailable_features": [
            "В выбранной версии нет функции " + item + "; её данные остаются в базе."
            for item in lost
        ],
        "warnings": [
            *[
                "Удаление в старой версии может затронуть новые связанные данные: " + name
                for name in cascading_deletes or []
            ],
            "Сборка подготовлена заново; это не побайтовое восстановление исторического окружения.",
            "Платежи, сообщения и уже выполненные действия не отменяются.",
            "Состояние БД проверено на копии при подготовке; новые записи не отменяются."
            if observed_database_state != "unknown"
            else "Наличие записей в БД не подтверждено; пустая база не предполагается.",
        ],
        "blockers": blocked,
        "next_actions": ["Подготовьте совместимую правку выбранной версии и повторите проверку."]
        if blocked
        else [],
    }
    resolutions = list(
        dict.fromkeys(
            check.resolution
            for check in checks or []
            if check.severity == "blocking" and check.resolution
        )
    )
    if blocked and resolutions:
        report["next_actions"] = [*resolutions, *report["next_actions"]]
    if inventory is not None and inventory.schema_analysis == "partial":
        report["warnings"].append(
            "Структура базы изучена частично: отдельные объекты требуют проверки (см. причины)."
        )
    if inventory is not None or checks is not None or capabilities is not None:
        report["format"] = 2
        report["inventory"] = inventory.model_dump(mode="json") if inventory else None
        # Every conflict/warning is kept; passed checks are capped to stay inside
        # the API's bounded report.
        hints = []
        if (
            observed_contract is not None
            and inventory is not None
            and inventory.presence == "present"
            and inventory.coverage == "complete"
            and inventory.schema_analysis == "complete"
            and inventory.observed_on == "source"
        ):
            hints = [
                CompatibilityCheck(
                    code="activation_probe_witness_hint",
                    status="not_applicable",
                    severity="info",
                    operation="activation_probe",
                    object=hint.split(" ")[3],
                    evidence="observed_catalog",
                    explanation=(
                        "Schema identifiers only; recheck the current contract before proof."
                    ),
                    resolution=hint,
                )
                for hint in initial_witness_hints(observed_contract)
            ]
        kept = [check for check in checks or [] if check.severity != "info"][:800]
        kept += hints
        kept += [check for check in checks or [] if check.severity == "info"][: 100 - len(hints)]
        report["checks"] = [check.model_dump(mode="json") for check in kept]
        report["capabilities"] = capabilities.model_dump(mode="json") if capabilities else None
    return report


def contract_matches(live: DataContract, prepared: dict[str, Any]) -> bool:
    """Compare with a prepared contract; one recorded before format 2 carried CHECKs
    as plain ``checks`` and no default/identity, so project the live one likewise."""
    current = live.model_dump(mode="json")
    if all("check_constraints" in table for table in prepared.get("tables", [])):
        return bool(current == prepared)
    for table in current["tables"]:
        table["checks"] = [check["definition"] for check in table.pop("check_constraints")]
        for column in table["columns"]:
            column.pop("default", None)
            column.pop("identity", None)
    return bool(current == prepared)


def blocking_explanations(checks: list[CompatibilityCheck]) -> list[str]:
    return [check.explanation for check in checks if check.severity == "blocking"]


def verify_post_dump_catalog(
    before: DataContract,
    before_blockers: list[str],
    before_unsupported: list[dict[str, Any]],
    after: DataContract,
    after_blockers: list[str],
    after_unsupported: list[dict[str, Any]],
) -> None:
    if (
        after != before
        or after_blockers != before_blockers
        or after_unsupported != before_unsupported
    ):
        raise CellIdentityConflict("restoration database schema changed during export")


# `timeout` сообщает об убийстве по времени именно этим кодом.
_TIMEOUT_EXIT_CODE = 124
# Абсолютный предохранитель: кандидат не должен занимать проверочный слот
# бесконечно. Выше собственных лимитов проекта, потому что ограничивать ими
# и так нечего — они уже выбраны под этот проект.
_STAGE_CEILING_SECONDS = 1800


def stage_timeout_seconds(task_timeout_seconds: int) -> int:
    """Сколько времени дать исторической сборке в проверочной ячейке.

    Прежде здесь стояло `min(..., 420)`, то есть МЕНЬШЕ, чем собственный лимит
    сборки проекта (600 с), и при этом кандидат работает на вдвое меньшем числе
    ядер. Такая сборка обязана быть медленнее обычной, а времени ей давали
    меньше — 23.09 живой откат на этом и умер: `next build` успешно
    скомпилировался, сгенерировал все страницы и был убит на последнем шаге, а
    владельцу написали «историческому коду нужна совместимая правка».

    Правило простое: не меньше, чем проект отводит себе сам.
    """
    return min(max(int(task_timeout_seconds), 1), _STAGE_CEILING_SECONDS)


class CodeRestorationEngine:
    def __init__(self, settings: Any = None, *, manager_factory: Any = None) -> None:
        if settings is None:
            from yleum_orchestrator.core.config import get_settings

            settings = get_settings()
        self.settings = settings
        self.root = Path(settings.cell_state_path).parent / "code-restoration-artifacts"
        self.manager_factory = manager_factory

    def _execution_journal(self) -> RestorationExecutionJournal:
        journal = getattr(self, "_restoration_execution_journal", None)
        if journal is None:
            journal = RestorationExecutionJournal(self.root)
            self._restoration_execution_journal = journal
        return cast(RestorationExecutionJournal, journal)

    def begin_cancel(self, request: CodeRestorationCancel) -> None:
        self._execution_journal().request_cancel(request)

    def _manager(self, workspace_id: UUID) -> Any:
        if self.manager_factory is not None:
            return self.manager_factory(workspace_id)
        from yleum_orchestrator.routers.workspace import (
            _require_docker_resource_manager,
            _workspace_provider,
        )

        return _require_docker_resource_manager(_workspace_provider(workspace_id))

    def activation_manager(self, workspace_id: UUID) -> Any:
        """Resolve the real Project Cell manager for a bound activation adapter."""

        return self._manager(workspace_id)

    async def activation_start_code_only_target(
        self,
        manager: Any,
        state: Any,
        *,
        code_volume: str,
        database_volume: str,
        epoch: int,
    ) -> None:
        """Start one code-only target while preserving the live DB volume."""

        from yleum_orchestrator.routers.workspace import _read_agent_workspace_files

        _machine, backend = manager.machine_runtime.parts(state)
        if backend.project_postgres_volume != database_volume:
            raise CellIdentityConflict("activation live database volume changed")
        files = await _read_agent_workspace_files(manager, code_volume)
        manifest = validate_supported_runtime(files)
        manifest_payload = manifest.model_dump(mode="json")
        target_application_running = await self._activation_application_running(
            backend,
            code_volume=code_volume,
            database_volume=database_volume,
            epoch=epoch,
        )
        if target_application_running:
            if await self._running_matches(
                manager,
                state,
                backend,
                code_volume,
                epoch,
                manifest_payload,
                database_volume,
            ):
                # A crash may happen after opening the serving boundary but
                # before the controller fence is persisted. Completing the
                # same deterministic operation is required before success.
                await self._complete_fence(manager, state, epoch, code_volume)
                self.activation_assert_target_controller(
                    manager,
                    manager.state_store.load(state.workspace_id),
                    epoch=epoch,
                    generation_run_id=state.active_generation_run_id,
                )
                return
            if await self._activation_services_ready(backend, manifest, epoch):
                # The app writers are already live. Reconcile only the trusted
                # boundary; clearing metadata or starting services again could
                # admit duplicate writers.
                await machine_effect(
                    manager.machine_runtime._start_boundary,
                    state,
                    manifest,
                    backend,
                    epoch,
                )
                await self._complete_fence(manager, state, epoch, code_volume)
                current = manager.state_store.load(state.workspace_id)
                self.activation_assert_target_controller(
                    manager,
                    current,
                    epoch=epoch,
                    generation_run_id=state.active_generation_run_id,
                )
                if await self._running_matches(
                    manager,
                    current,
                    backend,
                    code_volume,
                    epoch,
                    manifest_payload,
                    database_volume,
                ):
                    return
                raise CellResourceError("activation target pair is not running")
            # A partial bound app is ambiguous. Retire only that application
            # container; the live PostgreSQL container and volume stay intact.
            await machine_effect(backend.remove_machine)
        await self._activate_code(
            manager,
            state,
            backend,
            {
                "manifest": manifest.model_dump(mode="json"),
                "base_image": backend.base_image,
                "database_strategy": "preserve_current",
                "target_database_volume": database_volume,
            },
            code_volume,
            epoch,
            # The outer adaptation journal has already fsynced its PONR before
            # it invokes this public primitive.
            before_writers=lambda: None,
        )
        current = manager.state_store.load(state.workspace_id)
        if current is None:
            raise CellIdentityConflict("activation target state disappeared")
        self.activation_assert_target_controller(
            manager,
            current,
            epoch=epoch,
            generation_run_id=state.active_generation_run_id,
        )
        _machine, activated = manager.machine_runtime.parts(current)
        if not await self._running_matches(
            manager,
            current,
            activated,
            code_volume,
            epoch,
            manifest.model_dump(mode="json"),
            database_volume,
        ):
            raise CellResourceError("activation target pair is not running")

    async def activation_stop_source_application(
        self,
        manager: Any,
        state: Any,
        *,
        code_volume: str,
        database_volume: str,
        epoch: int,
    ) -> None:
        """Retire only the bound source app while PostgreSQL remains live."""

        _machine, backend = manager.machine_runtime.parts(state)
        if (
            backend.workspace_volume != code_volume
            or backend.project_postgres_volume != database_volume
        ):
            raise CellIdentityConflict("activation source pair changed")
        container = backend._container()
        if container is None:
            return
        labels = container.attrs.get("Config", {}).get("Labels") or container.labels or {}
        if labels.get("omnia.fencing_epoch") != str(epoch):
            raise CellIdentityConflict("activation source application fence changed")
        await machine_effect(backend.remove_machine)
        if backend._container() is not None:
            raise CellResourceError("activation source application did not stop")

    @staticmethod
    def activation_assert_target_controller(
        manager: Any,
        state: Any,
        *,
        epoch: int,
        generation_run_id: UUID | None,
        allow_running: bool = False,
    ) -> None:
        """Require the durable deterministic controller operation for a target."""

        if state is None:
            raise CellIdentityConflict("activation target controller state disappeared")
        mutation = CodeRestorationEngine._activation_mutation(state.workspace_id, epoch)
        operation = state.operation(mutation.operation_id)
        if (
            state.fencing_epoch != epoch
            or state.last_operation_id != mutation.operation_id
            or state.active_generation_run_id != generation_run_id
            or operation is None
            or operation.kind != "code_restore"
            or operation.status not in (
                {"running", "completed"} if allow_running else {"completed"}
            )
            or operation.fencing_epoch != epoch
            or operation.request_digest != mutation.request_digest
            or operation.generation_run_id != generation_run_id
        ):
            raise CellIdentityConflict("activation target controller fence is incomplete")

    async def activation_restart_source(
        self,
        manager: Any,
        state: Any,
        *,
        code_volume: str,
        database_volume: str,
    ) -> None:
        """Idempotently restart the unchanged pre-admission source pair."""

        machine, backend = manager.machine_runtime.parts(state)
        saved = machine.state()
        manifest = MachineManifest.model_validate(saved["manifest"])
        epoch = saved.get("epoch")
        if (
            type(epoch) is not int
            or epoch <= 0
            or backend.workspace_volume != code_volume
            or backend.project_postgres_volume != database_volume
        ):
            raise CellIdentityConflict("activation source pair changed")
        if await self._running_matches(
            manager,
            state,
            backend,
            code_volume,
            epoch,
            manifest.model_dump(mode="json"),
            database_volume,
        ):
            return
        if await self._activation_application_running(
            backend,
            code_volume=code_volume,
            database_volume=database_volume,
            epoch=epoch,
        ):
            if await self._activation_services_ready(backend, manifest, epoch):
                await machine_effect(
                    manager.machine_runtime._start_boundary,
                    state,
                    manifest,
                    backend,
                    epoch,
                )
                if await self._running_matches(
                    manager,
                    state,
                    backend,
                    code_volume,
                    epoch,
                    manifest.model_dump(mode="json"),
                    database_volume,
                ):
                    return
                raise CellResourceError("activation source pair is not running")
            await machine_effect(backend.remove_machine)
        await machine_effect(backend.ensure, manifest, epoch)
        await self._start(backend, manifest, epoch)
        await machine_effect(
            manager.machine_runtime._start_boundary,
            state,
            manifest,
            backend,
            epoch,
        )
        if not await self._running_matches(
            manager,
            state,
            backend,
            code_volume,
            epoch,
            manifest.model_dump(mode="json"),
            database_volume,
        ):
            raise CellResourceError("activation source pair is not running")

    async def activation_running_matches(
        self,
        manager: Any,
        state: Any,
        backend: Any,
        *,
        code_volume: str,
        database_volume: str,
        epoch: int,
        manifest: dict[str, Any],
    ) -> bool:
        """Confirm the exact running code/DB/fence pair."""

        return await self._running_matches(
            manager,
            state,
            backend,
            code_volume,
            epoch,
            manifest,
            database_volume,
        )

    @staticmethod
    async def _activation_application_running(
        backend: Any,
        *,
        code_volume: str,
        database_volume: str,
        epoch: int,
    ) -> bool:
        if (
            backend.workspace_volume != code_volume
            or backend.project_postgres_volume != database_volume
        ):
            return False
        container = backend._container()
        if container is None:
            return False
        await machine_effect(container.reload)
        labels = container.attrs.get("Config", {}).get("Labels") or container.labels or {}
        mounts = container.attrs.get("Mounts") or []
        workspace_mount = next(
            (
                item
                for item in mounts
                if item.get("Destination") == "/workspace"
            ),
            None,
        )
        mounted_volume = (
            workspace_mount.get("Name") or workspace_mount.get("Source")
            if isinstance(workspace_mount, dict)
            else None
        )
        if (
            labels.get("omnia.fencing_epoch") != str(epoch)
            or mounted_volume != code_volume
        ):
            raise CellIdentityConflict("activation target application identity changed")
        return bool(container.status == "running")

    @staticmethod
    async def _activation_services_ready(
        backend: Any,
        manifest: MachineManifest,
        epoch: int,
    ) -> bool:
        for name in manifest.service_order():
            service = next(item for item in manifest.services if item.name == name)
            result = await machine_effect(
                backend.service_status,
                service,
                epoch,
                include_logs=False,
            )
            if result.get("state") != "running" or result.get("ready") is not True:
                return False
        return True

    @staticmethod
    def _activation_mutation(workspace_id: UUID, epoch: int) -> LifecycleMutation:
        return LifecycleMutation(
            uuid5(workspace_id, "code-activation-" + str(epoch)),
            epoch,
            hashlib.sha256((str(workspace_id) + ":restore:" + str(epoch)).encode()).hexdigest(),
        )

    def _directory(self, operation_id: UUID) -> Path:
        path = self.root / str(operation_id)
        if path.is_symlink() or self.root.is_symlink():
            raise CellIdentityConflict("unsafe restoration artifacts")
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
        return path

    @staticmethod
    def _state(manager: Any, request: Any, *, epoch: int) -> Any:
        from yleum_orchestrator.services.cell_deletion import require_workspace_not_deleted

        require_workspace_not_deleted(manager.profile.state_path, request.workspace_id)
        state = manager.state_store.load(request.workspace_id)
        # Four different situations used to share one message; an operator reading
        # «identity, fence or activity changed» had to guess which (25.09.2026).
        if state is None:
            raise CellIdentityConflict("restoration source workspace state is missing")
        if state.project_id != request.project_id or state.owner_id != request.owner_id:
            raise CellIdentityConflict("restoration source identity changed")
        if state.fencing_epoch != epoch:
            raise CellIdentityConflict(
                f"restoration source fence changed: expected epoch {epoch}, "
                f"workspace is at {state.fencing_epoch}"
            )
        if state.active_generation_run_id is not None:
            raise CellIdentityConflict(
                "restoration source is held by an active generation "
                f"{state.active_generation_run_id}"
            )
        if manager.machine_runtime is None or not manager.machine_runtime.exists(
            state.workspace_id
        ):
            raise PreparationNeedsChanges(
                "Среда выбранного приложения пока не готова к восстановлению."
            )
        return state

    @staticmethod
    def _repair_legacy_release_receipt(
        manager: Any,
        request: CodeRestorationPrepare,
        state: Any,
        machine_state: dict[str, Any],
    ) -> Any:
        retained_epoch = legacy_release_serving_epoch(
            state,
            machine_epoch=machine_state.get("epoch"),
            machine_ready_epoch=machine_state.get("ready_epoch"),
        )
        if retained_epoch is None:
            return state
        return manager.state_store.repair_legacy_release_serving_epoch(
            request.workspace_id,
            expected_control_fencing_epoch=state.fencing_epoch,
            expected_last_operation_id=state.last_operation_id,
            retained_fencing_epoch=retained_epoch,
            machine_epoch=machine_state.get("epoch"),
            machine_ready_epoch=machine_state.get("ready_epoch"),
        )

    @staticmethod
    async def _source_database_volume_binding(source: Any) -> str:
        volume_name = source.project_postgres_volume
        if source.workspace_volume == volume_name:
            raise CellIdentityConflict("restoration source code and database volumes alias")
        volume = await machine_effect(
            source._lookup,
            source.client.volumes,
            volume_name,
            "project-volume",
        )
        attrs = volume.attrs if volume is not None else {}
        labels = attrs.get("Labels") or {}
        expected_labels = source.labels("project-volume")
        created_at = attrs.get("CreatedAt")
        if (
            volume is None
            or attrs.get("Name") != volume_name
            or not isinstance(created_at, str)
            or not created_at
            or any(labels.get(key) != value for key, value in expected_labels.items())
        ):
            raise CellIdentityConflict("restoration source database volume changed")
        return canonical_digest(
            {
                "name": volume_name,
                "created_at": created_at,
                "driver": attrs.get("Driver"),
                "scope": attrs.get("Scope"),
                "options": attrs.get("Options") or {},
                "labels": labels,
            }
        )

    @staticmethod
    async def _require_resume_checkpoint(
        request: CodeRestorationPrepare,
        source: Any,
        manifest: MachineManifest,
        revision: str,
    ) -> None:
        metadata = await machine_effect(source._metadata)
        if not isinstance(metadata, dict):
            raise CellIdentityConflict("restoration source checkpoint is unattested")
        sealed_at = metadata.get("environment_sealed_at")
        try:
            reference = MachineEnvironmentRef.model_validate(metadata.get("environment_ref"))
            if not isinstance(sealed_at, str):
                raise TypeError("checkpoint seal time is not a string")
            sealed = datetime.fromisoformat(sealed_at)
        except (TypeError, ValueError) as exc:
            raise CellIdentityConflict("restoration source checkpoint is unattested") from exc
        schema_digest = metadata.get("environment_schema_digest")
        manifest_digest = manifest.digest()
        expected_volumes = tuple(await machine_effect(source.environment_volume_names, manifest))
        reference_volumes = tuple(item.name for item in reference.volumes)
        if (
            metadata.get("environment_revision") != revision
            or not isinstance(schema_digest, str)
            or len(schema_digest) != 64
            or any(char not in "0123456789abcdef" for char in schema_digest)
            or sealed.tzinfo is None
            or reference.workspace_id != request.workspace_id
            or reference.manifest_digest != manifest_digest
            or reference.manifest is None
            or reference.manifest.digest() != manifest_digest
            or reference.base_image != source.base_image
            or source.workspace_volume not in reference_volumes
            or len(reference_volumes) != len(set(reference_volumes))
            or set(reference_volumes) != set(expected_volumes)
        ):
            raise CellIdentityConflict("restoration source checkpoint is unattested")

    @staticmethod
    async def _source_pair_running(
        manager: Any,
        state: Any,
        machine: Any,
        source: Any,
        manifest: MachineManifest,
        epoch: int,
    ) -> bool:
        saved = machine.state()
        try:
            saved_manifest = MachineManifest.model_validate(saved["manifest"])
        except (KeyError, TypeError, ValueError):
            raise CellIdentityConflict("restoration source manifest is unavailable") from None
        if (
            saved.get("epoch") != epoch
            or saved_manifest.digest() != manifest.digest()
        ):
            return False
        application = await machine_effect(source._container)
        postgres = await machine_effect(source._project_postgres)
        for container in (application, postgres):
            if container is None or container.labels.get("omnia.fencing_epoch") != str(epoch):
                return False
            await machine_effect(container.reload)
            if container.status != "running":
                return False
        if not any(
            item.get("Name") == source.workspace_volume and item.get("Destination") == "/workspace"
            for item in application.attrs.get("Mounts", [])
        ):
            return False
        if not any(
            item.get("Name") == source.project_postgres_volume
            and item.get("Destination") == "/var/lib/postgresql/data"
            for item in postgres.attrs.get("Mounts", [])
        ):
            return False
        for service in manifest.services:
            status = await machine_effect(
                source.service_status,
                service,
                epoch,
                include_logs=False,
            )
            if status.get("state") != "running" or status.get("ready") is not True:
                return False
        preview = await machine_effect(manager.machine_runtime.preview, state)
        return preview is not None and preview[0] == "running"

    async def prepare(
        self,
        request: CodeRestorationPrepare,
        *,
        cancel_requested: Callable[[], bool] | None = None,
    ) -> dict[str, Any]:
        files = source_text(request)

        def cancelled() -> bool:
            # Checked only between stages; an owner cancel never waits for the
            # whole install/build to finish (AV19.1). Cleanup runs in `finally`.
            return self._execution_journal().cancel_requested(request.operation_id) or bool(
                cancel_requested is not None and cancel_requested()
            )

        def cancelled_result() -> dict[str, Any]:
            return {
                "state": "cancelled",
                "candidate_id": None,
                "report": preparation_report(
                    blockers=["Подготовка восстановления отменена владельцем."]
                ),
            }

        try:
            manifest = validate_supported_runtime(files)
        except (PreparationNeedsChanges, ValueError, KeyError) as error:
            return {
                "state": "needs_changes",
                "candidate_id": None,
                "report": preparation_report(blockers=[str(error)]),
            }
        if cancelled():
            return cancelled_result()
        manager = self._manager(request.workspace_id)
        candidate_id = uuid5(request.operation_id, "candidate")
        directory = self._directory(request.operation_id)
        prepared_path = directory / "prepared.json"
        if prepared_path.exists():
            saved = json.loads(prepared_path.read_text())
            if saved["request_digest"] != request.digest():
                raise CellIdentityConflict("prepared restoration envelope changed")
            return cast(dict[str, Any], saved)
        observed_database_state: RestorationDatabaseState = "unknown"
        empty_materializer: list[str] | None = None
        empty_sql_migrations: list[tuple[str, str]] | None = None
        with machine_budget(870):
            async with manager.operation_lock.hold(request.workspace_id):
                try:
                    state = self._state(manager, request, epoch=request.fencing_epoch)
                except PreparationNeedsChanges as error:
                    return {
                        "state": "needs_changes",
                        "candidate_id": None,
                        "report": preparation_report(blockers=[str(error)]),
                    }
                adapter = manager.machine_runtime
                machine, source = adapter.parts(state)
                from yleum_orchestrator.routers.runtime import _workspace_revision
                from yleum_orchestrator.routers.workspace import _read_agent_workspace_files

                current_files = await _read_agent_workspace_files(manager, source.workspace_volume)
                current_revision = _workspace_revision(current_files)
                try:
                    source_bytes = await manager.docker.read_workspace_source_files(
                        source.workspace_volume
                    )
                    verify_source_inventory(
                        source_bytes, [item.model_dump() for item in request.current_files]
                    )
                    current_manifest = validate_supported_runtime(current_files)
                except (PreparationNeedsChanges, ValueError) as error:
                    return {
                        "state": "needs_changes",
                        "candidate_id": None,
                        "report": preparation_report(blockers=[str(error)]),
                    }
                source_machine_state = machine.state()
                try:
                    saved_manifest = MachineManifest.model_validate(
                        source_machine_state["manifest"]
                    )
                except (KeyError, TypeError, ValueError) as exc:
                    raise CellIdentityConflict(
                        "restoration source manifest is unavailable"
                    ) from exc
                if saved_manifest.digest() != current_manifest.digest():
                    raise CellIdentityConflict("restoration source manifest changed")
                source_code_volume = source.workspace_volume
                source_database_volume = source.project_postgres_volume
                source_database_binding = await self._source_database_volume_binding(source)
                source_serving_epoch = serving_fencing_epoch(
                    state,
                    machine_state=source_machine_state,
                )
                if source_machine_state.get("epoch") != source_serving_epoch:
                    raise CellIdentityConflict(
                        "restoration source serving machine epoch is detached"
                    )
                source_pair_running = await self._source_pair_running(
                    manager,
                    state,
                    machine,
                    source,
                    current_manifest,
                    source_serving_epoch,
                )
                source_ready_epoch = source_machine_state.get("ready_epoch")
                if source_pair_running and source_ready_epoch != source_serving_epoch:
                    cancelled_epoch = source_machine_state.get("cancelled_epoch")
                    if (
                        type(source_ready_epoch) is not int
                        or source_ready_epoch >= source_serving_epoch
                        or type(cancelled_epoch) is not int
                        or cancelled_epoch >= source_serving_epoch
                    ):
                        raise CellIdentityConflict(
                            "restoration source serving machine epoch is detached"
                        )
                    await adapter.resume_preview(state, epoch=source_serving_epoch)
                elif not source_pair_running:
                    if source_ready_epoch != source_serving_epoch:
                        raise CellIdentityConflict(
                            "restoration source serving machine epoch is detached"
                        )
                    await self._require_resume_checkpoint(
                        request,
                        source,
                        current_manifest,
                        current_revision,
                    )
                    await adapter.resume_preview(state, epoch=source_serving_epoch)

                resumed_state = self._state(manager, request, epoch=request.fencing_epoch)
                resumed_machine, resumed_source = adapter.parts(resumed_state)
                resumed_machine_state = resumed_machine.state()
                resumed_serving_epoch = serving_fencing_epoch(
                    resumed_state,
                    machine_state=resumed_machine_state,
                )
                if (
                    resumed_serving_epoch != source_serving_epoch
                    or resumed_machine_state.get("epoch") != resumed_serving_epoch
                    or resumed_machine_state.get("ready_epoch") != resumed_serving_epoch
                    or resumed_source.workspace_volume != source_code_volume
                    or resumed_source.project_postgres_volume != source_database_volume
                    or await self._source_database_volume_binding(resumed_source)
                    != source_database_binding
                ):
                    raise CellIdentityConflict("restoration source runtime changed during resume")
                resumed_files = await _read_agent_workspace_files(
                    manager,
                    resumed_source.workspace_volume,
                )
                resumed_revision = _workspace_revision(resumed_files)
                resumed_source_bytes = await manager.docker.read_workspace_source_files(
                    resumed_source.workspace_volume
                )
                try:
                    verify_source_inventory(
                        resumed_source_bytes,
                        [item.model_dump() for item in request.current_files],
                    )
                    resumed_manifest = validate_supported_runtime(resumed_files)
                except (PreparationNeedsChanges, ValueError) as exc:
                    raise CellIdentityConflict("restoration source changed during resume") from exc
                if (
                    resumed_revision != current_revision
                    or resumed_manifest.digest() != current_manifest.digest()
                    or not await self._source_pair_running(
                        manager,
                        resumed_state,
                        resumed_machine,
                        resumed_source,
                        resumed_manifest,
                        resumed_serving_epoch,
                    )
                ):
                    raise CellIdentityConflict("restoration source changed during resume")
                state = resumed_state
                machine = resumed_machine
                source = resumed_source
                current_files = resumed_files
                current_revision = resumed_revision
                source_bytes = resumed_source_bytes
                current_manifest = resumed_manifest
                source_runtime = self._runtime_identity(source, machine)
                capabilities = capability_diff(current_files, files)
                storage_blockers = []
                if current_manifest.data_stores or any(
                    service.mounts for service in current_manifest.services
                ):
                    storage_blockers.append("Нужна проверка текущих файловых хранилищ.")
                if storage_blockers:
                    return {
                        "state": "needs_changes",
                        "candidate_id": None,
                        "report": preparation_report(
                            blockers=storage_blockers,
                            capabilities=capabilities,
                        ),
                    }
                inventory = None
                checks: list[CompatibilityCheck] = []
                current_contract = DataContract(version=1)
                dump = b""
                identity_dump = b""
                empty_witness = None
                live_before = None
                source_business = source_technical = ""
                if request.binding_contract_version == 3:
                    source_machine_state = machine.state()
                    provisional = await machine_effect(
                        observe_live_source,
                        source,
                        machine,
                        state,
                        source_files=source_bytes,
                        schema={"empty_database_probe": 1},
                        machine_state=source_machine_state,
                    )
                    empty_witness = await machine_effect(
                        observe_empty_database,
                        source,
                        operation_id=request.operation_id,
                        workspace_id=request.workspace_id,
                        project_id=request.project_id,
                        database_identity_digest=provisional["database_identity_digest"],
                        observation_kind="source",
                    )
                if empty_witness is not None:
                    observed_database_state = "empty"
                    empty_materializer = empty_database_materializer(files)
                    if empty_materializer is None:
                        # Третий рецепт: применить сами файлы миграций. Он не
                        # исполняет код владельца и потому пробуется последним
                        # только по историческим причинам — риск у него меньше.
                        empty_sql_migrations = historical_sql_migrations(files)
                    if empty_materializer is None and empty_sql_migrations is None:
                        return {
                            "state": "needs_changes",
                            "candidate_id": None,
                            "report": preparation_report(
                                blockers=[
                                    empty_materializer_blocker(files)
                                    or "В выбранной версии нет поддерживаемого описания "
                                    "исторической схемы."
                                ],
                                observed_database_state=observed_database_state,
                                capabilities=capabilities,
                            ),
                        }
                    live_before = await machine_effect(
                        observe_live_source,
                        source,
                        machine,
                        state,
                        source_files=source_bytes,
                        schema={"empty_witness_catalog_digest": empty_witness.catalog_digest},
                        machine_state=machine.state(),
                    )
                    state = self._repair_legacy_release_receipt(
                        manager, request, state, source_machine_state
                    )
                    identity_dump = await machine_effect(
                        self._dump_identity_rows, source, empty_witness.identity_relations
                    )
                    fresh_witness = await machine_effect(
                        observe_empty_database,
                        source,
                        operation_id=request.operation_id,
                        workspace_id=request.workspace_id,
                        project_id=request.project_id,
                        database_identity_digest=empty_witness.database_identity_digest,
                        observation_kind="source",
                    )
                    if fresh_witness is None or not same_empty_source(empty_witness, fresh_witness):
                        raise CellIdentityConflict(
                            "restoration source changed while identity rows were prepared"
                        )
                    source_business = empty_witness.objects_digest
                    source_technical = empty_witness.technical_state_digest
                else:
                    # Evidence is gathered independently: row counts first, so a
                    # schema-analysis gap can never hide that data exist.
                    inventory = await machine_effect(observe_database, source, observed_on="source")
                    current_contract, catalog_blockers, unsupported = await machine_effect(
                        describe_live_catalog, source
                    )
                    inventory = inventory.model_copy(
                        update={"schema_analysis": "partial" if unsupported else "complete"}
                    )
                    checks = checks_from_unsupported(unsupported)
                    blockers = blocking_explanations(checks) if catalog_blockers else []
                    if blockers:
                        return {
                            "state": "needs_changes",
                            "candidate_id": None,
                            "report": preparation_report(
                                blockers=blockers,
                                inventory=inventory,
                                checks=checks,
                                capabilities=capabilities,
                            ),
                        }
                    if request.binding_contract_version in {2, 3}:
                        source_machine_state = machine.state()
                        live_before = await machine_effect(
                            observe_live_source,
                            source,
                            machine,
                            state,
                            source_files=source_bytes,
                            schema=current_contract.model_dump(mode="json"),
                            machine_state=source_machine_state,
                        )
                        state = self._repair_legacy_release_receipt(
                            manager, request, state, source_machine_state
                        )
                    # Only the dedicated database is copied. The trusted MAX core,
                    # credentials and queues are never attached.
                    dump = await machine_effect(self._dump, source)
                    fresh_contract, fresh_blockers, fresh_unsupported = await machine_effect(
                        describe_live_catalog, source
                    )
                    verify_post_dump_catalog(
                        current_contract,
                        catalog_blockers,
                        unsupported,
                        fresh_contract,
                        fresh_blockers,
                        fresh_unsupported,
                    )
                if live_before is not None and empty_witness is None:
                    state = self._state(manager, request, epoch=request.fencing_epoch)
                    fresh_machine_state = machine.state()
                    live_after = await machine_effect(
                        observe_live_source,
                        source,
                        machine,
                        state,
                        source_files=await manager.docker.read_workspace_source_files(
                            source.workspace_volume
                        ),
                        schema=fresh_contract.model_dump(mode="json"),
                        machine_state=fresh_machine_state,
                    )
                    if live_after != live_before:
                        raise CellIdentityConflict(
                            "restoration source changed while database export was prepared"
                        )
                    source_business, source_technical = await machine_effect(
                        exact_inventory_partition_digests, source, inventory
                    )
            try:
                if cancelled():
                    return cancelled_result()
                candidate = await self._candidate(manager, request, candidate_id, manifest)
                await machine_effect(self._seed_source, candidate, request)
                await self._run_stage(
                    request,
                    candidate,
                    ["pnpm", "install", "--frozen-lockfile", "--ignore-scripts"],
                    360,
                    stage="install",
                )
                if cancelled():
                    return cancelled_result()
                # Dependency installation has no customer data. Once data enter the
                # candidate, its public-egress proxy stays stopped until destruction.
                await machine_effect(self._disable_egress, candidate)
                package_scripts = json.loads(files.get("package.json", "{}")).get("scripts", {})
                if any(
                    key in package_scripts for key in ("prestart", "poststart", "predev", "postdev")
                ):
                    raise PreparationNeedsChanges("historical startup hooks require adaptation")
                target_witness = None
                database_digest = None
                if empty_witness is not None:
                    assert empty_materializer is not None or empty_sql_migrations is not None
                    if empty_materializer is not None:
                        await self._run_stage(
                            request,
                            candidate,
                            empty_materializer,
                            90,
                            stage="empty-database-migrations",
                        )
                    else:
                        assert empty_sql_migrations is not None
                        for _name, _sql in empty_sql_migrations:
                            await machine_effect(admin_sql, candidate, _sql)
                    expected_contract = await machine_effect(candidate_contract, candidate, files)
                    materialized_contract, catalog_blockers, unsupported = await machine_effect(
                        describe_live_catalog, candidate
                    )
                    if (
                        catalog_blockers
                        or unsupported
                        or not structural_materialization_matches(
                            expected_contract, materialized_contract
                        )
                    ):
                        raise PreparationNeedsChanges(
                            "Историческая схема не создана в изолированной базе."
                        )
                    if empty_sql_migrations is not None:
                        from yleum_orchestrator.services.project_migrations import (
                            record_witnessed_project_migrations,
                        )

                        # Only this controller-executed SQL inventory was witnessed.
                        # Never copy newer applied entries or guess from source files.
                        await machine_effect(
                            record_witnessed_project_migrations,
                            candidate,
                            dict(empty_sql_migrations),
                        )
                    await machine_effect(
                        self._install_identity_rows,
                        candidate,
                        empty_witness.identity_relations,
                        identity_dump,
                    )
                    assessment = ContractAssessment()
                    candidate_business = candidate_technical = ""
                else:
                    old_contract = await machine_effect(candidate_contract, candidate, files)
                    # Template MAX tables are served by the CURRENT trusted core, not
                    # by this project's dedicated database. No app role gains access.
                    old_contract = _project_data_contract(old_contract)
                    assessment = assess_contract(old_contract, current_contract)
                    checks = [
                        *checks_from_diagnostics(assessment.diagnostics),
                        *delete_warnings(assessment.blocked_deletes),
                    ]
                    # The copy lives only in this candidate's own isolated database.
                    await machine_effect(
                        admin_sql, candidate, dump.decode(), max_bytes=4 * 1024 * 1024
                    )
                    copied, copied_blockers = await machine_effect(catalog_contract, candidate)
                    if copied_blockers or copied != current_contract:
                        raise PreparationNeedsChanges(
                            "Структура копии данных не совпала с проверенной базой."
                        )
                    copied_inventory = await machine_effect(
                        observe_database, candidate, observed_on="candidate_copy"
                    )
                    assert inventory is not None
                    observed_database_state = inventory.presence
                binding = None
                if request.binding_contract_version in {2, 3} and empty_witness is None:
                    assert inventory is not None
                    if (
                        live_before is None
                        or inventory.coverage != "complete"
                        or copied_inventory.coverage != "complete"
                        or inventory.schema_analysis != "complete"
                    ):
                        raise PreparationNeedsChanges(
                            "Полная проверка исходной базы и копии недоступна."
                        )
                    candidate_business, candidate_technical = await machine_effect(
                        exact_inventory_partition_digests, candidate, copied_inventory
                    )
                    if (
                        source_business != candidate_business
                        or source_technical != candidate_technical
                    ):
                        raise PreparationNeedsChanges(
                            "Состав или записи копии базы не совпали с исходной базой."
                        )
                # Empty rows do not make incompatible schema safe for future writes.
                if assessment.blockers:
                    raise PreparationNeedsChanges(
                        "\n".join(blocking_explanations(checks))
                        or "Несовместимая структура данных: " + ", ".join(assessment.blockers)
                    )
                if cancelled():
                    return cancelled_result()
                tasks = [task for task in manifest.tasks if task.role == "full_build"]
                if not tasks:
                    tasks = [task for task in manifest.tasks if task.role == "build"]
                for index, task in enumerate(tasks):
                    await self._run_stage(
                        request,
                        candidate,
                        task.argv,
                        stage_timeout_seconds(task.timeout_seconds),
                        task.cwd,
                        stage=f"build:{index}",
                    )
                if cancelled():
                    return cancelled_result()
                async with self._execution_journal().producer(request, stage="launch"):
                    if cancelled():
                        raise RestorationExecutionCancelled(
                            "restoration cancellation was requested"
                        )
                    await self._start(candidate, manifest, 1)
                    if cancelled():
                        raise RestorationExecutionCancelled(
                            "restoration cancellation was requested"
                        )
                    await machine_effect(self._verify_source, candidate, request)
                    verify_source_inventory(
                        await manager.docker.read_workspace_source_files(
                            candidate.workspace_volume
                        ),
                        [
                            {
                                "path": item.path,
                                "sha256": hashlib.sha256(item.decoded()).hexdigest(),
                            }
                            for item in request.files
                        ],
                    )
                    if cancelled():
                        raise RestorationExecutionCancelled(
                            "restoration cancellation was requested"
                        )
                    if empty_witness is not None:
                        await machine_effect(candidate.stop_machine)
                        target_witness = await machine_effect(
                            observe_empty_database,
                            candidate,
                            operation_id=request.operation_id,
                            workspace_id=request.workspace_id,
                            project_id=request.project_id,
                            database_identity_digest=canonical_digest(
                                {
                                    "candidate_id": str(candidate_id),
                                    "database_volume": candidate.project_postgres_volume,
                                }
                            ),
                            observation_kind="candidate_copy",
                        )
                        if (
                            target_witness is None
                            or target_witness.identity_rows_digest
                            != empty_witness.identity_rows_digest
                        ):
                            raise PreparationNeedsChanges(
                                "Техническая идентичность новой пустой базы не совпала."
                            )
                        candidate_business = target_witness.objects_digest
                        candidate_technical = target_witness.technical_state_digest
                        await machine_effect(
                            self._rotate_database_password,
                            candidate,
                            source.project_postgres_password,
                        )
                    archive = directory / "code.tar"
                    database_archive = directory / "database.tar"
                    await machine_effect(candidate.stop)
                    digest = await machine_effect(
                        self._capture_code,
                        candidate,
                        archive,
                        reserve_bytes=self.settings.cell_required_free_disk_bytes,
                    )
                    if empty_witness is not None:
                        database_digest = await machine_effect(
                            self._capture_database,
                            candidate,
                            database_archive,
                            reserve_bytes=self.settings.cell_required_free_disk_bytes,
                        )
                if request.binding_contract_version == 2:
                    assert live_before is not None
                    binding = RestorationSourceBindingV2(
                        **live_before,
                        database_export_digest=hashlib.sha256(dump).hexdigest(),
                        source_business_inventory_digest=source_business,
                        candidate_business_inventory_digest=candidate_business,
                        source_technical_inventory_digest=source_technical,
                        candidate_technical_inventory_digest=candidate_technical,
                        candidate_artifact_digest=digest,
                    ).model_dump(mode="json")
                elif request.binding_contract_version == 3:
                    assert live_before is not None
                    if empty_witness is not None:
                        assert database_digest is not None and target_witness is not None
                        binding = RestorationSourceBindingV3(
                            **live_before,
                            database_strategy="replace_verified_empty",
                            witness_digest=empty_witness.digest(),
                            target_database_artifact_digest=database_digest,
                            database_export_digest=database_digest,
                            source_business_inventory_digest=source_business,
                            candidate_business_inventory_digest=candidate_business,
                            source_technical_inventory_digest=source_technical,
                            candidate_technical_inventory_digest=candidate_technical,
                            candidate_artifact_digest=digest,
                        ).model_dump(mode="json")
                    else:
                        binding = RestorationSourceBindingV3(
                            **live_before,
                            database_strategy="preserve_current",
                            database_export_digest=hashlib.sha256(dump).hexdigest(),
                            source_business_inventory_digest=source_business,
                            candidate_business_inventory_digest=candidate_business,
                            source_technical_inventory_digest=source_technical,
                            candidate_technical_inventory_digest=candidate_technical,
                            candidate_artifact_digest=digest,
                        ).model_dump(mode="json")
                prepared = {
                    "state": "ready",
                    "candidate_id": str(candidate_id),
                    "report": preparation_report(
                        retained=assessment.retained_columns,
                        cascading_deletes=assessment.blocked_deletes,
                        observed_database_state=observed_database_state,
                        inventory=inventory,
                        checks=checks,
                        capabilities=capabilities,
                        observed_contract=current_contract,
                    ),
                    "request_digest": request.digest(),
                    "workspace_revision": current_revision,
                    "source_runtime": source_runtime,
                    "current_files": [
                        item.model_dump(mode="json") for item in request.current_files
                    ],
                    "live_contract": current_contract.model_dump(mode="json"),
                    "manifest": manifest.model_dump(mode="json"),
                    "code_digest": digest,
                    "base_image": candidate.base_image,
                    "binding": binding,
                    "database_strategy": (
                        "replace_verified_empty"
                        if empty_witness is not None
                        else "preserve_current"
                    ),
                    "empty_witness": (
                        empty_witness.model_dump(mode="json") if empty_witness is not None else None
                    ),
                    "target_empty_witness": (
                        target_witness.model_dump(mode="json")
                        if target_witness is not None
                        else None
                    ),
                    "database_digest": database_digest,
                }
                write_controller_json(prepared_path, prepared)
                return prepared
            except RestorationExecutionCancelled:
                return cancelled_result()
            except (PreparationNeedsChanges, ValueError) as error:
                return {
                    "state": "needs_changes",
                    "candidate_id": None,
                    "report": preparation_report(
                        blockers=[line for line in str(error).split("\n") if line],
                        observed_database_state=observed_database_state,
                        inventory=inventory,
                        checks=checks,
                        capabilities=capabilities,
                        observed_contract=current_contract,
                    ),
                }
            finally:
                if not prepared_path.exists():
                    self._discard_code(directory)
                await self._cleanup_candidate(manager, request, candidate_id)

    async def _candidate(
        self,
        manager: Any,
        request: CodeRestorationPrepare,
        candidate_id: UUID,
        manifest: MachineManifest,
    ) -> Any:
        from yleum_orchestrator.services.cell_publication_capacity import production_manager

        candidate_manager = production_manager(manager, self.settings)
        # A restoration candidate is a verification workload, not a running app:
        # it must not compete with the draft/publication runtime reservations.
        candidate_manager = replace(
            candidate_manager,
            admission_gate=CellAdmissionGate(
                candidate_manager.profile,
                workload="verification",
                verification_cpu_cores=float(
                    getattr(self.settings, "cell_verification_cpu_cores", 2.0)
                ),
                verification_disk_bytes=int(
                    getattr(self.settings, "cell_verification_disk_bytes", 8 * 1024**3)
                ),
            ),
        )
        spec = WorkspaceSpec(
            workspace_id=candidate_id,
            project_id=request.project_id,
            owner_id=request.owner_id,
            profile_version=candidate_manager.profile.profile_version,
        )
        mutation = LifecycleMutation(uuid5(request.operation_id, "reserve"), 1, request.digest())
        journal = self._execution_journal()
        async with journal.producer(request, stage="provisioning"):
            if journal.cancel_requested(request.operation_id):
                raise RestorationExecutionCancelled("restoration cancellation was requested")
            # Real host accounting; a check cannot steal a running app's reservation.
            await candidate_manager.ensure(spec, mutation)
            if journal.cancel_requested(request.operation_id):
                raise RestorationExecutionCancelled("restoration cancellation was requested")
            state = candidate_manager.state_store.load(candidate_id)
            if state is None or candidate_manager.machine_runtime is None:
                raise CellResourceError("candidate machine provider unavailable")
            machine, backend = candidate_manager.machine_runtime.parts(state)
            write_controller_json(
                machine.path,
                {
                    "workspace_id": str(candidate_id),
                    "manifest": manifest.model_dump(mode="json"),
                    "epoch": 1,
                    "ready_epoch": None,
                    "operations": {},
                },
            )
            await machine_effect(backend.ensure, manifest, 1)
            return backend

    @staticmethod
    def _dump(backend: Any) -> bytes:
        from yleum_orchestrator.services.project_machine import machine_remaining_seconds

        args, env = admin_args(backend)
        execution = backend.client.api.exec_create(
            backend._project_postgres().id,
            [
                "pg_dump",
                "--no-owner",
                "--no-privileges",
                *args,
            ],
            environment=env,
        )
        connection = backend.client.api.exec_start(execution["Id"], socket=True)
        try:
            connection._sock.settimeout(machine_remaining_seconds(65))
            output = read_controller_output(connection, max_bytes=64 * 1024 * 1024)
        finally:
            close_controller_socket(connection)
        result = backend.client.api.exec_inspect(execution["Id"])
        if result.get("Running") or result.get("ExitCode") != 0:
            raise PreparationNeedsChanges(
                "Не удалось подготовить изолированную копию данных в пределах лимита."
            )
        return output

    @staticmethod
    def _dump_identity_rows(backend: Any, relations: list[str]) -> bytes:
        """Copy only attested platform identity rows; never product relations."""
        if not relations:
            return b""
        allowed = {"public.__omnia_migrations", "public.max_users"}
        if not set(relations).issubset(allowed):
            raise CellIdentityConflict("empty restoration identity relation is invalid")
        from yleum_orchestrator.services.project_machine import machine_remaining_seconds

        args, env = admin_args(backend)
        command = [
            "pg_dump",
            "--data-only",
            "--no-owner",
            "--no-privileges",
            "--column-inserts",
            *["--table=" + name for name in relations],
            *args,
        ]
        execution = backend.client.api.exec_create(
            backend._project_postgres().id, command, environment=env
        )
        connection = backend.client.api.exec_start(execution["Id"], socket=True)
        try:
            connection._sock.settimeout(machine_remaining_seconds(65))
            output = read_controller_output(connection, max_bytes=8 * 1024 * 1024)
        finally:
            close_controller_socket(connection)
        result = backend.client.api.exec_inspect(execution["Id"])
        if result.get("Running") or result.get("ExitCode") != 0:
            raise PreparationNeedsChanges("Не удалось сохранить техническую идентичность базы.")
        return output

    @staticmethod
    def _install_identity_rows(backend: Any, relations: list[str], dump: bytes) -> None:
        if not relations:
            if dump:
                raise CellIdentityConflict("unexpected empty restoration identity artifact")
            return
        allowed = {"public.__omnia_migrations", "public.max_users"}
        if not set(relations).issubset(allowed):
            raise CellIdentityConflict("empty restoration identity relation is invalid")
        names = ",".join(
            '"public"."' + name.split(".", 1)[1].replace('"', '""') + '"' for name in relations
        )
        admin_sql(backend, "TRUNCATE " + names + " CASCADE;\n" + dump.decode("utf-8"))

    @staticmethod
    def _rotate_database_password(backend: Any, password: str) -> None:
        escaped = password.replace("'", "''")
        admin_sql(backend, "ALTER ROLE postgres PASSWORD '" + escaped + "';")

    @staticmethod
    def _capture_database(backend: Any, path: Path, *, reserve_bytes: int) -> str:
        postgres = backend._project_postgres()
        if postgres is None:
            raise CellIdentityConflict("restoration candidate database is missing")
        postgres.reload()
        if postgres.status == "running":
            raise CellIdentityConflict("restoration candidate database is still running")
        return CodeRestorationEngine._capture_volume(
            backend, backend.project_postgres_volume, path, reserve_bytes=reserve_bytes
        )

    @staticmethod
    def _capture_volume(backend: Any, volume: str, path: Path, *, reserve_bytes: int) -> str:
        digest = hashlib.sha256()
        total = 0
        reserve = max(1024**3, reserve_bytes)
        limit = min(backend.disk_bytes, shutil.disk_usage(path.parent).free - reserve)
        if limit <= 0:
            raise PreparationNeedsChanges("Недостаточно места для проверенной копии базы.")
        with path.open("wb") as handle:
            path.chmod(0o600)
            for chunk in backend.export_volume(volume):
                total += len(chunk)
                if total > limit or shutil.disk_usage(path.parent).free - len(chunk) < reserve:
                    raise CellResourceError("restoration database artifact budget exceeded")
                digest.update(chunk)
                handle.write(chunk)
        return digest.hexdigest()

    @staticmethod
    def _seed_source(backend: Any, request: CodeRestorationPrepare) -> None:
        container = backend._container()
        # A new candidate volume is owned by this operation, never a live project.
        if container is None or not container.put_archive("/workspace", source_archive(request)):
            raise CellResourceError("candidate source upload failed")

    @staticmethod
    def _command(
        backend: Any,
        container: Any,
        argv: list[str],
        timeout: int,
        cwd: str = ".",
        stage: str = "",
    ) -> None:
        result = container.exec_run(["timeout", str(timeout), *argv], workdir="/workspace/" + cwd)
        if result.exit_code != 0:
            log = Path(backend.root) / str(backend.workspace_id) / "restoration-check.log"
            # Код выхода — в файл вместе с выводом: иначе оператор видит «ELIFECYCLE
            # Command failed» без единой ошибки и не может отличить убитую по
            # времени сборку от сломанной.
            header = f"[stage] exit_code={result.exit_code} timeout={timeout}s\n".encode()
            log.write_bytes(header + result.output[-24000:])
            log.chmod(0o600)
            details = stage_failure_details(
                stage=stage,
                argv=argv,
                cwd=cwd,
                exit_code=result.exit_code,
                timeout_seconds=timeout,
                output=result.output,
            )
            # Владельцу — причина, а не только вердикт: стадия, код выхода и конец
            # вывода его же сборки. Полный хвост остаётся в журнале операции.
            where = f" Стадия {stage}" if stage else " Стадия проверки"
            tail = owner_visible_tail(details["output_tail"])
            ending = f", конец вывода: «{tail}»." if tail else "."
            if result.exit_code == _TIMEOUT_EXIT_CODE:
                raise PreparationNeedsChanges(
                    f"Проверка исторического кода не уложилась в {timeout} с. "
                    "Это ограничение платформы, а не ошибка в коде версии."
                    f"{where}{ending}",
                    details=details,
                )
            raise PreparationNeedsChanges(
                "Проверка исторического кода не прошла; нужна совместимая правка."
                f"{where}, код выхода {result.exit_code}{ending}",
                details=details,
            )

    async def _run_stage(
        self,
        request: CodeRestorationPrepare,
        backend: Any,
        argv: list[str],
        command_timeout_seconds: int,
        cwd: str = ".",
        *,
        stage: str,
    ) -> None:
        journal = self._execution_journal()
        container = backend._container()
        receipt = journal.begin_attempt(
            request,
            container,
            stage=stage,
            argv=argv,
            cwd=cwd,
        )
        try:
            if journal.cancel_requested(request.operation_id):
                journal.finish_attempt(
                    request.operation_id,
                    receipt["attempt_id"],
                    state="stopped",
                )
                raise RestorationExecutionCancelled("restoration cancellation was requested")
            await machine_effect(
                self._command,
                backend,
                container,
                argv,
                command_timeout_seconds,
                cwd,
                stage,
            )
        except PreparationNeedsChanges as error:
            # The refusal's outcome lives with the attempt: stage, exit code,
            # timeout and the output tail — readable without the host.
            journal.finish_attempt(
                request.operation_id,
                receipt["attempt_id"],
                outcome=error.details or {"result": "failed", "stage": stage},
            )
            raise
        except BaseException:
            journal.finish_attempt(
                request.operation_id,
                receipt["attempt_id"],
                state="unknown",
            )
            raise
        else:
            journal.finish_attempt(
                request.operation_id,
                receipt["attempt_id"],
                outcome={"result": "ok", "stage": stage},
            )

    @staticmethod
    def _disable_egress(backend: Any) -> None:
        proxy = backend._lookup(backend.client.containers, backend.stem + "-proxy", "egress-proxy")
        if proxy is not None:
            proxy.stop(timeout=5)

    @staticmethod
    async def _start(backend: Any, manifest: MachineManifest, epoch: int) -> None:
        for name in manifest.service_order():
            service = next(service for service in manifest.services if service.name == name)
            await machine_effect(backend.start_service, service, epoch)
            result = await machine_effect(
                backend.service_status, service, epoch, include_logs=False
            )
            if not result["ready"]:
                raise PreparationNeedsChanges("Историческое приложение не прошло проверку запуска.")

    @staticmethod
    def _verify_source(backend: Any, request: CodeRestorationPrepare) -> None:
        expected = [
            [item.path, hashlib.sha256(item.decoded()).hexdigest(), int(item.mode, 8) & 0o777]
            for item in request.files
        ]
        script = """import hashlib,json,os,stat,sys
for path,digest,mode in json.load(sys.stdin):
 p='/workspace/'+path
 if not stat.S_ISREG(os.lstat(p).st_mode): sys.exit(1)
 if stat.S_IMODE(os.stat(p).st_mode)!=mode: sys.exit(1)
 if path.endswith('.tsbuildinfo') or path.rsplit('/',1)[-1]=='next-env.d.ts': continue
 if hashlib.sha256(open(p,'rb').read()).hexdigest()!=digest: sys.exit(1)
"""
        execution = backend.client.api.exec_create(
            backend._container().id,
            ["python3", "-c", script],
            stdin=True,
        )
        connection = backend.client.api.exec_start(execution["Id"], socket=True)
        try:
            connection._sock.settimeout(30)
            connection._sock.sendall(json.dumps(expected).encode())
            connection._sock.shutdown(socket.SHUT_WR)
            while connection._sock.recv(65536):
                pass
        finally:
            close_controller_socket(connection)
        result = backend.client.api.exec_inspect(execution["Id"])
        if result.get("Running") or result.get("ExitCode") != 0:
            raise PreparationNeedsChanges(
                "Сборка изменила сохранённые исходники или зависимости; нужна новая версия."
            )

    @staticmethod
    def _capture_code(backend: Any, path: Path, *, reserve_bytes: int) -> str:
        digest = hashlib.sha256()
        total = 0
        reserve = max(1024**3, reserve_bytes)
        limit = min(backend.disk_bytes, shutil.disk_usage(path.parent).free - reserve)
        if limit <= 0:
            raise PreparationNeedsChanges("Недостаточно места для проверенной копии кода.")
        with path.open("wb") as handle:
            for chunk in backend.export_volume(backend.workspace_volume):
                total += len(chunk)
                if total > limit or shutil.disk_usage(path.parent).free - len(chunk) < reserve:
                    raise CellResourceError("restoration artifact budget exceeded")
                digest.update(chunk)
                handle.write(chunk)
        return digest.hexdigest()

    async def _cleanup_candidate(self, manager: Any, request: Any, candidate_id: UUID) -> None:
        from yleum_orchestrator.services.cell_publication_capacity import production_manager

        candidate_manager = production_manager(manager, self.settings)
        operation_id = uuid5(request.operation_id, "cleanup")
        digest = hashlib.sha256(str(request.operation_id).encode()).hexdigest()
        async with candidate_manager.operation_lock.hold(candidate_id):
            state = candidate_manager.state_store.load(candidate_id)
            if state is None:
                return
            if (
                candidate_id != uuid5(request.operation_id, "candidate")
                or state.workspace_id != candidate_id
                or state.project_id != request.project_id
                or state.owner_id != request.owner_id
                or state.active_generation_run_id is not None
                or state.active_generation_fencing_epoch is not None
            ):
                raise CellIdentityConflict("candidate cleanup identity mismatch")
            previous = state.operation(operation_id)
            if previous is not None and (
                previous.operation_id != operation_id
                or not previous.matches_replay_envelope(
                    kind="destroy",
                    request_digest=digest,
                    fencing_epoch=state.fencing_epoch,
                    checkpoint_ref=None,
                )
                or previous.generation_run_id is not None
                or state.last_operation_id != operation_id
            ):
                raise CellIdentityConflict("candidate cleanup replay identity mismatch")
            mutation = LifecycleMutation(
                operation_id,
                previous.fencing_epoch if previous is not None else state.fencing_epoch + 1,
                digest,
            )
            if previous is not None and previous.status == "completed":
                # prepare may already have destroyed compute before returning needs_changes.
                # Its durable receipt is also the receipt for cancel: no new fence or halt.
                if (
                    previous.phase != "completed"
                    or previous.bundle_state != "retained"
                    or state.phase != "completed"
                    or state.bundle_state != "retained"
                    or state.resource_names is None
                ):
                    raise CellIdentityConflict("candidate cleanup completion mismatch")
                await candidate_manager._preflight_named_resources(
                    candidate_manager._spec_from_state(state), state.resource_names
                )
                if await candidate_manager.docker.list_workspace_containers(
                    candidate_id
                ) or await candidate_manager.docker.list_workspace_networks(candidate_id):
                    raise CellIdentityConflict("completed candidate cleanup still has compute")
                # Destroy records completion before releasing its reservation. A crash
                # in that final gap must not strand capacity or repeat runtime teardown.
                capacity_lock = candidate_manager.capacity_lock or candidate_manager.operation_lock
                async with capacity_lock.hold_named("host-capacity-admission"):
                    candidate_manager._release_capacity(candidate_id, mutation)
            else:
                await candidate_manager.destroy_compute_without_lock(
                    candidate_id,
                    mutation,
                    checkpoint_ref=None,
                    record_operation=True,
                    capture=False,
                )
            # Keep the candidate lock through retries of partially removed volumes.
            volumes = await candidate_manager.docker.list_workspace_volumes(candidate_id)
            for volume in volumes:
                if (
                    volume.labels.get("omnia.workspace_id") != str(candidate_id)
                    or volume.labels.get("omnia.project_id") != str(request.project_id)
                    or volume.labels.get("omnia.owner_id") != str(request.owner_id)
                ):
                    raise CellIdentityConflict("candidate volume cleanup identity mismatch")
            for volume in volumes:
                await candidate_manager.docker.remove_volume(volume.name)

    async def cancel(self, request: CodeRestorationCancel, prepared: dict[str, Any] | None) -> None:
        directory = self._directory(request.operation_id)
        if (directory / "activation.json").exists():
            raise CellIdentityConflict("an admitted activation cannot be cancelled")
        journal = self._execution_journal()
        journal.request_cancel(request)
        manager = self._manager(request.workspace_id)
        docker_host = getattr(manager.docker, "docker_host", None)
        if not isinstance(docker_host, str) or not docker_host:
            # No attempt means there is no container transport to contact. This
            # keeps old cleanup-only journals and test providers replayable.
            attempt_path = directory / "attempt.json"
            if attempt_path.exists():
                raise CellResourceError("restoration cancellation Docker client unavailable")

            def api_factory() -> Any:
                raise CellResourceError("restoration cancellation Docker client unavailable")
        else:
            api_factory = dedicated_docker_api_factory(
                docker_host,
                transport_timeout_seconds=0.75,
            )

        # Exact-ID stop is independent of producer/default-executor locks. It
        # must run first so a saturated controller pool cannot delay the kill.
        await journal.stop_active(
            request,
            api_factory=api_factory,
            timeout_seconds=4.0,
        )
        async with journal.no_producers(request, timeout_seconds=0.25):
            await self._cleanup_candidate(
                manager, request, uuid5(request.operation_id, "candidate")
            )
            # Recheck the immutable attempt while the producer lease excludes
            # late ensure/launch work. Cleanup completion alone is insufficient.
            await journal.stop_active(
                request,
                api_factory=api_factory,
                timeout_seconds=4.0,
            )
            self._discard_code(directory)
            journal.complete_cancel(request)

    @staticmethod
    def _preflight_receipt(request: CodeRestorationApply) -> dict[str, Any]:
        return {
            "operation_id": str(request.operation_id),
            "workspace_id": str(request.workspace_id),
            "project_id": str(request.project_id),
            "owner_id": str(request.owner_id),
            "candidate_id": str(request.candidate_id),
            "source_commit_sha": request.planned_commit_sha,
            "fencing_epoch": request.fencing_epoch,
            "retained_source_fencing_epoch": None,
            "binding_digest": request.binding_digest,
            "state": "preflight",
            "preflight_attempted": False,
            "effects_admitted": False,
        }

    def begin_preflight(self, request: CodeRestorationApply) -> None:
        path = self._directory(request.operation_id) / "activation.json"
        if not path.exists():
            write_controller_json(path, self._preflight_receipt(request))
        self._validate_preflight_receipt(json.loads(path.read_text()), request)

    @staticmethod
    def _validate_preflight_receipt(intent: dict[str, Any], request: CodeRestorationApply) -> None:
        expected = {
            "operation_id": str(request.operation_id),
            "workspace_id": str(request.workspace_id),
            "project_id": str(request.project_id),
            "owner_id": str(request.owner_id),
            "candidate_id": str(request.candidate_id),
            "source_commit_sha": request.planned_commit_sha,
            "fencing_epoch": request.fencing_epoch,
            "binding_digest": request.binding_digest,
        }
        if any(intent.get(key) != value for key, value in expected.items()):
            raise CellIdentityConflict("restoration preflight receipt identity mismatch")
        retained_epoch = intent.get("retained_source_fencing_epoch")
        if retained_epoch is not None and (
            type(retained_epoch) is not int
            or retained_epoch < 1
            or retained_epoch > request.expected_fencing_epoch
        ):
            raise CellIdentityConflict("restoration retained serving epoch is invalid")

    def _claim_preflight(self, request: CodeRestorationApply) -> str:
        path = self._directory(request.operation_id) / "activation.json"
        intent = json.loads(path.read_text())
        self._validate_preflight_receipt(intent, request)
        if intent.get("state") != "preflight":
            return "observe"
        if intent.get("preflight_attempted") is True:
            return "recover"
        intent["preflight_attempted"] = True
        write_controller_json(path, intent)
        return "start"

    def _capture_preflight_serving_epoch(self, manager: Any, request: CodeRestorationApply) -> None:
        path = self._directory(request.operation_id) / "activation.json"
        intent = json.loads(path.read_text())
        self._validate_preflight_receipt(intent, request)
        if (
            intent.get("state") != "preflight"
            or intent.get("retained_source_fencing_epoch") is not None
        ):
            return
        current = manager.state_store.load(request.workspace_id)
        if (
            current is None
            or current.project_id != request.project_id
            or current.owner_id != request.owner_id
            or current.fencing_epoch != request.expected_fencing_epoch
        ):
            return
        intent["retained_source_fencing_epoch"] = serving_fencing_epoch(current)
        write_controller_json(path, intent)

    def _confirm_preflight_rejection(
        self,
        manager: Any,
        request: CodeRestorationApply,
        *,
        reason_type: str,
    ) -> dict[str, Any]:
        path = self._directory(request.operation_id) / "activation.json"
        intent = json.loads(path.read_text())
        if intent.get("effects_admitted") is not False or intent.get("state") not in {
            "preflight",
            "rejected",
            "superseded",
        }:
            raise CellIdentityConflict("restoration rejection receipt is not pre-effect")
        current = manager.state_store.load(request.workspace_id)
        if (
            current is None
            or current.project_id != request.project_id
            or current.owner_id != request.owner_id
        ):
            raise CellIdentityConflict("restoration rejection owner changed")
        mutation = LifecycleMutation(
            uuid5(request.operation_id, "preflight-rejection"),
            request.fencing_epoch,
            hashlib.sha256(
                (
                    str(request.operation_id)
                    + ":preflight-rejection:"
                    + str(request.expected_fencing_epoch)
                    + ":"
                    + str(request.fencing_epoch)
                    + ":"
                    + str(request.binding_digest)
                ).encode()
            ).hexdigest(),
        )
        if current.fencing_epoch == request.expected_fencing_epoch:
            if current.active_generation_run_id is not None:
                raise CellIdentityConflict("restoration rejection cannot replace active generation")
            if current.resource_names is None:
                raise CellIdentityConflict("restoration rejection resources are unavailable")
            retained_epoch = serving_fencing_epoch(current)
            recorded_retained_epoch = intent.get("retained_source_fencing_epoch")
            if recorded_retained_epoch not in {None, retained_epoch}:
                raise CellIdentityConflict("restoration retained serving epoch changed")
            intent["retained_source_fencing_epoch"] = retained_epoch
            intent["retained_bundle_state"] = current.bundle_state
            write_controller_json(path, intent)
            manager.state_store.begin(
                manager._spec_from_state(current),
                mutation,
                kind="restoration_rejection",
                phase="rejected",
                resource_names=current.resource_names,
            )
            current = manager.state_store.load(request.workspace_id)
        if current is not None and current.fencing_epoch == request.fencing_epoch:
            recorded = current.operation(mutation.operation_id)
            if current.last_operation_id != mutation.operation_id:
                intent["state"] = "superseded"
                intent["fence_reconciled"] = False
                intent["retained_source_fencing_epoch"] = None
            else:
                retained_epoch = intent.get("retained_source_fencing_epoch")
                if (
                    type(retained_epoch) is not int
                    or retained_epoch < 1
                    or retained_epoch > request.expected_fencing_epoch
                ):
                    raise CellIdentityConflict("restoration retained serving epoch is unavailable")
                if recorded is None or not recorded.matches_replay_envelope(
                    kind="restoration_rejection",
                    request_digest=mutation.request_digest,
                    fencing_epoch=mutation.fencing_epoch,
                    checkpoint_ref=None,
                ):
                    raise CellIdentityConflict("restoration rejection operation envelope changed")
                if recorded.status != "completed":
                    retained_bundle_state = intent.get("retained_bundle_state")
                    if not isinstance(retained_bundle_state, str) or not retained_bundle_state:
                        raise CellIdentityConflict(
                            "restoration rejection source state is unavailable"
                        )
                    current = manager.state_store.complete(
                        request.workspace_id,
                        mutation,
                        phase="rejected",
                        bundle_state=retained_bundle_state,
                        detail="retained_source_fencing_epoch=" + str(retained_epoch),
                    )
                    recorded = current.operation(mutation.operation_id)
                if (
                    recorded is None
                    or recorded.status != "completed"
                    or recorded.detail != "retained_source_fencing_epoch=" + str(retained_epoch)
                ):
                    raise CellIdentityConflict("restoration rejection receipt is incomplete")
                intent["fence_reconciled"] = True
        elif current is not None and current.fencing_epoch > request.fencing_epoch:
            # A later operation owns the monotonic fence. This rejected operation
            # is still proven effect-free and must never rewrite the newer state.
            intent["fence_reconciled"] = False
            if intent.get("retained_source_fencing_epoch") is None:
                intent["state"] = "superseded"
        else:
            raise CellIdentityConflict("restoration rejection fence cannot be reconciled")
        if intent.get("state") != "superseded":
            intent["state"] = "rejected"
        intent["reason_type"] = reason_type
        write_controller_json(path, intent)
        self._discard_rejected_code(path.parent)
        return self._observed(intent, applied=False)

    async def apply(
        self, request: CodeRestorationApply, prepared: dict[str, Any]
    ) -> dict[str, Any]:
        # Written before the first await, including the coordinator's lock await.
        # A crash/cancellation anywhere in preflight therefore proves no effect
        # was admitted and recovery can finish a safe negative without rechecking.
        self.begin_preflight(request)
        manager = self._manager(request.workspace_id)
        self._capture_preflight_serving_epoch(manager, request)
        preflight = self._claim_preflight(request)
        directory = self._directory(request.operation_id)
        async with manager.operation_lock.hold(request.workspace_id):
            if preflight == "observe":
                result = await self._observe_locked(manager, request, prepared)
                if result is None:
                    raise CellResourceError("activation outcome is not yet confirmed")
                return result
            if preflight == "recover":
                return self._confirm_preflight_rejection(
                    manager, request, reason_type="InterruptedPreflight"
                )
            try:
                state, machine, backend = await self._activation_preflight(
                    manager, request, prepared
                )
            except Exception as exc:
                if request.binding_digest is None:
                    raise
                return self._confirm_preflight_rejection(
                    manager, request, reason_type=type(exc).__name__
                )
            from yleum_orchestrator.routers.runtime import _workspace_revision
            from yleum_orchestrator.routers.workspace import _read_agent_workspace_files

            volume = backend.stem + "-code-" + request.operation_id.hex
            database_strategy = prepared.get("database_strategy", "preserve_current")
            database_volume = (
                backend.stem + "-db-" + request.operation_id.hex
                if database_strategy == "replace_verified_empty"
                else backend.project_postgres_volume
            )
            old = {
                "metadata": backend._metadata(),
                "machine": machine.state(),
                "workspace_volume": backend.workspace_volume,
                "database_volume": backend.project_postgres_volume,
                "contract": prepared["live_contract"],
            }
            intent = {
                "operation_id": str(request.operation_id),
                "workspace_id": str(request.workspace_id),
                "project_id": str(request.project_id),
                "owner_id": str(request.owner_id),
                "candidate_id": str(request.candidate_id),
                "source_commit_sha": request.planned_commit_sha,
                "fencing_epoch": request.fencing_epoch,
                "binding_digest": request.binding_digest,
                "retained_source_fencing_epoch": request.expected_fencing_epoch,
                # Preparation and writer shutdown remain reversible. The PONR
                # is fsynced by admit_target_writers immediately before the
                # first target start for every database strategy.
                "effects_admitted": False,
                "old": old,
                "volume": volume,
                "database_strategy": database_strategy,
                "database_volume": database_volume,
                "pair_plan_digest": canonical_digest(
                    {
                        "code_digest": prepared["code_digest"],
                        "database_digest": prepared.get("database_digest"),
                        "database_strategy": database_strategy,
                        "old_code_volume": backend.workspace_volume,
                        "old_database_volume": backend.project_postgres_volume,
                        "new_code_volume": volume,
                        "new_database_volume": database_volume,
                        "witness": prepared.get("empty_witness"),
                    }
                ),
                "state": "intent",
            }
            _write_activation_journal(directory / "activation.json", intent)
            try:
                current = await _read_agent_workspace_files(manager, backend.workspace_volume)
                if _workspace_revision(current) != prepared["workspace_revision"]:
                    raise CellIdentityConflict("restoration source changed after checking")
                verify_source_inventory(
                    await manager.docker.read_workspace_source_files(backend.workspace_volume),
                    prepared["current_files"],
                )
                if database_strategy == "replace_verified_empty":
                    prepared_witness = EmptyDatabaseWitness.model_validate(
                        prepared["empty_witness"]
                    )
                    current_witness = await machine_effect(
                        observe_empty_database,
                        backend,
                        operation_id=request.operation_id,
                        workspace_id=request.workspace_id,
                        project_id=request.project_id,
                        database_identity_digest=prepared_witness.database_identity_digest,
                        observation_kind="source",
                    )
                    if current_witness is None or not same_empty_source(
                        prepared_witness, current_witness
                    ):
                        raise CellIdentityConflict("restoration source is no longer empty")
                else:
                    live, blockers = await machine_effect(catalog_contract, backend)
                    if blockers or not contract_matches(live, prepared["live_contract"]):
                        raise CellIdentityConflict(
                            "restoration data contract changed after checking"
                        )
                archive = directory / "code.tar"
                if await machine_effect(self._file_digest, archive) != prepared["code_digest"]:
                    raise CellIdentityConflict("restoration code artifact changed")
                if backend._lookup(backend.client.volumes, volume, "project-volume") is not None:
                    raise CellIdentityConflict(
                        "activation volume already exists without completed observation"
                    )
                target = replace(backend, workspace_volume=volume)
                # Source readers and future generations verify the canonical
                # Project Cell identity as well as the machine-volume identity.
                code_volume_labels: dict[str, str] = {}
                if database_strategy == "replace_verified_empty":
                    if request.binding_digest is None or not isinstance(
                        prepared.get("database_digest"), str
                    ):
                        raise CellIdentityConflict(
                            "empty restoration artifact identity is unavailable"
                        )
                    code_volume_labels = target.restoration_volume_labels(
                        request.operation_id,
                        purpose="code",
                        binding_digest=request.binding_digest,
                        artifact_digest=prepared["code_digest"],
                    )
                await manager._ensure_volume(
                    volume,
                    {
                        **manager._state_labels(state, "project-volume"),
                        **target.labels("project-volume"),
                        **code_volume_labels,
                    },
                )
                await machine_effect(target.import_volume, volume, archive)
                if database_strategy == "replace_verified_empty":
                    if (
                        backend._lookup(backend.client.volumes, database_volume, "project-volume")
                        is not None
                    ):
                        raise CellIdentityConflict(
                            "activation database volume already exists without observation"
                        )
                    await manager._ensure_volume(
                        database_volume,
                        {
                            **manager._state_labels(state, "project-volume"),
                            **target.labels("project-volume"),
                            **target.restoration_volume_labels(
                                request.operation_id,
                                purpose="database",
                                binding_digest=request.binding_digest,
                                artifact_digest=prepared["database_digest"],
                            ),
                        },
                    )
                    await machine_effect(
                        target.import_restoration_database,
                        request.operation_id,
                        directory / "database.tar",
                        binding_digest=request.binding_digest,
                        artifact_digest=prepared["database_digest"],
                    )
                    intent["state"] = "writers_stopping"
                    _write_activation_journal(directory / "activation.json", intent)
                    final_witness = await self._quiesce_empty_source(backend, request, prepared)
                    intent["final_witness"] = final_witness.model_dump(mode="json")
                    intent["final_witness_digest"] = final_witness.digest()
                    intent["pair_seal"] = canonical_digest(
                        {
                            "code_digest": prepared["code_digest"],
                            "database_digest": prepared["database_digest"],
                            "witness_digest": final_witness.digest(),
                            "old_code_volume": old["workspace_volume"],
                            "old_database_volume": old["database_volume"],
                            "new_code_volume": volume,
                            "new_database_volume": database_volume,
                        }
                    )
                intent["state"] = "switching"
                _write_activation_journal(directory / "activation.json", intent)
                # Writers stop only after all checks and code import have succeeded.
                await machine_effect(backend.remove)
                activation_prepared = {
                    **prepared,
                    "target_database_volume": database_volume,
                }

                def admit_target_writers() -> None:
                    intent["state"] = "target_writers_admitted"
                    intent["effects_admitted"] = True
                    _write_activation_journal(directory / "activation.json", intent)

                await self._activate_code(
                    manager,
                    state,
                    backend,
                    activation_prepared,
                    volume,
                    request.fencing_epoch,
                    before_writers=admit_target_writers,
                )
                intent["source_revision"] = await self._complete_activation(
                    manager, state, backend, request, intent
                )
            except Exception as error:
                error_path = directory / "activation-error.log"
                error_path.write_text(traceback.format_exc(), encoding="utf-8")
                error_path.chmod(0o600)
                if intent.get("state") in {"target_writers_admitted", "target_recovery"}:
                    intent["state"] = "target_recovery"
                    _write_activation_journal(directory / "activation.json", intent)
                    raise CellResourceError(
                        "target restoration requires forward recovery"
                    ) from error
                intent["state"] = "reverting"
                _write_activation_journal(directory / "activation.json", intent)
                await self._recover_old(manager, state, backend, old, request.fencing_epoch)
                await self._record_reverted_pair(
                    manager,
                    state,
                    backend,
                    request,
                    prepared,
                    intent,
                    directory / "activation.json",
                    old,
                )
                self._discard_code(directory)
                return self._observed(intent, applied=False)
            intent["state"] = "active"
            _write_activation_journal(directory / "activation.json", intent)
            self._discard_code(directory)
            return self._observed(intent)

    async def _activation_preflight(
        self, manager: Any, request: CodeRestorationApply, prepared: dict[str, Any]
    ) -> tuple[Any, Any, Any]:
        from yleum_orchestrator.routers.runtime import _workspace_revision
        from yleum_orchestrator.routers.workspace import _read_agent_workspace_files

        state = self._state(manager, request, epoch=request.expected_fencing_epoch)
        machine, backend = manager.machine_runtime.parts(state)
        if prepared.get("binding") is None:
            if request.binding_digest is not None:
                raise CellIdentityConflict("restoration source binding is unavailable")
            return state, machine, backend
        binding_payload = prepared["binding"]
        binding = (
            RestorationSourceBindingV3.model_validate(binding_payload)
            if binding_payload.get("version") == 3
            else RestorationSourceBindingV2.model_validate(binding_payload)
        )
        if request.binding_digest != binding.digest():
            raise CellIdentityConflict("restoration source binding digest changed")
        current = await _read_agent_workspace_files(manager, backend.workspace_volume)
        if _workspace_revision(current) != prepared["workspace_revision"]:
            raise CellIdentityConflict("restoration source changed after checking")
        source_bytes = await manager.docker.read_workspace_source_files(backend.workspace_volume)
        verify_source_inventory(source_bytes, prepared["current_files"])
        if (
            isinstance(binding, RestorationSourceBindingV3)
            and binding.database_strategy == "replace_verified_empty"
        ):
            prepared_witness = EmptyDatabaseWitness.model_validate(prepared["empty_witness"])
            current_witness = await machine_effect(
                observe_empty_database,
                backend,
                operation_id=request.operation_id,
                workspace_id=request.workspace_id,
                project_id=request.project_id,
                database_identity_digest=prepared_witness.database_identity_digest,
                observation_kind="source",
            )
            if current_witness is None or not same_empty_source(prepared_witness, current_witness):
                raise CellIdentityConflict("restoration source is no longer empty")
            observed = await machine_effect(
                observe_live_source,
                backend,
                machine,
                state,
                source_files=source_bytes,
                schema={"empty_witness_catalog_digest": current_witness.catalog_digest},
            )
        else:
            live, blockers = await machine_effect(catalog_contract, backend)
            if blockers or not contract_matches(live, prepared["live_contract"]):
                raise CellIdentityConflict("restoration data contract changed after checking")
            observed = await machine_effect(
                observe_live_source,
                backend,
                machine,
                state,
                source_files=source_bytes,
                schema=live.model_dump(mode="json"),
            )
        rebound = binding.model_copy(update=observed)
        if rebound.live_identity_digest() != binding.live_identity_digest():
            raise CellIdentityConflict("restoration live source binding changed")
        archive = self._directory(request.operation_id) / "code.tar"
        archive_digest = await machine_effect(self._file_digest, archive)
        if (
            archive_digest != prepared["code_digest"]
            or archive_digest != binding.candidate_artifact_digest
        ):
            raise CellIdentityConflict("restoration code artifact changed")
        if isinstance(binding, RestorationSourceBindingV3):
            database_archive = self._directory(request.operation_id) / "database.tar"
            if binding.database_strategy == "replace_verified_empty":
                if (
                    not database_archive.is_file()
                    or await machine_effect(self._file_digest, database_archive)
                    != binding.target_database_artifact_digest
                ):
                    raise CellIdentityConflict("restoration database artifact changed")
            elif database_archive.exists():
                raise CellIdentityConflict("preserved restoration has database artifact")
        return state, machine, backend

    async def _quiesce_empty_source(
        self,
        backend: Any,
        request: CodeRestorationApply,
        prepared: dict[str, Any],
    ) -> EmptyDatabaseWitness:
        prepared_witness = EmptyDatabaseWitness.model_validate(prepared["empty_witness"])
        await machine_effect(backend.stop_machine)
        current = await machine_effect(
            observe_empty_database,
            backend,
            operation_id=request.operation_id,
            workspace_id=request.workspace_id,
            project_id=request.project_id,
            database_identity_digest=prepared_witness.database_identity_digest,
            observation_kind="quiesced_source",
        )
        if current is None or not same_empty_source(prepared_witness, current):
            raise CellIdentityConflict("restoration source is no longer empty")
        return cast(EmptyDatabaseWitness, current)

    async def _activate_code(
        self,
        manager: Any,
        state: Any,
        backend: Any,
        prepared: dict[str, Any],
        volume: str,
        epoch: int,
        before_writers: Callable[[], None],
    ) -> None:
        adapter = manager.machine_runtime
        manifest = MachineManifest.model_validate(prepared["manifest"])
        metadata = backend._metadata()
        metadata.update(
            active_code_volume=volume,
            active_database_volume=(
                prepared["target_database_volume"]
                if prepared.get("database_strategy") == "replace_verified_empty"
                else metadata.get("active_database_volume")
            ),
            manifest=manifest.model_dump(mode="json"),
            restored_image=prepared["base_image"],
            environment_ref=None,
            services={},
            exec_logs={},
            exec_pids={},
            quiesce_state=None,
            epoch=epoch,
        )
        write_controller_json(backend.metadata_path, metadata)
        backend.workspace_volume = volume
        machine, _ = adapter.parts(state)
        saved = machine.state()
        saved.update(
            manifest=manifest.model_dump(mode="json"),
            epoch=epoch,
            ready_epoch=epoch,
            operations={},
            cancelled_epoch=0,
        )
        write_controller_json(machine.path, saved)
        # The live database volume is reused as-is; only the code volume changes.
        # backend.ensure starts the target machine container, so the durable
        # PONR must precede it as well as the later service writer starts.
        before_writers()
        await machine_effect(backend.ensure, manifest, epoch)
        await self._start(backend, manifest, epoch)
        await machine_effect(adapter._start_boundary, state, manifest, backend, epoch)
        await self._complete_fence(manager, state, epoch, volume)

    @staticmethod
    async def _complete_fence(manager: Any, state: Any, epoch: int, volume: str) -> None:
        mutation = CodeRestorationEngine._activation_mutation(state.workspace_id, epoch)
        current = manager.state_store.load(state.workspace_id)
        if (
            current is None
            or current.project_id != state.project_id
            or current.owner_id != state.owner_id
            or current.active_generation_run_id != state.active_generation_run_id
            or current.fencing_epoch not in {state.fencing_epoch, epoch}
        ):
            raise CellIdentityConflict("activation controller ownership changed")
        if current.fencing_epoch == epoch:
            operation = current.operation(mutation.operation_id)
            if (
                current.last_operation_id != mutation.operation_id
                or operation is None
                or operation.kind != "code_restore"
                or operation.status not in {"running", "completed"}
                or operation.fencing_epoch != epoch
                or operation.request_digest != mutation.request_digest
            ):
                raise CellIdentityConflict("activation controller operation changed")
        capacity_lock = manager.capacity_lock or manager.operation_lock
        async with capacity_lock.hold_named("host-capacity-admission"):
            manager._capacity_reservation_store().rebind(state.workspace_id, mutation)
        spec = WorkspaceSpec(
            workspace_id=state.workspace_id,
            project_id=state.project_id,
            owner_id=state.owner_id,
            profile_version=state.profile_version,
            generation_run_id=state.active_generation_run_id,
        )
        manager.state_store.begin(
            spec,
            mutation,
            kind="code_restore",
            phase="activating",
            resource_names=state.resource_names,
        )
        manager.state_store.complete(
            state.workspace_id, mutation, phase="completed", bundle_state="resources_ready"
        )
        completed = manager.state_store.load(state.workspace_id)
        CodeRestorationEngine.activation_assert_target_controller(
            manager,
            completed,
            epoch=epoch,
            generation_run_id=state.active_generation_run_id,
        )

    async def _recover_old(
        self,
        manager: Any,
        state: Any,
        backend: Any,
        old: dict[str, Any],
        epoch: int,
    ) -> None:
        await machine_effect(backend.remove)
        previous = dict(old["metadata"])
        previous.update(
            active_code_volume=old["workspace_volume"],
            active_database_volume=old.get("database_volume"),
            services={},
            exec_logs={},
            exec_pids={},
            epoch=epoch,
            quiesce_state=None,
            environment_ref=None,
        )
        write_controller_json(backend.metadata_path, previous)
        backend.workspace_volume = old["workspace_volume"]
        manifest = MachineManifest.model_validate(old["machine"]["manifest"])
        machine, _ = manager.machine_runtime.parts(state)
        restored_machine = dict(old["machine"])
        restored_machine.update(epoch=epoch, ready_epoch=epoch, operations={}, cancelled_epoch=0)
        write_controller_json(machine.path, restored_machine)
        await machine_effect(backend.ensure, manifest, epoch)
        await self._start(backend, manifest, epoch)
        await machine_effect(
            manager.machine_runtime._start_boundary, state, manifest, backend, epoch
        )
        await self._complete_fence(manager, state, epoch, old["workspace_volume"])

    async def _cleanup_reverted_pair(
        self,
        backend: Any,
        request: CodeRestorationApply,
        prepared: dict[str, Any],
        intent: dict[str, Any],
        path: Path,
    ) -> None:
        if (
            intent.get("state") != "reverted"
            or intent.get("cleanup_pending") is not True
            or intent.get("database_strategy") != "replace_verified_empty"
        ):
            raise CellIdentityConflict("restoration cleanup state is invalid")
        if request.binding_digest is None or intent.get("binding_digest") != request.binding_digest:
            raise CellIdentityConflict("restoration cleanup binding changed")
        code_digest = prepared.get("code_digest")
        database_digest = prepared.get("database_digest")
        if not isinstance(code_digest, str) or not isinstance(database_digest, str):
            raise CellIdentityConflict("restoration cleanup artifacts are unavailable")
        await machine_effect(
            backend.cleanup_reverted_restoration,
            request.operation_id,
            binding_digest=request.binding_digest,
            code_artifact_digest=code_digest,
            database_artifact_digest=database_digest,
        )
        intent.pop("cleanup_pending", None)
        _write_activation_journal(path, intent)

    async def _record_reverted_pair(
        self,
        manager: Any,
        state: Any,
        backend: Any,
        request: CodeRestorationApply,
        prepared: dict[str, Any],
        intent: dict[str, Any],
        path: Path,
        old: dict[str, Any],
    ) -> None:
        intent["state"] = "reverted"
        if intent.get("database_strategy") != "replace_verified_empty":
            _write_activation_journal(path, intent)
            return
        if not await self._running_matches(
            manager,
            state,
            backend,
            old["workspace_volume"],
            request.fencing_epoch,
            old["machine"]["manifest"],
            old.get("database_volume"),
        ):
            raise CellResourceError("reverted restoration source pair is not confirmed")
        intent["cleanup_pending"] = True
        _write_activation_journal(path, intent)
        await self._cleanup_reverted_pair(backend, request, prepared, intent, path)

    async def observe(
        self, request: CodeRestorationApply, prepared: dict[str, Any]
    ) -> dict[str, Any] | None:
        manager = self._manager(request.workspace_id)
        async with manager.operation_lock.hold(request.workspace_id):
            return await self._observe_locked(manager, request, prepared)

    def activation_journal_exists(self, request: CodeRestorationApply) -> bool:
        path = self.root / str(request.operation_id) / "activation.json"
        return path.is_file() and not path.is_symlink() and not self.root.is_symlink()

    async def _observe_locked(
        self, manager: Any, request: CodeRestorationApply, prepared: dict[str, Any]
    ) -> dict[str, Any] | None:
        path = self._directory(request.operation_id) / "activation.json"
        if not path.exists():
            # The coordinator persists effect_started before calling apply. A
            # crash in that gap has no activation journal. Prove the untouched
            # source, then fence and recover it; do not leave admission blocked.
            await self._record_untouched_source(manager, request, prepared, path)
        intent = json.loads(path.read_text())
        if (
            intent.get("operation_id") != str(request.operation_id)
            or intent.get("workspace_id") != str(request.workspace_id)
            or intent.get("project_id") != str(request.project_id)
            or intent.get("owner_id") != str(request.owner_id)
            or intent.get("candidate_id") != str(request.candidate_id)
            or intent.get("source_commit_sha") != request.planned_commit_sha
            or intent.get("fencing_epoch") != request.fencing_epoch
            or intent.get("binding_digest") != request.binding_digest
        ):
            raise CellIdentityConflict("activation observation identity mismatch")
        if intent.get("state") in {"preflight", "rejected", "superseded"}:
            return self._confirm_preflight_rejection(
                manager,
                request,
                reason_type=str(intent.get("reason_type") or "InterruptedPreflight"),
            )
        state = manager.state_store.load(request.workspace_id)
        if (
            state is None
            or state.project_id != request.project_id
            or state.owner_id != request.owner_id
            or state.fencing_epoch not in {request.expected_fencing_epoch, request.fencing_epoch}
            or state.active_generation_run_id is not None
        ):
            raise CellIdentityConflict("activation owner, fence or activity changed")
        _, backend = manager.machine_runtime.parts(state)
        target_forward_only = intent.get("state") in {
            "target_writers_admitted",
            "target_recovery",
            "active",
        }
        if intent["state"] in {
            "switching",
            "target_writers_admitted",
            "target_recovery",
            "active",
        } and await self._running_matches(
            manager,
            state,
            backend,
            intent["volume"],
            request.fencing_epoch,
            prepared["manifest"],
            intent.get("database_volume"),
        ):
            # The external switch already happened. Observe it; never restart an
            # app which may already be accepting new business writes.
            intent["source_revision"] = await self._complete_activation(
                manager, state, backend, request, intent
            )
            intent["state"] = "active"
            _write_activation_journal(path, intent)
            self._discard_code(path.parent)
            return self._observed(intent)
        if target_forward_only:
            # Once target writers were admitted, target rows may exist. Recovery
            # may repair/restart only the bound target pair; old DB is forbidden.
            intent["state"] = "target_recovery"
            _write_activation_journal(path, intent)
            activation_prepared = {
                **prepared,
                "target_database_volume": intent["database_volume"],
            }
            if (
                backend._lookup(backend.client.volumes, intent["volume"], "project-volume") is None
                or backend._lookup(
                    backend.client.volumes,
                    intent["database_volume"],
                    "project-volume",
                )
                is None
            ):
                raise CellIdentityConflict("target restoration pair is incomplete")
            await machine_effect(backend.remove)

            def readmit_target_writers() -> None:
                intent["state"] = "target_writers_admitted"
                intent["effects_admitted"] = True
                _write_activation_journal(path, intent)

            await self._activate_code(
                manager,
                state,
                backend,
                activation_prepared,
                intent["volume"],
                request.fencing_epoch,
                before_writers=readmit_target_writers,
            )
            intent["source_revision"] = await self._complete_activation(
                manager, state, backend, request, intent
            )
            intent["state"] = "active"
            _write_activation_journal(path, intent)
            self._discard_code(path.parent)
            return self._observed(intent)
        old = intent["old"]
        if intent["state"] == "reverted" and await self._running_matches(
            manager,
            state,
            backend,
            old["workspace_volume"],
            request.fencing_epoch,
            old["machine"]["manifest"],
            old.get("database_volume"),
        ):
            if intent.get("cleanup_pending") is True:
                await self._cleanup_reverted_pair(backend, request, prepared, intent, path)
            self._discard_code(path.parent)
            return self._observed(intent, applied=False)
        intent["state"] = "reverting"
        _write_activation_journal(path, intent)
        await self._recover_old(manager, state, backend, old, request.fencing_epoch)
        await self._record_reverted_pair(
            manager,
            state,
            backend,
            request,
            prepared,
            intent,
            path,
            old,
        )
        self._discard_code(path.parent)
        return self._observed(intent, applied=False)

    @staticmethod
    def _discard_code(directory: Path) -> None:
        # Receipt + current mounted code suffice for terminal observation. The
        # disposable build archive must not accumulate once it can no longer apply.
        try:
            (directory / "code.tar").unlink(missing_ok=True)
            (directory / "database.tar").unlink(missing_ok=True)
        except OSError:
            # Cleanup cannot turn an observed successful activation into a
            # failed operation or roll back the user's already-running code.
            logging.getLogger(__name__).warning("restoration archive cleanup needs retry")

    @staticmethod
    def _discard_rejected_code(directory: Path) -> None:
        # A negative receipt is not terminal until its disposable archive has
        # gone. Raising keeps the coordinator in reconciliation so a transient
        # cleanup failure is retried after restart.
        (directory / "code.tar").unlink(missing_ok=True)
        (directory / "database.tar").unlink(missing_ok=True)

    async def _record_untouched_source(
        self, manager: Any, request: CodeRestorationApply, prepared: dict[str, Any], path: Path
    ) -> None:
        from yleum_orchestrator.routers.runtime import _workspace_revision
        from yleum_orchestrator.routers.workspace import _read_agent_workspace_files

        state = self._state(manager, request, epoch=request.expected_fencing_epoch)
        machine, backend = manager.machine_runtime.parts(state)
        volume = backend.stem + "-code-" + request.operation_id.hex
        database_volume = backend.stem + "-db-" + request.operation_id.hex
        metadata, saved = backend._metadata(), machine.state()
        current_database_volume = getattr(
            backend, "project_postgres_volume", backend.stem + "-app-postgres-data"
        )
        # Preparations recorded while databases were protected also carry a
        # policy epoch; that obsolete field is not part of the runtime identity.
        recorded = {
            key: value
            for key, value in (prepared.get("source_runtime") or {}).items()
            if key != "policy_epoch"
        }
        if (
            backend._lookup(backend.client.volumes, volume, "project-volume") is not None
            or backend._lookup(backend.client.volumes, database_volume, "project-volume")
            is not None
            or self._runtime_identity(backend, machine) != recorded
            or type(metadata.get("epoch")) is not int
            or metadata["epoch"] > request.expected_fencing_epoch
            or type(saved.get("epoch")) is not int
            or saved["epoch"] > request.expected_fencing_epoch
        ):
            raise CellIdentityConflict("missing activation intent has ambiguous effects")
        current = await _read_agent_workspace_files(manager, backend.workspace_volume)
        if _workspace_revision(current) != prepared["workspace_revision"]:
            raise CellIdentityConflict("unrecorded activation source changed")
        if prepared.get("binding") is not None:
            binding_payload = prepared["binding"]
            binding = (
                RestorationSourceBindingV3.model_validate(binding_payload)
                if binding_payload.get("version") == 3
                else RestorationSourceBindingV2.model_validate(binding_payload)
            )
            if request.binding_digest != binding.digest():
                raise CellIdentityConflict("restoration source binding digest changed")
            source_bytes = await manager.docker.read_workspace_source_files(
                backend.workspace_volume
            )
            if (
                isinstance(binding, RestorationSourceBindingV3)
                and binding.database_strategy == "replace_verified_empty"
            ):
                prepared_witness = EmptyDatabaseWitness.model_validate(prepared["empty_witness"])
                current_witness = await machine_effect(
                    observe_empty_database,
                    backend,
                    operation_id=request.operation_id,
                    workspace_id=request.workspace_id,
                    project_id=request.project_id,
                    database_identity_digest=prepared_witness.database_identity_digest,
                    observation_kind="source",
                )
                if current_witness is None or not same_empty_source(
                    prepared_witness, current_witness
                ):
                    raise CellIdentityConflict("unrecorded activation database changed")
                schema = {"empty_witness_catalog_digest": current_witness.catalog_digest}
            else:
                live, blockers = await machine_effect(catalog_contract, backend)
                if blockers or not contract_matches(live, prepared["live_contract"]):
                    raise CellIdentityConflict("unrecorded activation database changed")
                schema = live.model_dump(mode="json")
            observed = await machine_effect(
                observe_live_source,
                backend,
                machine,
                state,
                source_files=source_bytes,
                schema=schema,
            )
            if binding.model_copy(update=observed).live_identity_digest() != (
                binding.live_identity_digest()
            ):
                raise CellIdentityConflict("unrecorded activation source binding changed")
        _write_activation_journal(
            path,
            {
                "operation_id": str(request.operation_id),
                "workspace_id": str(request.workspace_id),
                "project_id": str(request.project_id),
                "owner_id": str(request.owner_id),
                "candidate_id": str(request.candidate_id),
                "source_commit_sha": request.planned_commit_sha,
                "fencing_epoch": request.fencing_epoch,
                "binding_digest": request.binding_digest,
                "volume": volume,
                "state": "reverting",
                "old": {
                    "metadata": metadata,
                    "machine": saved,
                    "workspace_volume": backend.workspace_volume,
                    "database_volume": current_database_volume,
                    "contract": prepared["live_contract"],
                },
            },
        )

    @staticmethod
    def _runtime_identity(backend: Any, machine: Any) -> dict[str, Any]:
        return {
            "workspace_volume": backend.workspace_volume,
            "metadata_epoch": backend._metadata().get("epoch"),
            "machine_epoch": machine.state().get("epoch"),
            "containers": [
                {"id": item.id, "epoch": item.labels.get("omnia.fencing_epoch")}
                if item is not None
                else None
                for item in (backend._container(), backend._project_postgres())
            ],
        }

    async def _running_matches(
        self,
        manager: Any,
        state: Any,
        backend: Any,
        volume: str,
        epoch: int,
        raw_manifest: dict[str, Any],
        database_volume: str | None = None,
    ) -> bool:
        manifest = MachineManifest.model_validate(raw_manifest)
        if backend.workspace_volume != volume:
            return False
        expected_database = database_volume or backend.project_postgres_volume
        if backend.project_postgres_volume != expected_database:
            return False
        for container in (backend._container(), backend._project_postgres()):
            if container is None or container.labels.get("omnia.fencing_epoch") != str(epoch):
                return False
            await machine_effect(container.reload)
            if container.status != "running":
                return False
        mounts = backend._container().attrs.get("Mounts", [])
        if not any(
            item.get("Name") == volume and item.get("Destination") == "/workspace"
            for item in mounts
        ):
            return False
        postgres_mounts = backend._project_postgres().attrs.get("Mounts", [])
        if not any(
            item.get("Name") == expected_database
            and item.get("Destination") == "/var/lib/postgresql/data"
            for item in postgres_mounts
        ):
            return False
        for service in manifest.services:
            result = await machine_effect(
                backend.service_status, service, epoch, include_logs=False
            )
            if not result["ready"]:
                return False
        preview = await machine_effect(manager.machine_runtime.preview, state)
        return preview is not None and preview[0] == "running"

    async def _complete_activation(
        self,
        manager: Any,
        state: Any,
        backend: Any,
        request: CodeRestorationApply,
        intent: dict[str, Any],
    ) -> str:
        from yleum_orchestrator.routers.runtime import _workspace_revision
        from yleum_orchestrator.routers.workspace import _read_agent_workspace_files

        revision = _workspace_revision(
            await _read_agent_workspace_files(manager, backend.workspace_volume)
        )
        if intent.get("source_revision") is not None and intent["source_revision"] != revision:
            raise CellIdentityConflict("activated source changed before confirmation")
        metadata = backend._metadata()
        metadata["restoration_proof"] = {
            key: intent[key]
            for key in (
                "operation_id",
                "workspace_id",
                "project_id",
                "owner_id",
                "candidate_id",
                "source_commit_sha",
                "fencing_epoch",
            )
        }
        metadata["restoration_proof"]["source_revision"] = revision
        for key in (
            "binding_digest",
            "database_strategy",
            "database_volume",
            "final_witness_digest",
            "pair_plan_digest",
            "pair_seal",
        ):
            if intent.get(key) is not None:
                metadata["restoration_proof"][key] = intent[key]
        write_controller_json(backend.metadata_path, metadata)
        await self._complete_fence(manager, state, request.fencing_epoch, intent["volume"])
        return revision

    @staticmethod
    def _observed(intent: dict[str, Any], *, applied: bool = True) -> dict[str, Any]:
        result = {
            "candidate_id": intent["candidate_id"],
            "source_commit_sha": intent["source_commit_sha"],
            "fencing_epoch": intent["fencing_epoch"],
            "applied": applied,
        }
        if intent.get("binding_digest") is not None:
            result["binding_digest"] = intent["binding_digest"]
        if applied:
            result["source_revision"] = intent["source_revision"]
        else:
            result["safe_to_release"] = True
            if intent.get("state") == "rejected" and intent.get("effects_admitted") is False:
                retained_epoch = intent.get("retained_source_fencing_epoch")
                if (
                    type(retained_epoch) is not int
                    or retained_epoch < 1
                    or retained_epoch >= intent["fencing_epoch"]
                ):
                    raise CellIdentityConflict("restoration rejection epoch is invalid")
                result["rejected_before_effect"] = True
                result["retained_source_fencing_epoch"] = retained_epoch
            elif intent.get("state") == "superseded" and intent.get("effects_admitted") is False:
                if intent.get("retained_source_fencing_epoch") is not None:
                    raise CellIdentityConflict("superseded restoration asserted a serving epoch")
                result["superseded_before_effect"] = True
        return result

    @staticmethod
    def _file_digest(path: Path) -> str:
        with path.open("rb") as handle:
            return hashlib.file_digest(handle, "sha256").hexdigest()
