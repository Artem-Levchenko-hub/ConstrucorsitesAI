"""Isolated code preparation and code-volume activation. No live data import exists here."""

from __future__ import annotations

import fnmatch
import hashlib
import io
import json
import logging
import re
import shutil
import socket
import tarfile
import traceback
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any, Literal, cast
from uuid import UUID, uuid5

from omnia_orchestrator.core.cell_resources import (
    CellIdentityConflict,
    CellResourceError,
    LifecycleMutation,
)
from omnia_orchestrator.core.project_machine import MachineManifest
from omnia_orchestrator.core.workspace_provider import WorkspaceSpec
from omnia_orchestrator.schemas.code_restoration import (
    CodeRestorationApply,
    CodeRestorationCancel,
    CodeRestorationPrepare,
    RestorationDatabaseState,
    RestorationSourceBindingV2,
)
from omnia_orchestrator.services.cell_admission import CellAdmissionGate
from omnia_orchestrator.services.cell_state import legacy_release_serving_epoch
from omnia_orchestrator.services.project_machine import (
    machine_budget,
    machine_effect,
    write_controller_json,
)
from omnia_orchestrator.services.restoration_binding import (
    exact_inventory_partition_digests,
    observe_live_source,
    serving_fencing_epoch,
)
from omnia_orchestrator.services.restoration_catalog import (
    candidate_contract,
    catalog_contract,
    describe_live_catalog,
)
from omnia_orchestrator.services.restoration_data_contract import DataContract, assess_contract
from omnia_orchestrator.services.restoration_database import (
    admin_args,
    admin_sql,
    read_controller_output,
)
from omnia_orchestrator.services.restoration_execution import (
    RestorationExecutionCancelled,
    RestorationExecutionJournal,
    dedicated_docker_api_factory,
)
from omnia_orchestrator.services.versioning.compatibility import (
    capability_diff,
    checks_from_diagnostics,
    checks_from_unsupported,
    delete_warnings,
    describe_capability,
)
from omnia_orchestrator.services.versioning.contracts import (
    CapabilityDiff,
    CompatibilityCheck,
    InventoryReport,
)
from omnia_orchestrator.services.versioning.inventory import observe_inventory


class PreparationNeedsChanges(ValueError):
    pass


def verify_source_inventory(actual: dict[str, bytes], expected: list[dict[str, Any]]) -> None:
    """Unknown persistent files must be classified before replacing a code volume."""
    from omnia_orchestrator.services.docker_py_cell_backend import (
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
    resolutions = list(dict.fromkeys(
        check.resolution for check in checks or []
        if check.severity == "blocking" and check.resolution
    ))
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
        kept = [check for check in checks or [] if check.severity != "info"][:800]
        kept += [check for check in checks or [] if check.severity == "info"][:100]
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


class CodeRestorationEngine:
    def __init__(self, settings: Any = None, *, manager_factory: Any = None) -> None:
        if settings is None:
            from omnia_orchestrator.core.config import get_settings

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
        from omnia_orchestrator.routers.workspace import (
            _require_docker_resource_manager,
            _workspace_provider,
        )

        return _require_docker_resource_manager(_workspace_provider(workspace_id))

    def _directory(self, operation_id: UUID) -> Path:
        path = self.root / str(operation_id)
        if path.is_symlink() or self.root.is_symlink():
            raise CellIdentityConflict("unsafe restoration artifacts")
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
        return path

    @staticmethod
    def _state(manager: Any, request: Any, *, epoch: int) -> Any:
        from omnia_orchestrator.services.cell_deletion import require_workspace_not_deleted

        require_workspace_not_deleted(manager.profile.state_path, request.workspace_id)
        state = manager.state_store.load(request.workspace_id)
        if (
            state is None
            or state.project_id != request.project_id
            or state.owner_id != request.owner_id
            or state.fencing_epoch != epoch
            or state.active_generation_run_id is not None
        ):
            raise CellIdentityConflict("restoration source identity, fence or activity changed")
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
        with machine_budget(870):
            async with manager.operation_lock.hold(request.workspace_id):
                try:
                    state = self._state(manager, request, epoch=request.fencing_epoch)
                    adapter = manager.machine_runtime
                    machine, source = adapter.parts(state)
                    if not await machine_effect(source.is_running):
                        raise PreparationNeedsChanges(
                            "Откройте текущую версию и повторите подготовку восстановления."
                        )
                except PreparationNeedsChanges as error:
                    # A sleeping/unprovisioned draft is an actionable condition for
                    # the owner, not a controller failure.
                    return {
                        "state": "needs_changes",
                        "candidate_id": None,
                        "report": preparation_report(blockers=[str(error)]),
                    }
                from omnia_orchestrator.routers.runtime import _workspace_revision
                from omnia_orchestrator.routers.workspace import _read_agent_workspace_files

                current_files = await _read_agent_workspace_files(manager, source.workspace_volume)
                current_revision = _workspace_revision(current_files)
                try:
                    source_bytes = await manager.docker.read_workspace_source_files(
                        source.workspace_volume
                    )
                    verify_source_inventory(
                        source_bytes, [item.model_dump() for item in request.current_files]
                    )
                    validate_supported_runtime(current_files)
                except (PreparationNeedsChanges, ValueError) as error:
                    return {
                        "state": "needs_changes",
                        "candidate_id": None,
                        "report": preparation_report(blockers=[str(error)]),
                    }
                source_runtime = self._runtime_identity(source, machine)
                # Evidence is gathered independently: row counts first (read-only),
                # so a schema-analysis gap can never hide that the data exist.
                inventory = await machine_effect(observe_database, source, observed_on="source")
                capabilities = capability_diff(current_files, files)
                current_contract, catalog_blockers, unsupported = await machine_effect(
                    describe_live_catalog, source
                )
                inventory = inventory.model_copy(
                    update={"schema_analysis": "partial" if unsupported else "complete"}
                )
                checks = checks_from_unsupported(unsupported)
                blockers = blocking_explanations(checks) if catalog_blockers else []
                current_manifest = MachineManifest.model_validate(machine.state()["manifest"])
                if current_manifest.data_stores or any(
                    service.mounts for service in current_manifest.services
                ):
                    blockers.append("Нужна проверка текущих файловых хранилищ.")
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
                live_before = None
                if request.binding_contract_version == 2:
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
                # live managed database, credentials and queues are never attached.
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
                if live_before is not None:
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
            observed_database_state: RestorationDatabaseState = "unknown"
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
                old_contract = await machine_effect(candidate_contract, candidate, files)
                # Template MAX tables are served by the CURRENT trusted core, not
                # by this project's dedicated database. No app role gains access.
                managed = {
                    "max_users",
                    "max_webhook_events",
                    "max_catalog_items",
                    "max_business_actions",
                    "max_consents",
                    "max_analytics_events",
                    "max_bot_outbox",
                    "max_audit_log",
                }
                old_contract = DataContract(
                    version=1,
                    tables=[table for table in old_contract.tables if table.name not in managed],
                )
                assessment = assess_contract(old_contract, current_contract)
                checks = [
                    *checks_from_diagnostics(assessment.diagnostics),
                    *delete_warnings(assessment.blocked_deletes),
                ]
                # The copy lives only in this candidate's own isolated database.
                await machine_effect(admin_sql, candidate, dump.decode(), max_bytes=4 * 1024 * 1024)
                copied, copied_blockers = await machine_effect(catalog_contract, candidate)
                if copied_blockers or copied != current_contract:
                    raise PreparationNeedsChanges(
                        "Структура копии данных не совпала с проверенной базой."
                    )
                copied_inventory = await machine_effect(
                    observe_database, candidate, observed_on="candidate_copy"
                )
                observed_database_state = inventory.presence
                binding = None
                if request.binding_contract_version == 2:
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
                        min(task.timeout_seconds, 420),
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
                    archive = directory / "code.tar"
                    await machine_effect(candidate.stop)
                    digest = await machine_effect(
                        self._capture_code,
                        candidate,
                        archive,
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
        from omnia_orchestrator.services.cell_publication_capacity import production_manager

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
        from omnia_orchestrator.services.project_machine import machine_remaining_seconds

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
            connection.close()
        result = backend.client.api.exec_inspect(execution["Id"])
        if result.get("Running") or result.get("ExitCode") != 0:
            raise PreparationNeedsChanges(
                "Не удалось подготовить изолированную копию данных в пределах лимита."
            )
        return output

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
    ) -> None:
        result = container.exec_run(
            ["timeout", str(timeout), *argv], workdir="/workspace/" + cwd
        )
        if result.exit_code != 0:
            log = Path(backend.root) / str(backend.workspace_id) / "restoration-check.log"
            log.write_bytes(result.output[-24000:])
            log.chmod(0o600)
            raise PreparationNeedsChanges(
                "Проверка исторического кода не прошла; нужна совместимая правка."
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
            )
        except PreparationNeedsChanges:
            journal.finish_attempt(request.operation_id, receipt["attempt_id"])
            raise
        except BaseException:
            journal.finish_attempt(
                request.operation_id,
                receipt["attempt_id"],
                state="unknown",
            )
            raise
        else:
            journal.finish_attempt(request.operation_id, receipt["attempt_id"])

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
            connection.close()
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
        from omnia_orchestrator.services.cell_publication_capacity import production_manager

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
                raise CellResourceError(
                    "restoration cancellation Docker client unavailable"
                )
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
    def _validate_preflight_receipt(
        intent: dict[str, Any], request: CodeRestorationApply
    ) -> None:
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

    def _capture_preflight_serving_epoch(
        self, manager: Any, request: CodeRestorationApply
    ) -> None:
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
                    raise CellIdentityConflict(
                        "restoration retained serving epoch is unavailable"
                    )
                if recorded is None or not recorded.matches_replay_envelope(
                    kind="restoration_rejection",
                    request_digest=mutation.request_digest,
                    fencing_epoch=mutation.fencing_epoch,
                    checkpoint_ref=None,
                ):
                    raise CellIdentityConflict(
                        "restoration rejection operation envelope changed"
                    )
                if recorded.status != "completed":
                    retained_bundle_state = intent.get("retained_bundle_state")
                    if (
                        not isinstance(retained_bundle_state, str)
                        or not retained_bundle_state
                    ):
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
                    or recorded.detail
                    != "retained_source_fencing_epoch=" + str(retained_epoch)
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
            from omnia_orchestrator.routers.runtime import _workspace_revision
            from omnia_orchestrator.routers.workspace import _read_agent_workspace_files

            volume = backend.stem + "-code-" + request.operation_id.hex
            old = {
                "metadata": backend._metadata(),
                "machine": machine.state(),
                "workspace_volume": backend.workspace_volume,
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
                "effects_admitted": True,
                "old": old,
                "volume": volume,
                "state": "intent",
            }
            write_controller_json(directory / "activation.json", intent)
            try:
                current = await _read_agent_workspace_files(manager, backend.workspace_volume)
                if _workspace_revision(current) != prepared["workspace_revision"]:
                    raise CellIdentityConflict("restoration source changed after checking")
                verify_source_inventory(
                    await manager.docker.read_workspace_source_files(backend.workspace_volume),
                    prepared["current_files"],
                )
                live, blockers = await machine_effect(catalog_contract, backend)
                if blockers or not contract_matches(live, prepared["live_contract"]):
                    raise CellIdentityConflict("restoration data contract changed after checking")
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
                await manager._ensure_volume(
                    volume,
                    {
                        **manager._state_labels(state, "project-volume"),
                        **target.labels("project-volume"),
                    },
                )
                await machine_effect(target.import_volume, volume, archive)
                intent["state"] = "switching"
                write_controller_json(directory / "activation.json", intent)
                # Writers stop only after all checks and code import have succeeded.
                await machine_effect(backend.remove)
                await self._activate_code(
                    manager, state, backend, prepared, volume, request.fencing_epoch
                )
                intent["source_revision"] = await self._complete_activation(
                    manager, state, backend, request, intent
                )
            except Exception:
                error_path = directory / "activation-error.log"
                error_path.write_text(traceback.format_exc(), encoding="utf-8")
                error_path.chmod(0o600)
                intent["state"] = "reverting"
                write_controller_json(directory / "activation.json", intent)
                await self._recover_old(manager, state, backend, old, request.fencing_epoch)
                intent["state"] = "reverted"
                write_controller_json(directory / "activation.json", intent)
                self._discard_code(directory)
                return self._observed(intent, applied=False)
            intent["state"] = "active"
            write_controller_json(directory / "activation.json", intent)
            self._discard_code(directory)
            return self._observed(intent)

    async def _activation_preflight(
        self, manager: Any, request: CodeRestorationApply, prepared: dict[str, Any]
    ) -> tuple[Any, Any, Any]:
        from omnia_orchestrator.routers.runtime import _workspace_revision
        from omnia_orchestrator.routers.workspace import _read_agent_workspace_files

        state = self._state(manager, request, epoch=request.expected_fencing_epoch)
        machine, backend = manager.machine_runtime.parts(state)
        if prepared.get("binding") is None:
            if request.binding_digest is not None:
                raise CellIdentityConflict("restoration source binding is unavailable")
            return state, machine, backend
        binding = RestorationSourceBindingV2.model_validate(prepared["binding"])
        if request.binding_digest != binding.digest():
            raise CellIdentityConflict("restoration source binding digest changed")
        current = await _read_agent_workspace_files(manager, backend.workspace_volume)
        if _workspace_revision(current) != prepared["workspace_revision"]:
            raise CellIdentityConflict("restoration source changed after checking")
        source_bytes = await manager.docker.read_workspace_source_files(backend.workspace_volume)
        verify_source_inventory(source_bytes, prepared["current_files"])
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
        return state, machine, backend

    async def _activate_code(
        self,
        manager: Any,
        state: Any,
        backend: Any,
        prepared: dict[str, Any],
        volume: str,
        epoch: int,
    ) -> None:
        adapter = manager.machine_runtime
        manifest = MachineManifest.model_validate(prepared["manifest"])
        metadata = backend._metadata()
        metadata.update(
            active_code_volume=volume,
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
        await machine_effect(backend.ensure, manifest, epoch)
        await self._start(backend, manifest, epoch)
        await machine_effect(adapter._start_boundary, state, manifest, backend, epoch)
        await self._complete_fence(manager, state, epoch, volume)

    @staticmethod
    async def _complete_fence(manager: Any, state: Any, epoch: int, volume: str) -> None:
        mutation = LifecycleMutation(
            uuid5(state.workspace_id, "code-activation-" + str(epoch)),
            epoch,
            hashlib.sha256(
                (str(state.workspace_id) + ":restore:" + str(epoch)).encode()
            ).hexdigest(),
        )
        capacity_lock = manager.capacity_lock or manager.operation_lock
        async with capacity_lock.hold_named("host-capacity-admission"):
            manager._capacity_reservation_store().rebind(state.workspace_id, mutation)
        spec = WorkspaceSpec(
            workspace_id=state.workspace_id,
            project_id=state.project_id,
            owner_id=state.owner_id,
            profile_version=state.profile_version,
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
        if intent["state"] in {"switching", "active"} and await self._running_matches(
            manager,
            state,
            backend,
            intent["volume"],
            request.fencing_epoch,
            prepared["manifest"],
        ):
            # The external switch already happened. Observe it; never restart an
            # app which may already be accepting new business writes.
            intent["source_revision"] = await self._complete_activation(
                manager, state, backend, request, intent
            )
            intent["state"] = "active"
            write_controller_json(path, intent)
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
        ):
            self._discard_code(path.parent)
            return self._observed(intent, applied=False)
        intent["state"] = "reverting"
        write_controller_json(path, intent)
        await self._recover_old(manager, state, backend, old, request.fencing_epoch)
        intent["state"] = "reverted"
        write_controller_json(path, intent)
        self._discard_code(path.parent)
        return self._observed(intent, applied=False)

    @staticmethod
    def _discard_code(directory: Path) -> None:
        # Receipt + current mounted code suffice for terminal observation. The
        # disposable build archive must not accumulate once it can no longer apply.
        try:
            (directory / "code.tar").unlink(missing_ok=True)
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

    async def _record_untouched_source(
        self, manager: Any, request: CodeRestorationApply, prepared: dict[str, Any], path: Path
    ) -> None:
        from omnia_orchestrator.routers.runtime import _workspace_revision
        from omnia_orchestrator.routers.workspace import _read_agent_workspace_files

        state = self._state(manager, request, epoch=request.expected_fencing_epoch)
        machine, backend = manager.machine_runtime.parts(state)
        volume = backend.stem + "-code-" + request.operation_id.hex
        metadata, saved = backend._metadata(), machine.state()
        # Preparations recorded while databases were protected also carry a
        # policy epoch; that obsolete field is not part of the runtime identity.
        recorded = {
            key: value
            for key, value in (prepared.get("source_runtime") or {}).items()
            if key != "policy_epoch"
        }
        if (
            backend._lookup(backend.client.volumes, volume, "project-volume") is not None
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
            binding = RestorationSourceBindingV2.model_validate(prepared["binding"])
            if request.binding_digest != binding.digest():
                raise CellIdentityConflict("restoration source binding digest changed")
            source_bytes = await manager.docker.read_workspace_source_files(
                backend.workspace_volume
            )
            live, blockers = await machine_effect(catalog_contract, backend)
            if blockers or not contract_matches(live, prepared["live_contract"]):
                raise CellIdentityConflict("unrecorded activation database changed")
            observed = await machine_effect(
                observe_live_source,
                backend,
                machine,
                state,
                source_files=source_bytes,
                schema=live.model_dump(mode="json"),
            )
            if binding.model_copy(update=observed).live_identity_digest() != (
                binding.live_identity_digest()
            ):
                raise CellIdentityConflict("unrecorded activation source binding changed")
        write_controller_json(
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
    ) -> bool:
        manifest = MachineManifest.model_validate(raw_manifest)
        if backend.workspace_volume != volume:
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
        from omnia_orchestrator.routers.runtime import _workspace_revision
        from omnia_orchestrator.routers.workspace import _read_agent_workspace_files

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
            elif (
                intent.get("state") == "superseded"
                and intent.get("effects_admitted") is False
            ):
                if intent.get("retained_source_fencing_epoch") is not None:
                    raise CellIdentityConflict("superseded restoration asserted a serving epoch")
                result["superseded_before_effect"] = True
        return result

    @staticmethod
    def _file_digest(path: Path) -> str:
        with path.open("rb") as handle:
            return hashlib.file_digest(handle, "sha256").hexdigest()
