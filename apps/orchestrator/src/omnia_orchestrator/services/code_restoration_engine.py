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
from dataclasses import replace
from pathlib import Path
from typing import Any, cast
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
)
from omnia_orchestrator.services.project_machine import (
    machine_budget,
    machine_effect,
    write_controller_json,
)
from omnia_orchestrator.services.restoration_catalog import (
    candidate_contract,
    catalog_contract,
    database_state,
)
from omnia_orchestrator.services.restoration_data_contract import DataContract, assess_contract
from omnia_orchestrator.services.restoration_database import (
    admin_args,
    admin_sql,
    install_policy,
    load_policy,
    read_controller_output,
    recover_policy,
    stage_policy,
)


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


def trusted_policy_contract(backend: Any) -> DataContract | None:
    policy = load_policy(backend)
    return DataContract.model_validate(policy["contract"]) if policy is not None else None


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


def preparation_report(
    *,
    blockers: list[str] | None = None,
    retained: list[str] | None = None,
    blocked_deletes: list[str] | None = None,
    observed_database_state: RestorationDatabaseState = "unknown",
) -> dict[str, Any]:
    blocked = blockers or []
    return {
        "revision": 1,
        "mode": "adapted" if blocked else "exact",
        "database_state": observed_database_state,
        "changes": [
            "Код выбранной версии собирается с её сохранёнными зависимостями.",
            "Доступ к данным выполняется через текущую проверку пользователя и ограниченные права.",
            *([] if blocked else ["Исторический код подготовлен без запуска AI-агента."]),
        ],
        "retained_data": [
            "Текущая база и действующая публикация не заменяются.",
            *["Поле сохраняется в базе: " + name for name in retained or []],
        ],
        "unavailable_features": [
            "Удаление требует адаптации: " + name for name in blocked_deletes or []
        ],
        "warnings": [
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


class CodeRestorationEngine:
    def __init__(self, settings: Any = None, *, manager_factory: Any = None) -> None:
        if settings is None:
            from omnia_orchestrator.core.config import get_settings

            settings = get_settings()
        self.settings = settings
        self.root = Path(settings.cell_state_path).parent / "code-restoration-artifacts"
        self.manager_factory = manager_factory

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

    async def prepare(self, request: CodeRestorationPrepare) -> dict[str, Any]:
        files = source_text(request)
        try:
            manifest = validate_supported_runtime(files)
        except (PreparationNeedsChanges, ValueError, KeyError) as error:
            return {
                "state": "needs_changes",
                "candidate_id": None,
                "report": preparation_report(blockers=[str(error)]),
            }
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
                state = self._state(manager, request, epoch=request.fencing_epoch)
                adapter = manager.machine_runtime
                machine, source = adapter.parts(state)
                if not await machine_effect(source.is_running):
                    raise PreparationNeedsChanges(
                        "Откройте текущую версию и повторите подготовку восстановления."
                    )
                from omnia_orchestrator.routers.runtime import _workspace_revision
                from omnia_orchestrator.routers.workspace import _read_agent_workspace_files

                current_files = await _read_agent_workspace_files(manager, source.workspace_volume)
                current_revision = _workspace_revision(current_files)
                try:
                    verify_source_inventory(
                        await manager.docker.read_workspace_source_files(source.workspace_volume),
                        [item.model_dump() for item in request.current_files],
                    )
                    validate_supported_runtime(current_files)
                except (PreparationNeedsChanges, ValueError) as error:
                    return {
                        "state": "needs_changes",
                        "candidate_id": None,
                        "report": preparation_report(blockers=[str(error)]),
                    }
                source_policy_contract = trusted_policy_contract(source)
                source_runtime = self._runtime_identity(source, machine)
                current_contract, blockers = await machine_effect(
                    catalog_contract,
                    source,
                    trusted_contract=source_policy_contract,
                )
                current_manifest = MachineManifest.model_validate(machine.state()["manifest"])
                if current_manifest.data_stores or any(
                    service.mounts for service in current_manifest.services
                ):
                    blockers.append("Нужна проверка текущих файловых хранилищ.")
                if blockers:
                    return {
                        "state": "needs_changes",
                        "candidate_id": None,
                        "report": preparation_report(blockers=blockers),
                    }
                # Only the dedicated database is copied. The trusted MAX core,
                # live managed database, credentials and queues are never attached.
                dump = await machine_effect(self._dump, source)
            observed_database_state: RestorationDatabaseState = "unknown"
            try:
                candidate = await self._candidate(manager, request, candidate_id, manifest)
                await machine_effect(self._seed_source, candidate, request)
                await machine_effect(
                    self._command,
                    candidate,
                    ["pnpm", "install", "--frozen-lockfile", "--ignore-scripts"],
                    360,
                )
                await machine_effect(
                    self._command,
                    candidate,
                    [
                        "node",
                        "-e",
                        "const pg=require('pg'); "
                        "if(typeof pg.Client.prototype.getTransactionStatus!=="
                        "'function') process.exit(1);",
                    ],
                    15,
                )
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
                # pg_dump includes policy references, but deliberately exports no
                # roles. Create only the known inert role; credentials are issued
                # for this candidate later, never copied from the live database.
                await machine_effect(
                    admin_sql,
                    candidate,
                    "DO $$ BEGIN IF NOT EXISTS "
                    "(SELECT FROM pg_roles WHERE rolname='omnia_runtime') "
                    "THEN CREATE ROLE omnia_runtime NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE "
                    "NOREPLICATION NOBYPASSRLS NOINHERIT; END IF; END $$;",
                )
                await machine_effect(admin_sql, candidate, dump.decode(), max_bytes=4 * 1024 * 1024)
                copied, copied_blockers = await machine_effect(
                    catalog_contract,
                    candidate,
                    trusted_contract=source_policy_contract,
                )
                if copied_blockers or copied != current_contract:
                    raise PreparationNeedsChanges(
                        "Структура копии данных не совпала с проверенной базой."
                    )
                observed_database_state = await machine_effect(database_state, candidate)
                # Empty rows do not make incompatible schema safe for future writes.
                if assessment.blockers:
                    raise PreparationNeedsChanges(
                        "Несовместимая структура данных: " + ", ".join(assessment.blockers)
                    )
                await machine_effect(candidate.remove)
                await machine_effect(
                    stage_policy,
                    candidate,
                    old_contract,
                    2,
                    blocked_deletes=assessment.blocked_deletes,
                )
                await machine_effect(candidate.ensure, manifest, 2)
                await machine_effect(install_policy, candidate)
                await machine_effect(self._disable_egress, candidate)
                tasks = [task for task in manifest.tasks if task.role == "full_build"]
                if not tasks:
                    tasks = [task for task in manifest.tasks if task.role == "build"]
                for task in tasks:
                    await machine_effect(
                        self._command,
                        candidate,
                        task.argv,
                        min(task.timeout_seconds, 420),
                        task.cwd,
                    )
                await self._start(candidate, manifest, 2)
                await machine_effect(self._verify_source, candidate, request)
                verify_source_inventory(
                    await manager.docker.read_workspace_source_files(candidate.workspace_volume),
                    [
                        {"path": item.path, "sha256": hashlib.sha256(item.decoded()).hexdigest()}
                        for item in request.files
                    ],
                )
                archive = directory / "code.tar"
                await machine_effect(candidate.stop)
                digest = await machine_effect(
                    self._capture_code,
                    candidate,
                    archive,
                    reserve_bytes=self.settings.cell_required_free_disk_bytes,
                )
                prepared = {
                    "state": "ready",
                    "candidate_id": str(candidate_id),
                    "report": preparation_report(
                        retained=assessment.retained_columns,
                        blocked_deletes=assessment.blocked_deletes,
                        observed_database_state=observed_database_state,
                    ),
                    "request_digest": request.digest(),
                    "workspace_revision": current_revision,
                    "source_runtime": source_runtime,
                    "current_files": [
                        item.model_dump(mode="json") for item in request.current_files
                    ],
                    "live_contract": current_contract.model_dump(mode="json"),
                    "contract": old_contract.model_dump(mode="json"),
                    "blocked_deletes": assessment.blocked_deletes,
                    "manifest": manifest.model_dump(mode="json"),
                    "code_digest": digest,
                    "base_image": candidate.base_image,
                }
                write_controller_json(prepared_path, prepared)
                return prepared
            except (PreparationNeedsChanges, ValueError) as error:
                return {
                    "state": "needs_changes",
                    "candidate_id": None,
                    "report": preparation_report(
                        blockers=[str(error)], observed_database_state=observed_database_state
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
        spec = WorkspaceSpec(
            workspace_id=candidate_id,
            project_id=request.project_id,
            owner_id=request.owner_id,
            profile_version=candidate_manager.profile.profile_version,
        )
        mutation = LifecycleMutation(uuid5(request.operation_id, "reserve"), 1, request.digest())
        # Real host accounting; a check cannot steal a running app's reservation.
        await candidate_manager.ensure(spec, mutation)
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
                "--exclude-table=omnia_guard.identity",
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
    def _command(backend: Any, argv: list[str], timeout: int, cwd: str = ".") -> None:
        result = backend._container().exec_run(
            ["timeout", str(timeout), *argv], workdir="/workspace/" + cwd
        )
        if result.exit_code != 0:
            log = Path(backend.root) / str(backend.workspace_id) / "restoration-check.log"
            log.write_bytes(result.output[-24000:])
            log.chmod(0o600)
            raise PreparationNeedsChanges(
                "Проверка исторического кода не прошла; нужна совместимая правка."
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
        await self._cleanup_candidate(
            self._manager(request.workspace_id), request, uuid5(request.operation_id, "candidate")
        )
        self._discard_code(directory)

    async def apply(
        self, request: CodeRestorationApply, prepared: dict[str, Any]
    ) -> dict[str, Any]:
        manager = self._manager(request.workspace_id)
        directory = self._directory(request.operation_id)
        async with manager.operation_lock.hold(request.workspace_id):
            if (directory / "activation.json").exists():
                result = await self._observe_locked(manager, request, prepared)
                if result is None:
                    raise CellResourceError("activation outcome is not yet confirmed")
                return result
            state = self._state(manager, request, epoch=request.expected_fencing_epoch)
            adapter = manager.machine_runtime
            machine, backend = adapter.parts(state)
            from omnia_orchestrator.routers.runtime import _workspace_revision
            from omnia_orchestrator.routers.workspace import _read_agent_workspace_files

            volume = backend.stem + "-code-" + request.operation_id.hex
            old = {
                "metadata": backend._metadata(),
                "machine": machine.state(),
                "workspace_volume": backend.workspace_volume,
                "policy": load_policy(backend),
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
                live, blockers = await machine_effect(
                    catalog_contract,
                    backend,
                    trusted_contract=trusted_policy_contract(backend),
                )
                if blockers or live.model_dump(mode="json") != prepared["live_contract"]:
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
                await self._recover_old(
                    manager, state, backend, old, request.fencing_epoch, str(request.operation_id)
                )
                intent["state"] = "reverted"
                write_controller_json(directory / "activation.json", intent)
                self._discard_code(directory)
                return self._observed(intent, applied=False)
            intent["state"] = "active"
            write_controller_json(directory / "activation.json", intent)
            self._discard_code(directory)
            return self._observed(intent)

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
        await machine_effect(
            stage_policy,
            backend,
            DataContract.model_validate(prepared["contract"]),
            epoch,
            blocked_deletes=prepared["blocked_deletes"],
        )
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
        await machine_effect(backend.ensure, manifest, epoch)
        await machine_effect(install_policy, backend)
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
        operation_id: str,
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
        original_policy = old.get("policy")
        if load_policy(backend) is not None:
            contract = original_policy["contract"] if original_policy else old["contract"]
            blocked = original_policy["blocked_deletes"] if original_policy else []
            await machine_effect(
                recover_policy,
                backend,
                DataContract.model_validate(contract),
                epoch,
                blocked_deletes=blocked,
                operation_id=operation_id,
            )
        backend.workspace_volume = old["workspace_volume"]
        manifest = MachineManifest.model_validate(old["machine"]["manifest"])
        machine, _ = manager.machine_runtime.parts(state)
        restored_machine = dict(old["machine"])
        restored_machine.update(epoch=epoch, ready_epoch=epoch, operations={}, cancelled_epoch=0)
        write_controller_json(machine.path, restored_machine)
        await machine_effect(backend.ensure, manifest, epoch)
        if load_policy(backend) is not None:
            await machine_effect(install_policy, backend)
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
        ):
            raise CellIdentityConflict("activation observation identity mismatch")
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
        await self._recover_old(
            manager, state, backend, old, request.fencing_epoch, str(request.operation_id)
        )
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

    async def _record_untouched_source(
        self, manager: Any, request: CodeRestorationApply, prepared: dict[str, Any], path: Path
    ) -> None:
        from omnia_orchestrator.routers.runtime import _workspace_revision
        from omnia_orchestrator.routers.workspace import _read_agent_workspace_files

        state = self._state(manager, request, epoch=request.expected_fencing_epoch)
        machine, backend = manager.machine_runtime.parts(state)
        volume = backend.stem + "-code-" + request.operation_id.hex
        metadata, saved, policy = backend._metadata(), machine.state(), load_policy(backend)
        if (
            backend._lookup(backend.client.volumes, volume, "project-volume") is not None
            or self._runtime_identity(backend, machine) != prepared.get("source_runtime")
            or type(metadata.get("epoch")) is not int
            or metadata["epoch"] > request.expected_fencing_epoch
            or type(saved.get("epoch")) is not int
            or saved["epoch"] > request.expected_fencing_epoch
            or (policy is not None and policy["epoch"] > request.expected_fencing_epoch)
        ):
            raise CellIdentityConflict("missing activation intent has ambiguous effects")
        current = await _read_agent_workspace_files(manager, backend.workspace_volume)
        if _workspace_revision(current) != prepared["workspace_revision"]:
            raise CellIdentityConflict("unrecorded activation source changed")
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
                "volume": volume,
                "state": "reverting",
                "old": {
                    "metadata": metadata,
                    "machine": saved,
                    "policy": policy,
                    "workspace_volume": backend.workspace_volume,
                    "contract": prepared["live_contract"],
                },
            },
        )

    @staticmethod
    def _runtime_identity(backend: Any, machine: Any) -> dict[str, Any]:
        policy = load_policy(backend)
        return {
            "workspace_volume": backend.workspace_volume,
            "metadata_epoch": backend._metadata().get("epoch"),
            "machine_epoch": machine.state().get("epoch"),
            "policy_epoch": policy["epoch"] if policy else None,
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
        if applied:
            result["source_revision"] = intent["source_revision"]
        else:
            result["safe_to_release"] = True
        return result

    @staticmethod
    def _file_digest(path: Path) -> str:
        with path.open("rb") as handle:
            return hashlib.file_digest(handle, "sha256").hexdigest()
