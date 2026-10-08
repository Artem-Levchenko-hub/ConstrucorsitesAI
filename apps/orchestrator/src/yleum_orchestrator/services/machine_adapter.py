"""Reachable portable Cell adapter behind the existing owner/lease/source checks."""

from __future__ import annotations

import asyncio
import hashlib
import json
import secrets
import socket
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast
from urllib.parse import quote
from uuid import UUID, uuid4, uuid5

import docker  # type: ignore[import-untyped]

from yleum_orchestrator.core.cell_resources import (
    CellFenceRejected,
    CellIdentityConflict,
    CellResourceError,
    CellResourceNames,
    LifecycleMutation,
    identity_labels,
)
from yleum_orchestrator.core.project_machine import MachineManifest
from yleum_orchestrator.core.stack_registry import get_stack
from yleum_orchestrator.services import nginx_writer
from yleum_orchestrator.services.cell_state import CellCredentialStore, CoreRuntimeCredentialStore
from yleum_orchestrator.services.docker_cell_resources import DockerCommandResult
from yleum_orchestrator.services.docker_machine_backend import DockerMachineBackend
from yleum_orchestrator.services.machine_environment import (
    MachineEnvironmentRef,
    MachineEnvironmentStore,
)
from yleum_orchestrator.services.machine_services import MachineServiceFailed, MachineServices
from yleum_orchestrator.services.project_database_credentials import (
    ProjectDatabaseCredentials,
    ProjectDatabaseCredentialStore,
)
from yleum_orchestrator.services.project_machine import (
    MachineOperationResult,
    ProjectMachine,
    machine_budget,
    machine_effect,
    machine_remaining_seconds,
    write_controller_json,
)
from yleum_orchestrator.services.project_migrations import (
    LEGACY_PATHS,
    RECEIPT_PREFIX,
    adaptation_database,
    run_project_migrations,
    select_migrations,
)
from yleum_orchestrator.services.react_effect_contract import (
    REACT_EFFECT_CHECK_PHASE,
    REACT_EFFECT_CONTRACT_JS,
    node_manifest_commands,
)
from yleum_orchestrator.services.restoration_database import close_controller_socket
from yleum_orchestrator.services.studio_origins import studio_origins

MACHINE_APPLY_TIMEOUT_SECONDS = 900
MACHINE_APPLY_CLEANUP_RESERVE_SECONDS = 30
_PROJECT_POSTGRES_MIN_MEMORY_BYTES = 128 * 1024**2
_PROJECT_POSTGRES_TARGET_MEMORY_BYTES = 256 * 1024**2
_PROJECT_POSTGRES_MIN_CPU_CORES = 0.1
_PROJECT_POSTGRES_TARGET_CPU_CORES = 0.15
# Gateway CPU: one full core while it starts, 5% of a core afterwards.
_GATEWAY_CPU_PERIOD = 100_000
_GATEWAY_BOOST_QUOTA = 100_000
_GATEWAY_STEADY_QUOTA = 5_000
_PUBLIC_CORE_COMMAND = ("node", "server.js")
_CORE_ROLE_PROTOCOL = "omnia.max-core.db-role-protocol"
_CORE_RESERVED_ENV = frozenset({
    "DATABASE_URL", "CORE_RUNTIME_PASSWORD", "POSTGRES_PASSWORD", "PGPASSWORD",
    "PGUSER", "PGHOST", "PGDATABASE", "PGPORT", "PGOPTIONS",
})


class MachineAdapter:
    def __init__(self, manager: Any, settings: Any) -> None:
        self.manager = manager
        self.settings = settings

    @property
    def root(self) -> Path:
        return Path(self.manager.state_store.root).parent / "project-machines"

    @property
    def core_runtime_credentials(self) -> CoreRuntimeCredentialStore:
        return CoreRuntimeCredentialStore(self.root / "core-runtime-credentials")

    def _core_image(self, client: Any, *, public_mode: bool = False) -> str:
        from yleum_orchestrator.services.docker_machine_backend import _PIN

        reference = getattr(self.settings, "cell_public_core_image" if public_mode
                            else "cell_preview_core_image", "")
        if not _PIN.fullmatch(reference):
            raise CellResourceError("pinned compiled MAX core image is required")
        image = client.images.get(reference)
        if (image.labels.get("omnia.max-core.protocol") != "1"
                or image.labels.get(_CORE_ROLE_PROTOCOL) != "1"
                or (not public_mode
                    and image.labels.get("omnia.max-core.preview-protocol") != "1")):
            raise CellResourceError("compiled MAX core image lacks required runtime protocols")
        return str(image.id)

    def bootstrap_core_database(
        self, state: Any, postgres_name: str, *, role_only: bool = False,
        maintenance: bool = False, public_mode: bool = False,
    ) -> None:
        """Short-lived admin process; shares only the exact owned PG network namespace."""
        client = self.manager.docker._client_obj()
        image = self._core_image(client, public_mode=public_mode)
        postgres = client.containers.get(postgres_name)
        postgres.reload()
        expected = identity_labels(state, "postgres-maintenance" if maintenance else "postgres")
        labels = postgres.attrs.get("Config", {}).get("Labels") or {}
        if (postgres.status != "running"
                or any(labels.get(key) != value for key, value in expected.items())):
            raise CellIdentityConflict("trusted core database identity mismatch")
        credential = self.core_runtime_credentials.load_or_create(state.workspace_id)
        admin = self.manager.credential_store.load_or_create(state.workspace_id)
        helper_name = "omnia-core-bootstrap-" + uuid4().hex
        helper_labels = {**expected, "omnia.resource_kind": "core-database-bootstrap"}
        helper = None
        try:
            helper = client.containers.create(
                image, ["timeout", "70", "node", "scripts/bootstrap-database.mjs",
                        *(["--role-only"] if role_only else [])],
                name=helper_name, labels=helper_labels, detach=True,
                network_mode="container:" + postgres.id,
                user="node", working_dir="/app", cap_drop=["ALL"], privileged=False,
                security_opt=["no-new-privileges:true"], read_only=True,
                mem_limit=256 * 1024**2, memswap_limit=256 * 1024**2,
                nano_cpus=500_000_000, pids_limit=64,
                environment={
                    "DATABASE_URL": "postgresql://postgres:"
                    + quote(admin.postgres_password, safe="") + "@127.0.0.1:5432/postgres",
                    "CORE_RUNTIME_PASSWORD": credential.runtime_password,
                },
            )
            helper.start()
            if helper.wait(timeout=80).get("StatusCode") != 0:
                raise CellResourceError("trusted core database bootstrap failed")
        except Exception:
            # Docker errors may echo environment/driver messages; never propagate them.
            raise CellResourceError("trusted core database bootstrap failed") from None
        finally:
            if helper is None:
                try:
                    helper = client.containers.get(helper_name)
                except docker.errors.NotFound:
                    pass
                except Exception:
                    raise CellResourceError("trusted core bootstrap cleanup unverified") from None
            if helper is not None:
                actual = helper.attrs.get("Config", {}).get("Labels") or {}
                if any(actual.get(key) != value for key, value in helper_labels.items()):
                    raise CellIdentityConflict("core bootstrap cleanup identity mismatch")
                try:
                    helper.remove(force=True)
                except Exception:
                    raise CellResourceError("trusted core bootstrap cleanup failed") from None

    def capabilities(self) -> dict[str, object]:
        return {
            "portable_machine": True,
            "manifest_path": ".omnia/cell.json",
            "public_package_egress": True,
            "persistent_environment": True,
            "managed_max_boundary": True,
            "dedicated_postgres": True,
            "database_url_env": "DATABASE_URL",
            "database_admin": "runtime-crud",
            "database_migrations": "controller-limited-login",
            "commands": ["bash", "build", "runtime_check"],
            "task_roles": ["bootstrap", "fast_check", "full_build"],
            "framework": "nextjs",
            "default_stack": "max-nextjs-typescript",
            "node_major": 22,
            "package_manager": "pnpm@9.15.0",
            "dependency_policy": "project-controlled",
        }

    def validate_available(self) -> None:
        from yleum_orchestrator.services.docker_machine_backend import _PIN

        if (
            not _PIN.fullmatch(self.settings.cell_machine_base_image)
            or not _PIN.fullmatch(self.settings.cell_machine_guard_image)
            or not _PIN.fullmatch(self.manager.profile.postgres_image)
            or not self.settings.cell_machine_denied_cidrs
            or not self.settings.cell_network_pool
        ):
            raise CellResourceError(
                "portable machine base/guard/postgres images, public host denies "
                "and pool are required"
            )
        client = self.manager.docker._client_obj()
        for image in (
            self.settings.cell_machine_base_image,
            self.settings.cell_machine_guard_image,
            self.manager.profile.postgres_image,
        ):
            client.images.get(image)

    def exists(self, workspace_id: UUID) -> bool:
        return (self.root / str(workspace_id) / "machine.json").is_file()

    def recovery_required(self, state: Any) -> bool:
        if not self.exists(state.workspace_id):
            return False
        _machine, backend = self.parts(state)
        metadata = backend._metadata()
        return bool(
            metadata.get("restore_in_progress")
            or metadata.get("quiesce_state") in {"pending", "failed"}
        )

    def parts(self, state: Any) -> tuple[ProjectMachine, DockerMachineBackend]:
        names = state.resource_names
        if names is None or state.project_id is None or state.owner_id is None:
            raise CellResourceError("portable machine identity incomplete")
        profile = self.manager.profile
        project_postgres_memory = self._project_postgres_memory_bytes()
        project_postgres_cpu = self._project_postgres_cpu_cores()
        self._max_core_memory_bytes()
        self._max_core_cpu_cores()
        backend = DockerMachineBackend(
            client=self.manager.docker._client_obj(),
            workspace_id=state.workspace_id,
            project_id=state.project_id,
            owner_id=state.owner_id,
            root=self.root,
            internal_network=names.internal_network,
            workspace_volume=names.workspace_volume,
            base_image=self.settings.cell_machine_base_image,
            guard_image=self.settings.cell_machine_guard_image,
            postgres_image=profile.postgres_image,
            project_postgres_password=self.project_database_password(state.workspace_id),
            project_credentials=self.project_database_credentials(state.workspace_id),
            project_postgres_memory_bytes=project_postgres_memory,
            project_postgres_cpu_cores=project_postgres_cpu,
            network_pool=self.settings.cell_network_pool,
            denied_cidrs=tuple(self.settings.cell_machine_denied_cidrs),
            cpu_cores=(
                profile.active_machine_cpu_cores
                if profile.is_v2
                else profile.executor_cpu_cores - 0.2
            ),
            memory_bytes=(
                profile.active_machine_memory_bytes
                if profile.is_v2
                else profile.executor_memory_bytes - 128 * 1024**2
            ),
            disk_bytes=profile.required_free_disk_bytes,
            pids=512,
            resource_profile_version=profile.profile_version,
            namespace=self.manager.namespace,
        )
        # Restores activate a new code volume; business volumes keep their identity.
        active_code = backend._metadata().get("active_code_volume")
        if active_code and active_code != names.workspace_volume:
            import re

            pattern = re.escape(backend.stem) + r"-code-[0-9a-f]{32}"
            if re.fullmatch(pattern, str(active_code)) is None:
                raise CellResourceError("invalid active code volume identity")
            volume = backend._lookup(backend.client.volumes, str(active_code), "project-volume")
            if volume is None:
                raise CellResourceError("active code volume is missing; recovery required")
            backend.workspace_volume = str(active_code)

        def epoch() -> int | None:
            current = self.manager.state_store.load(state.workspace_id)
            if current is None or current.active_generation_run_id is None:
                return None
            value = current.active_generation_fencing_epoch
            return int(value) if value is not None else None

        return ProjectMachine(self.root, state.workspace_id, backend, lease_epoch=epoch), backend

    def secret(self, workspace_id: UUID) -> str:
        # Independent of project PG password and old agent-home secret files.
        return (
            CellCredentialStore(self.root / "boundary-secrets")
            .load_or_create(
                workspace_id,
            )
            .postgres_password
        )

    def project_database_password(self, workspace_id: UUID) -> str:
        return (
            CellCredentialStore(self.root / "project-postgres-secrets")
            .load_or_create(workspace_id)
            .postgres_password
        )

    def project_database_credentials(self, workspace_id: UUID) -> ProjectDatabaseCredentials:
        from yleum_orchestrator.services.cell_state import _read_plain_json_file

        store = ProjectDatabaseCredentialStore(self.root / "project-db-credentials")
        metadata_path = self.root / str(workspace_id) / "docker.json"
        if (metadata_path.exists() and
                _read_plain_json_file(metadata_path).get("project_database_role_protocol") == 1):
            return store.load(workspace_id)
        return store.load_or_create(workspace_id)

    def _project_postgres_memory_bytes(self) -> int:
        if self.manager.profile.is_v2:
            return int(self.manager.profile.project_postgres_memory_bytes)
        draft = int(self.manager.profile.draft_memory_bytes)
        reserve = max(_PROJECT_POSTGRES_MIN_MEMORY_BYTES, draft // 4)
        return min(_PROJECT_POSTGRES_TARGET_MEMORY_BYTES, reserve)

    def _project_postgres_cpu_cores(self) -> float:
        if self.manager.profile.is_v2:
            return float(self.manager.profile.project_postgres_cpu_cores)
        draft = float(self.manager.profile.draft_cpu_cores)
        reserve = max(_PROJECT_POSTGRES_MIN_CPU_CORES, draft / 3)
        return min(_PROJECT_POSTGRES_TARGET_CPU_CORES, reserve)

    def _max_core_memory_bytes(self) -> int:
        if self.manager.profile.is_v2:
            return int(self.manager.profile.managed_core_memory_bytes)
        remaining = (
            int(self.manager.profile.draft_memory_bytes) - self._project_postgres_memory_bytes()
        )
        if remaining <= 0:
            raise CellResourceError("draft memory cannot fit managed core and project postgres")
        return remaining

    def _max_core_cpu_cores(self) -> float:
        if self.manager.profile.is_v2:
            return float(self.manager.profile.managed_core_cpu_cores)
        remaining = float(self.manager.profile.draft_cpu_cores) - self._project_postgres_cpu_cores()
        if remaining <= 0:
            raise CellResourceError("draft CPU cannot fit managed core and project postgres")
        return remaining

    async def execute(
        self, state: Any, manifest: MachineManifest, request: Any
    ) -> DockerCommandResult:
        command_grace = int(
            getattr(self.settings, "cell_machine_command_grace_seconds", 20)
        )
        cleanup_reserve = command_grace + 5
        try:
            with machine_budget(request.timeout_seconds + cleanup_reserve):
                async with asyncio.timeout(
                    machine_remaining_seconds(request.timeout_seconds + cleanup_reserve)
                ):
                    return await self._execute(state, manifest, request)
        except TimeoutError:
            machine, _backend = self.parts(state)
            digest = self._request_digest(manifest, request)
            mutation = LifecycleMutation(request.operation_id, request.fencing_epoch, digest)
            with machine_budget(None):
                status = await machine.inspect_request_status(
                    operation_id=request.operation_id
                )
                if status.result is not None:
                    return DockerCommandResult(
                        exit_code=status.result.exit_code or 0,
                        output=status.result.output,
                        timed_out=status.result.timed_out,
                    )
                if status.state in {"starting", "running"}:
                    active_names = {task.name for task in manifest.tasks}
                    if (request.task_role in {"fast_check", "full_build"}
                            and node_manifest_commands(manifest)):
                        active_names.add(REACT_EFFECT_CHECK_PHASE)
                    if status.phase in active_names:
                        operation_id = uuid5(request.operation_id, status.phase)
                        try:
                            await machine.exec_terminate(
                                str(operation_id),
                                LifecycleMutation(
                                    operation_id,
                                    request.fencing_epoch,
                                    digest,
                                ),
                                    grace_seconds=command_grace,
                            )
                        except CellFenceRejected:
                            # The request budget can expire just before exec_start
                            # records a child command; there is then nothing to kill.
                            pass
            terminal = MachineOperationResult(
                operation_id=str(request.operation_id),
                state="completed",
                exit_code=124,
                output="request budget exhausted; command process terminated",
                timed_out=True,
            )
            with machine_budget(None):
                stored = await machine.request_finish(mutation, terminal)
            return DockerCommandResult(
                exit_code=stored.exit_code or 124,
                output=stored.output,
                timed_out=stored.timed_out,
            )

    async def protect_migration_sources(
        self, state: Any, files: dict[str, str],
    ) -> dict[str, str]:
        from yleum_orchestrator.services.applied_migration_sources import (
            protected_sources,
            require_protected_sources,
        )

        if not self.exists(state.workspace_id):
            return {}
        machine, backend = self.parts(state)
        if getattr(state, "active_generation_run_id", None) is not None:
            postgres = await machine_effect(backend._project_postgres)
            if postgres is not None:
                await machine_effect(postgres.reload)
            if postgres is None or postgres.status != "running":
                # A released environment retains its DB volume. Resume it under
                # the validated generation lease before the first write, using
                # its persisted manifest rather than the proposed source patch.
                manifest = MachineManifest.model_validate(machine.state()["manifest"])
                await machine.ensure(manifest, LifecycleMutation(
                    uuid4(), state.fencing_epoch, manifest.digest(),
                ))
        protected = await machine_effect(
            protected_sources, backend, files,
            verify_only=adaptation_database(state, machine.state()),
        )
        require_protected_sources(protected, files)
        return cast(dict[str, str], protected)

    async def guarded_execute(
        self, state: Any, manifest: MachineManifest, request: Any,
    ) -> DockerCommandResult:
        """Caller holds operation lock; inspect physical source even on command failure."""
        from yleum_orchestrator.routers.workspace import _read_agent_workspace_files
        from yleum_orchestrator.services.applied_migration_sources import (
            protected_sources,
            require_protected_sources,
        )

        machine, backend = self.parts(state)
        record = machine.state().get("operations", {}).get(str(request.operation_id), {})
        if record.get("source_guard_failure"):
            raise CellResourceError(
                "this command changed applied migration source; use a new operation"
            )
        before = await _read_agent_workspace_files(self.manager, backend.workspace_volume)
        protected = await self.protect_migration_sources(state, before)
        try:
            return await self.execute(state, manifest, request)
        finally:
            # SQL may have committed before a failed build, role bootstrap or
            # cancellation. Recover its pre-submission witness from the journal.
            with machine_budget(None):
                try:
                    protected = {}  # Never restore a witness if the live DB cannot revalidate it.
                    protected = await machine_effect(
                        protected_sources, backend, before,
                        verify_only=adaptation_database(state, machine.state()),
                    )
                    after = await _read_agent_workspace_files(
                        self.manager, backend.workspace_volume,
                    )
                    require_protected_sources(protected, after)
                except BaseException:
                    saved = machine.state()
                    record = saved.get("operations", {}).get(str(request.operation_id))
                    if record is not None:
                        record["source_guard_failure"] = True
                        record["state"] = "failed"
                        record["result"] = MachineOperationResult(
                            operation_id=str(request.operation_id), state="completed", exit_code=1,
                            output="applied migration source protection rejected this command",
                        ).model_dump()
                        record.pop("compiled_asset_receipt", None)
                        record.pop("transport_response", None)
                        write_controller_json(machine.path, saved)
                    # Terminating the entire guest also catches daemonized children.
                    # Never restore while a guest can still write the source volume.
                    await machine_effect(backend.stop_machine)
                    container = await machine_effect(backend._container)
                    if container is not None:
                        await machine_effect(container.reload)
                        if container.status not in {"exited", "dead", "created"}:
                            raise CellResourceError(
                                "applied migration protection could not stop the source writer"
                            ) from None
                    if protected:
                        await self.manager.docker.write_volume_files(backend.workspace_volume, {
                            path: source.encode("utf-8") for path, source in protected.items()
                        })
                        restored = await _read_agent_workspace_files(
                            self.manager, backend.workspace_volume,
                        )
                        require_protected_sources(protected, restored)
                    raise

    @staticmethod
    def _request_digest(manifest: MachineManifest, request: Any) -> str:
        return hashlib.sha256(
            json.dumps(
                {
                    "cmd": request.cmd,
                    "role": request.task_role,
                    "revision": request.expected_revision,
                    "manifest": manifest.digest(),
                    "timeout_seconds": request.timeout_seconds,
                    **({"migration_contract": "project-migrations-v1"}
                       if request.task_role == "full_build" else {}),
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()

    async def _execute(
        self, state: Any, manifest: MachineManifest, request: Any
    ) -> DockerCommandResult:
        request_deadline = time.monotonic() + machine_remaining_seconds(request.timeout_seconds)
        role = request.task_role
        if role == "build" and not any(task.role == "test" for task in manifest.tasks):
            raise ValueError("portable build requires a declared test task")
        if role:
            roles = ("bootstrap", "build", "test") if role == "build" else (role,)
            commands = [
                (task.name, task.argv, task.cwd, task.timeout_seconds)
                for current_role in roles
                for task in manifest.tasks
                if task.role == current_role
            ]
            if not commands:
                raise ValueError(f"manifest has no {role} task")
        else:
            commands = [("shell", ["sh", "-lc", request.cmd], ".", request.timeout_seconds)]
        machine, _backend = self.parts(state)
        digest = self._request_digest(manifest, request)
        mutation = LifecycleMutation(request.operation_id, request.fencing_epoch, digest)
        await machine.ensure(manifest, mutation)
        replayed = await machine.request_start(
            mutation,
            phase=role or "shell",
            deadline_at=datetime.now(UTC) + timedelta(seconds=request.timeout_seconds),
        )
        if replayed is not None:
            return DockerCommandResult(
                exit_code=replayed.exit_code or 0,
                output=replayed.output,
                timed_out=replayed.timed_out,
            )

        async def finish(
            *,
            exit_code: int,
            output: str,
            timed_out: bool = False,
        ) -> DockerCommandResult:
            terminal = MachineOperationResult(
                operation_id=str(request.operation_id),
                state="completed",
                exit_code=exit_code,
                output=output[-24000:],
                timed_out=timed_out,
            )
            stored = await machine.request_finish(mutation, terminal)
            return DockerCommandResult(
                exit_code=stored.exit_code or 0,
                output=stored.output,
                timed_out=stored.timed_out,
            )

        output: list[str] = []
        if role == "fast_check":
            try:
                gap = await self._migration_dependency_gap(state)
            except CellResourceError as exc:
                return await finish(exit_code=1, output=str(exc))
            if gap:
                return await finish(exit_code=1, output=gap)
        if role == "full_build":
            try:
                await self._project_migrations(state, request, verify_applied=False)
            except CellResourceError as exc:
                return await finish(exit_code=1, output=str(exc))
        heartbeat_seconds = int(
            getattr(self.settings, "cell_machine_command_heartbeat_seconds", 15)
        )
        if role in {"fast_check", "full_build"} and node_manifest_commands(manifest):
            # Fresh portable volumes have source but no installed dependencies.
            # Run only the declared bootstrap under this request's existing
            # deadline/replay journal before loading the workspace AST parser.
            bootstrap = [
                (task.name, task.argv, task.cwd, task.timeout_seconds)
                for task in manifest.tasks if task.role == "bootstrap"
            ]
            commands = [*bootstrap, (
                REACT_EFFECT_CHECK_PHASE,
                ["node", "-e", REACT_EFFECT_CONTRACT_JS], ".", 30,
            ), *commands]
        for name, argv, cwd, timeout in commands:
            await machine.request_heartbeat(
                mutation,
                phase=name,
                log_bytes=len("\n".join(output).encode("utf-8")),
                min_interval_seconds=heartbeat_seconds,
                force=True,
            )
            operation_mutation = LifecycleMutation(
                uuid5(request.operation_id, name), mutation.fencing_epoch, digest
            )
            if time.monotonic() >= request_deadline:
                return await finish(
                    exit_code=124,
                    output="request budget exhausted before next command",
                    timed_out=True,
                )
            deadline = min(request_deadline, time.monotonic() + timeout)
            operation = await machine.exec_start(argv, cwd, operation_mutation)
            while True:
                result = await machine.exec_status(operation, operation_mutation)
                await machine.request_heartbeat(
                    mutation,
                    phase=name,
                    log_bytes=len(("\n".join(output) + result.output).encode("utf-8")),
                    min_interval_seconds=heartbeat_seconds,
                )
                if time.monotonic() >= deadline:
                    with machine_budget(None):
                        await machine.exec_terminate(
                            operation,
                            operation_mutation,
                            grace_seconds=int(
                                getattr(
                                    self.settings,
                                    "cell_machine_command_grace_seconds",
                                    20,
                                )
                            ),
                        )
                    return await finish(
                        exit_code=124,
                        output="\n".join(output)
                        + f"\n{name} timed out; command process terminated",
                        timed_out=True,
                    )
                if result.state == "completed":
                    output.append(f"[{name}] {result.output}")
                    if result.exit_code != 0:
                        return await finish(
                            exit_code=result.exit_code or 1,
                            output="\n".join(output),
                        )
                    break
                await asyncio.sleep(0.2)
        if role == "full_build":
            try:
                receipt = await self._project_migrations(state, request, verify_applied=True)
                from yleum_orchestrator.services.next_compilation_capture import (
                    capture_next_compilation,
                )

                _machine, compiler_backend = self.parts(state)
                compilation = await machine_effect(
                    capture_next_compilation, compiler_backend, request, manifest,
                )
                await self._activate_runtime(state, manifest, request)
            except (MachineServiceFailed, CellResourceError) as exc:
                # A failed product start is command evidence, not a transport
                # rejection. Finish the durable request and retain task logs.
                # finish() keeps the last 24k characters; bound this payload
                # first so successful task logs cannot displace the diagnosis.
                failure = str(exc)[:4000]
                task_tail = "\n".join(output)[-19000:]
                return await finish(exit_code=1, output=f"{failure}\n{task_tail}")
            output.append(RECEIPT_PREFIX + json.dumps(receipt, sort_keys=True))
            self._store_migration_receipt(state, request.operation_id, receipt)
            if compilation is not None:
                saved = _machine.state()
                stored_operation = saved["operations"][str(request.operation_id)]
                stored_operation["compiled_asset_receipt"] = compilation
                write_controller_json(_machine.path, saved)
        return await finish(exit_code=0, output="\n".join(output))

    def _store_migration_receipt(
        self, state: Any, operation_id: UUID, receipt: dict[str, Any],
    ) -> None:
        machine, _backend = self.parts(state)
        saved = machine.state()
        saved["operations"][str(operation_id)]["project_migration_receipt"] = receipt
        write_controller_json(machine.path, saved)

    def migration_receipt(self, state: Any, operation_id: UUID) -> dict[str, Any] | None:
        machine, _backend = self.parts(state)
        return cast(dict[str, Any] | None, machine.state().get("operations", {}).get(
            str(operation_id), {}
        ).get("project_migration_receipt"))

    def compilation_receipt(self, state: Any, operation_id: UUID) -> dict[str, Any] | None:
        machine, _backend = self.parts(state)
        return cast(dict[str, Any] | None, machine.state().get("operations", {}).get(
            str(operation_id), {}
        ).get("compiled_asset_receipt"))

    async def _migration_inventory(self, backend: Any) -> dict[str, str]:
        from yleum_orchestrator.routers.workspace import _read_agent_workspace_files
        from yleum_orchestrator.services.cell_draft_support import trusted_template_source

        files = await _read_agent_workspace_files(self.manager, backend.workspace_volume)
        template = trusted_template_source(get_stack("max-miniapp-nextjs").template_dir)
        legacy = {path: (template / path).read_text(encoding="utf-8") for path in LEGACY_PATHS}
        return select_migrations(files, legacy)

    async def _migration_dependency_gap(self, state: Any) -> str | None:
        from yleum_orchestrator.services.project_migration_dependencies import (
            check_migration_dependencies,
        )

        machine, backend = self.parts(state)
        if adaptation_database(state, machine.state()):
            return None  # Adaptation never reinterprets copied historical migrations.
        migrations = await self._migration_inventory(backend)
        return cast(
            str | None, await machine_effect(check_migration_dependencies, backend, migrations)
        )

    async def _project_migrations(
        self, state: Any, request: Any, *, verify_applied: bool,
    ) -> dict[str, Any]:
        machine, backend = self.parts(state)
        verify_only = adaptation_database(state, machine.state())
        # Adaptation verifies its copied DB, never classifies/replays historical SQL.
        migrations = {}
        if not verify_only:
            migrations = await self._migration_inventory(backend)
            if verify_applied:
                from yleum_orchestrator.services.applied_migration_sources import (
                    protected_sources,
                    require_protected_sources,
                )

                protected = await machine_effect(protected_sources, backend, migrations)
                require_protected_sources(protected, migrations)
        if not (verify_only or verify_applied):
            mutation = LifecycleMutation(request.operation_id, request.fencing_epoch,
                                         self._request_digest(
                                             MachineManifest.model_validate(machine.state()["manifest"]),
                                             request))
            await machine.assert_ready(mutation)
            await machine_effect(backend.prepare_project_database_migrations, request.fencing_epoch)
            from yleum_orchestrator.services.applied_migration_sources import save_migration_intent

            await machine_effect(save_migration_intent, backend, migrations)
        receipt: dict[str, Any] = await machine_effect(
            run_project_migrations, backend, migrations,
            verify_only=verify_only, verify_applied=verify_applied,
        )
        receipt.update(
            workspace_id=str(state.workspace_id),
            generation_run_id=str(request.generation_run_id),
            fencing_epoch=request.fencing_epoch,
            source_revision=request.expected_revision,
        )
        if not verify_applied:
            self._store_migration_receipt(state, request.operation_id, receipt)
        return receipt

    async def _activate_runtime(self, state: Any, manifest: MachineManifest, request: Any) -> None:
        """Start services from the exact successful full-build workspace."""
        await self.checkpoint(state)
        machine, backend = self.parts(state)
        mutation = LifecycleMutation(uuid4(), request.fencing_epoch, manifest.digest())
        await machine.ensure(manifest, mutation)
        await MachineServices(machine, backend).reconcile(manifest, mutation)
        await machine_effect(
            self._start_boundary,
            state,
            manifest,
            backend,
            request.fencing_epoch,
        )

    async def checkpoint(
        self,
        state: Any,
        *,
        volumes: tuple[str, ...] | None = None,
        persist: bool = True,
        observer: Any = None,
    ) -> MachineEnvironmentRef | None:
        if not self.exists(state.workspace_id):
            return None
        machine, backend = self.parts(state)
        if backend._container() is None:
            saved_ref = backend._metadata().get("environment_ref")
            return MachineEnvironmentRef.model_validate(saved_ref) if saved_ref else None
        manifest = MachineManifest.model_validate(machine.state()["manifest"])
        store = MachineEnvironmentStore(
            self.root / "artifacts", state.workspace_id, backend, max_bytes=backend.disk_bytes
        )
        store.observer = observer
        saved_ref = backend._metadata().get("environment_ref")
        seal = await self._seal_identity(backend) if persist else None
        reference = await machine_effect(
            store.capture,
            manifest_digest=manifest.digest(),
            base_image=backend.base_image,
            volumes=volumes if volumes is not None else backend.snapshot_volume_names(manifest),
            manifest=manifest,
            previous=MachineEnvironmentRef.model_validate(saved_ref) if saved_ref else None,
        )
        if persist:
            metadata = backend._metadata()
            metadata.update(
                environment_ref=reference.model_dump(mode="json"), restored_image=reference.image_id
            )
            # P05/P12: a persisted checkpoint (every halt after a generation) is
            # the sealed release artifact of this exact source revision; a
            # publication of the same revision starts from it without stopping
            # or waking the editor. Unknown identity simply records nothing.
            for key in (
                "environment_revision",
                "environment_schema_digest",
                "environment_sealed_at",
                "environment_project_db_role_protocol",
            ):
                metadata.pop(key, None)
            if seal is not None:
                metadata.update(
                    environment_revision=seal["revision"],
                    environment_schema_digest=seal["schema_digest"],
                    environment_sealed_at=datetime.now(UTC).isoformat(),
                )
                if (seal.get("project_db_role_protocol") == 1
                        and metadata.get("project_database_role_protocol") == 1):
                    metadata["environment_project_db_role_protocol"] = 1
            write_controller_json(backend.metadata_path, metadata)
        return cast(MachineEnvironmentRef, reference)

    async def _seal_identity(self, backend: Any) -> dict[str, str | int] | None:
        """Source revision (agent view of the workspace) and the live database
        schema, read while the machine still runs. Best effort: None on failure."""
        from yleum_orchestrator.routers.runtime import _workspace_revision
        from yleum_orchestrator.routers.workspace import _read_agent_workspace_files
        from yleum_orchestrator.services.published_machine_backend import PublishedMachineBackend

        try:
            files = await _read_agent_workspace_files(self.manager, backend.workspace_volume)
            revision = _workspace_revision(files)
            schema = await machine_effect(PublishedMachineBackend.schema_digest, backend)
            role_protocol = backend._metadata().get("project_database_role_protocol")
        except asyncio.CancelledError:
            raise
        except Exception:
            return None
        seal: dict[str, str | int] = {"revision": revision, "schema_digest": schema}
        if role_protocol == 1:
            seal["project_db_role_protocol"] = 1
        return seal

    async def checkpoint_payload(self, state: Any) -> bytes | None:
        reference = await self.checkpoint(state)
        if reference is None:
            return None
        machine, _backend = self.parts(state)
        manifest = MachineManifest.model_validate(machine.state()["manifest"])
        if reference.manifest_digest != manifest.digest():
            raise CellResourceError("checkpoint environment and source manifest differ")
        return json.dumps(
            {
                "manifest": manifest.model_dump(mode="json"),
                "reference": reference.model_dump(mode="json"),
            },
            sort_keys=True,
        ).encode()

    async def validate_restore_payload(self, state: Any, payload: bytes | None) -> None:
        if payload is None:
            return
        value = json.loads(payload)
        if set(value) != {"manifest", "reference"}:
            raise CellResourceError("invalid portable checkpoint envelope")
        manifest = MachineManifest.model_validate(value["manifest"])
        reference = MachineEnvironmentRef.model_validate(value["reference"])
        _machine, backend = self.parts(state)
        backend.validate_restore_reference(reference)
        store = MachineEnvironmentStore(
            self.root / "artifacts", state.workspace_id, backend, max_bytes=backend.disk_bytes
        )
        await machine_effect(store.validate, reference, manifest_digest=manifest.digest())

    async def restore_payload(self, state: Any, payload: bytes | None) -> None:
        await self.validate_restore_payload(state, payload)
        machine, backend = self.parts(state)
        if payload is None:
            if self.exists(state.workspace_id):
                await self.halt(state, remove_network=True, capture=False)
                # Explicit restoration of a legacy checkpoint restores legacy
                # selection too; immutable machine artifacts remain recoverable.
                machine.path.unlink(missing_ok=True)
                backend.metadata_path.unlink(missing_ok=True)
            (machine.path.parent / "portable.json").unlink(missing_ok=True)
            return
        value = json.loads(payload)
        if set(value) != {"manifest", "reference"}:
            raise CellResourceError("invalid portable checkpoint envelope")
        manifest = MachineManifest.model_validate(value["manifest"])
        reference = MachineEnvironmentRef.model_validate(value["reference"])
        store = MachineEnvironmentStore(
            self.root / "artifacts", state.workspace_id, backend, max_bytes=backend.disk_bytes
        )
        await machine_effect(store.restore, reference, manifest_digest=manifest.digest())
        metadata = backend._metadata()
        metadata["manifest"] = manifest.model_dump(mode="json")
        write_controller_json(backend.metadata_path, metadata)
        saved = machine.state()
        saved.update(
            manifest=manifest.model_dump(mode="json"),
            epoch=state.fencing_epoch,
            ready_epoch=None,
            cancelled_epoch=0,
            operations={},
        )
        write_controller_json(machine.path, saved)

    async def apply(
        self, state: Any, manifest: MachineManifest, request: Any
    ) -> DockerCommandResult:
        try:
            work_seconds = max(
                0, MACHINE_APPLY_TIMEOUT_SECONDS - MACHINE_APPLY_CLEANUP_RESERVE_SECONDS
            )
            with machine_budget(work_seconds):
                async with asyncio.timeout(work_seconds):
                    result = await self._apply(state, manifest, request)
                    if result.timed_out:
                        raise TimeoutError("nested machine execution exhausted work budget")
                    return result
        except TimeoutError:
            # machine_effect drains in-flight Docker mutations before cancellation
            # escapes. Teardown is then serialized under the workspace lock.
            await self.halt(state, capture=False)
            return DockerCommandResult(
                exit_code=124,
                output="apply total budget exhausted; runtime stopped",
                timed_out=True,
            )

    async def _apply(
        self, state: Any, manifest: MachineManifest, request: Any
    ) -> DockerCommandResult:
        from yleum_orchestrator.schemas.workspace import WorkspaceAgentExecRequest

        result = await self.guarded_execute(
            state,
            manifest,
            WorkspaceAgentExecRequest(
                generation_run_id=request.generation_run_id,
                fencing_epoch=request.fencing_epoch,
                expected_revision=request.expected_revision,
                cmd="omnia:build",
                task_role="full_build",
                operation_id=uuid4(),
                timeout_seconds=900,
            ),
        )
        if result.exit_code:
            return result
        return result

    def _public_auth_secret(
        self, state: Any, backend: DockerMachineBackend, runtime_env: dict[str, str],
    ) -> str:
        """Rotate only the public cookie key on bot change/revoke, before reopening ingress."""
        path = self.root / "public-boundary-auth" / f"{state.workspace_id}.json"
        if path.is_symlink():
            raise CellResourceError("unsafe public auth configuration")
        old = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        digest = hashlib.sha256(json.dumps(runtime_env, sort_keys=True).encode()).hexdigest()
        secret = self.secret(state.workspace_id)
        core = backend._lookup(backend.client.containers, backend.stem + "-max-core",
                               "managed-max-core")
        changed = old.get("digest") != digest
        mismatched = False
        if core is not None:
            env = dict(item.split("=", 1) for item in core.attrs.get("Config", {}).get("Env", [])
                       if "=" in item)
            mismatched = env.get("AUTH_SECRET") != secret
        if changed or mismatched:
            gateway = backend._lookup(backend.client.containers, backend.stem + "-gateway",
                                      "max-gateway")
            if gateway is not None:
                gateway.remove(force=True)
            if core is not None:
                core.remove(force=True)
            if old and changed:
                secret = secrets.token_urlsafe(32)
                write_controller_json(
                    self.root / "boundary-secrets" / f"{state.workspace_id}.json",
                    {"postgres_password": secret},
                )
            write_controller_json(path, {"digest": digest})
        return secret

    def _start_boundary(
        self, state: Any, manifest: MachineManifest, backend: DockerMachineBackend, epoch: int,
        *, public_mode: bool = False, runtime_env: dict[str, str] | None = None,
        business_config_override: dict[str, Any] | None = None, observer: Any = None,
    ) -> None:
        transition: dict[str, Any] = {}
        try:
            self._start_boundary_impl(
                state, manifest, backend, epoch, public_mode=public_mode,
                runtime_env=runtime_env, business_config_override=business_config_override,
                observer=observer, transition=transition,
            )
        except BaseException:
            # Until gateway replacement begins, the original serving core and
            # gateway remain usable, even if bootstrap/config/auth checks fail.
            if not transition.get("switch_started"):
                candidate = transition.get("candidate")
                if candidate is not None:
                    candidate.remove(force=True)
                if transition.get("renamed_old"):
                    transition["old"].rename(backend.stem + "-max-core")
            raise
        else:
            retired = backend._lookup(backend.client.containers,
                                      backend.stem + "-max-core-retired", "managed-max-core")
            if retired is not None:
                retired.remove(force=True)

    def _start_boundary_impl(
        self, state: Any, manifest: MachineManifest, backend: DockerMachineBackend, epoch: int,
        *, public_mode: bool, runtime_env: dict[str, str] | None,
        business_config_override: dict[str, Any] | None, observer: Any,
        transition: dict[str, Any],
    ) -> None:
        # P13: the publication trace sees each readiness step of the trusted
        # boundary (core, configuration readback, auth probes, gateway) instead
        # of one opaque "verify_runtime"; a None observer changes nothing.
        def stage(name: str) -> None:
            if observer is not None:
                observer.stage(name)

        stage("verify_core")
        client = backend.client
        names = state.resource_names
        if _CORE_RESERVED_ENV.intersection(runtime_env or {}):
            raise CellResourceError("runtime configuration contains reserved database credentials")
        image_tag = self._core_image(client, public_mode=public_mode)
        compiled_core = True
        core_name = backend.stem + "-max-core"
        core = backend._lookup(client.containers, core_name, "managed-max-core")
        store = self.core_runtime_credentials
        if (core is not None
                and (core.attrs.get("Config", {}).get("Labels") or {}).get(_CORE_ROLE_PROTOCOL)
                == "1"
                and not (store.root / f"{state.workspace_id}.json").is_file()):
            raise CellResourceError(
                "existing core runtime credential missing; explicit recovery required"
            )
        runtime_credential = store.load_or_create(state.workspace_id)
        runtime_dsn = "postgresql://omnia_core_runtime:" + quote(
            runtime_credential.runtime_password, safe=""
        ) + f"@{names.postgres_container}:5432/postgres"
        if (core is not None
                and (core.attrs.get("Config", {}).get("Labels") or {}).get(_CORE_ROLE_PROTOCOL)
                == "1"):
            env = dict(item.split("=", 1) for item in core.attrs["Config"].get("Env", [])
                       if "=" in item)
            if env.get("DATABASE_URL") != runtime_dsn:
                raise CellResourceError(
                    "core runtime credential mismatch; explicit recovery required"
                )
        # Public auth rotation may retire the old boundary. Credential validation
        # must precede it, otherwise a missing sidecar could silently rotate DB login.
        secret = (self._public_auth_secret(state, backend, runtime_env or {})
                  if public_mode else self.secret(state.workspace_id))
        core = backend._lookup(client.containers, core_name, "managed-max-core")
        public_options = ""
        if compiled_core:
            # The trusted core never needs a dev compiler, including owner preview.
            # Bound V8 from the container, not the host's available memory.
            heap_mib = max(64, min(384, self._max_core_memory_bytes() // (2 * 1024**2)))
            public_options = f"--max-old-space-size={heap_mib}"
        if core is not None:
            config = core.attrs.get("Config", {})
            env = dict(item.split("=", 1) for item in config.get("Env", []) if "=" in item)
            if ((compiled_core and (
                    env.get("NODE_OPTIONS") != public_options
                        or config.get("Cmd") != list(_PUBLIC_CORE_COMMAND)
                        or core.attrs.get("Image") != image_tag
                        or env.get("DATABASE_URL") != runtime_dsn
                        or any(key in env for key in _CORE_RESERVED_ENV - {"DATABASE_URL"})
                        or (config.get("Labels") or {}).get(_CORE_ROLE_PROTOCOL) != "1"
                        # Retire cores created with the removed encrypted-data key mount.
                        or "omnia.max-core.data-key" in (config.get("Labels") or {})))
                    or (not public_mode and (
                        env.get("OMNIA_OWNER_PREVIEW") != "1"
                        or env.get("OMNIA_PUBLIC_APP_ORIGIN")
                        or env.get("AUTH_SECRET") != secret
                        or env.get("OMNIA_PROJECT_ID") != str(state.project_id)
                    ))):
                # Runtime-only upgrade, including retained legacy preview cores.
                # Keep auth secret, product and all DB containers/volumes.
                transition["old"] = core
                core = None
        if core is None:
            self.bootstrap_core_database(state, names.postgres_container, public_mode=public_mode)
            candidate_name = backend.stem + "-max-core-candidate"
            stale = backend._lookup(client.containers, candidate_name, "managed-max-core")
            if stale is not None:
                stale.remove(force=True)
            core = client.containers.create(
                image_tag,
                **({"command": list(_PUBLIC_CORE_COMMAND)} if compiled_core else {}),
                name=candidate_name,
                labels={**backend.labels("managed-max-core"), _CORE_ROLE_PROTOCOL: "1",
                        **({"omnia.max-core.protocol": "1"} if compiled_core else {})},
                detach=True,
                network=names.internal_network,
                cap_drop=["ALL"],
                privileged=False,
                security_opt=["no-new-privileges:true"],
                user="node",
                working_dir="/app",
                environment={
                    "NODE_ENV": "development",
                    "AUTH_SECRET": secret,
                    "OMNIA_PROJECT_ID": str(state.project_id),
                    "REDIS_URL": f"redis://{names.redis_container}:6379/0",
                    **(runtime_env or {}),
                    "DATABASE_URL": runtime_dsn,
                    **({"NODE_OPTIONS": public_options, "NODE_ENV": "production",
                        "HOSTNAME": "0.0.0.0", "PORT": "3000"} if compiled_core else {}),
                    **({
                        "OMNIA_OWNER_PREVIEW": "1",
                        "OMNIA_OWNER_PREVIEW_ORIGINS": json.dumps(studio_origins()),
                    } if not public_mode else {}),
                },
                mem_limit=self._max_core_memory_bytes(),
                memswap_limit=self._max_core_memory_bytes(),
                nano_cpus=int(self._max_core_cpu_cores() * 1_000_000_000),
                pids_limit=256,
            )
            transition["candidate"] = core
            # Only the immutable managed core can call its fixed platform API.
            # Generated project code still has the namespace DROP guard.
            backend._network(backend.stem + "-public", internal=False).connect(core)
        core.reload()
        if core.status != "running":
            core.start()
        core.reload()
        core_ip = core.attrs["NetworkSettings"]["Networks"][names.internal_network]["IPAddress"]
        from yleum_orchestrator.services.machine_business_config import (
            apply_public_core_overlay,
        )

        apply_public_core_overlay(core)
        self._wait_http(core, core_ip, "/api/health", expected=200, timeout=120)
        from yleum_orchestrator.services.machine_business_config import (
            apply_core_config,
            boundary_source,
        )

        stage("verify_config")
        business_config_path = self.parts(state)[0].path.parent / "business-config.json"
        business_config = (
            json.loads(business_config_path.read_text(encoding="utf-8"))
            if business_config_path.exists() else None
        )
        if business_config_override is not None:
            business_config = {
                "project_id": str(state.project_id), "owner_id": str(state.owner_id),
                "config": business_config_override,
            }
        if business_config is not None:
            if (business_config["project_id"] != str(state.project_id)
                    or business_config["owner_id"] != str(state.owner_id)):
                raise CellResourceError("MAX configuration ownership mismatch")
            apply_core_config(core, core_ip, business_config["config"])
        if public_mode:
            stage("verify_auth")
            # A health/legal page does not compile Next's lazy auth/API modules.
            # Exercise the real rejection paths before publishing/reusing ingress;
            # empty launch data cannot create a session or write a user record.
            self._wait_http(
                core, core_ip, "/api/max/session",
                expected=401 if (runtime_env or {}).get("MAX_BOT_TOKEN") else 503,
                timeout=120,
                method="POST", body=b'{"initData":""}', attempt_timeout=30,
            )
            self._wait_http(
                core, core_ip, "/api/omnia/actions", expected=401, timeout=120,
                attempt_timeout=30,
            )
        stage("gateway")
        if transition.get("candidate") is not None:
            retired_name = backend.stem + "-max-core-retired"
            retired = backend._lookup(client.containers, retired_name, "managed-max-core")
            if retired is not None:
                retired.remove(force=True)
            if transition.get("old") is not None:
                transition["old"].rename(retired_name)
                transition["renamed_old"] = True
            core.rename(core_name)
        gateway_name = backend.stem + "-gateway"
        config = {
            "secret": secret, "project_id": str(state.project_id), "epoch": epoch,
            "core_host": core_ip, "machine_host": backend.address(),
            "routes": [route.model_dump() for route in manifest.routes],
            **({
                "public_mode": True,
                "public_origin": (runtime_env or {}).get("OMNIA_PUBLIC_APP_ORIGIN", ""),
            } if public_mode else {
                "owner_origins": studio_origins(),
                "preview_origin": nginx_writer.dev_url(
                    CellResourceNames.for_workspace(state.workspace_id).draft_preview_slug()
                ),
            }),
        }
        runtime_stamp = self.root / (
            "public-boundary-runtime" if public_mode else "owner-boundary-runtime"
        ) / f"{state.workspace_id}.json"
        # The guard image is only a pinned bootstrap. Always run the current
        # controller-owned boundary source so owner previews receive the same
        # framing/auth policy as public and configured gateways.
        wire_config: dict[str, Any] = {"config": config, "server": boundary_source()}
        # Reconcile trusted code updates as well as configuration changes. Reusing
        # a healthy old gateway must not strand already-published apps on old auth.
        runtime_digest = hashlib.sha256(
            json.dumps({
                "wire": wire_config, "source": boundary_source(), "core_image": image_tag,
                "guard_image": backend.guard_image, "public_mode": public_mode,
                "owner_id": str(state.owner_id), "workspace_id": str(state.workspace_id),
            }, sort_keys=True).encode(),
        ).hexdigest()
        old = backend._lookup(client.containers, gateway_name, "max-gateway")
        if runtime_stamp.is_symlink():
            raise CellResourceError("unsafe boundary state")
        if old is not None and runtime_stamp.is_file():
            stamp = json.loads(runtime_stamp.read_text(encoding="utf-8"))
            identity = backend.trusted_container_identity(old, "max-gateway")
            if (identity is not None and stamp.get("digest") == runtime_digest
                    and stamp.get("gateway") == identity):
                address = old.attrs["NetworkSettings"]["Networks"][names.internal_network][
                    "IPAddress"
                ]
                try:
                    self._wait_http(old, address, "/__omnia/identity", expected=401, timeout=30)
                except CellResourceError:
                    # Matching metadata cannot certify an unhealthy gateway.
                    # Forget its receipt before replacing only this owned
                    # container; budget exhaustion/cancellation still propagate.
                    write_controller_json(runtime_stamp, {})
                else:
                    return
        transition["switch_started"] = True
        if old is not None:
            old.remove(force=True)
        gateway = client.containers.create(
            backend.guard_image,
            ["python3", "-c", "import os,time,runpy; "
             "p='/run/omnia-boundary/server.py'; "
             "exec('while not os.path.isfile(p): time.sleep(0.1)'); "
             "runpy.run_path(p,run_name='__main__')"],
            name=gateway_name,
            labels=backend.labels("max-gateway"),
            detach=True,
            network=names.internal_network,
            user="0:0",
            cap_drop=["ALL"],
            privileged=False,
            security_opt=["no-new-privileges:true"],
            read_only=True,
            tmpfs={"/run/omnia-boundary": "rw,noexec,nosuid,nodev,size=1m,mode=0700"},
            mem_limit=32 * 1024**2,
            memswap_limit=32 * 1024**2,
            # P13/P17: the gateway needs ~1.2 CPU-seconds to start (Python + the
            # seeded boundary server). At its steady 5% of a core that was ~25 s of
            # pure throttling on every publication and preview wake — measured on
            # production (nr_throttled 222/249 periods). It starts with a full core
            # and is lowered to the steady quota once it answers; memory, pids and
            # every isolation option stay as they were.
            cpu_period=_GATEWAY_CPU_PERIOD,
            cpu_quota=_GATEWAY_BOOST_QUOTA,
            pids_limit=32,
        )
        gateway.start()
        # Runtime tmpfs is invisible to Docker29/containerd archive APIs. Send
        # secrets through exec stdin and atomically publish inside the tmpfs;
        # neither image/rootfs nor Docker command arguments contain this config.
        # Existing pinned guard images stay unchanged. Seed only trusted
        # controller code into gateway tmpfs; no project executable input.
        script = (
            "import os,sys,json; v=json.load(sys.stdin); "
            "open('/run/omnia-boundary/config.json','w').write(json.dumps(v['config'])); "
            "p='/run/omnia-boundary/.server'; open(p,'w').write(v['server']); "
            "os.replace(p,'/run/omnia-boundary/server.py')"
        )
        execution = client.api.exec_create(gateway.id, ["python3", "-c", script], stdin=True)
        connection = client.api.exec_start(execution["Id"], socket=True)
        try:
            connection._sock.settimeout(machine_remaining_seconds(15))
            connection._sock.sendall(json.dumps(wire_config).encode())
            connection._sock.shutdown(socket.SHUT_WR)
            # Drain completion, not a fire-and-forget half-written secret file.
            while connection._sock.recv(4096):
                connection._sock.settimeout(machine_remaining_seconds(15))
        finally:
            close_controller_socket(connection)
        outcome = client.api.exec_inspect(execution["Id"])
        if outcome.get("Running") or outcome.get("ExitCode") != 0:
            raise CellResourceError("trusted preview configuration was not applied")
        gateway.reload()
        gateway_ip = gateway.attrs["NetworkSettings"]["Networks"][names.internal_network][
            "IPAddress"
        ]
        self._wait_http(gateway, gateway_ip, "/__omnia/identity", expected=401, timeout=30)
        try:
            # Back to the steady quota before its identity is recorded, so the
            # receipt describes the container as it will keep running.
            gateway.update(cpu_period=_GATEWAY_CPU_PERIOD, cpu_quota=_GATEWAY_STEADY_QUOTA)
        except (docker.errors.APIError, OSError):
            # A gateway left with its start-up quota still serves correctly.
            pass
        identity = backend.trusted_container_identity(gateway, "max-gateway")
        if identity is not None:
            write_controller_json(runtime_stamp, {"digest": runtime_digest, "gateway": identity})

    @staticmethod
    def _wait_http(
        container: Any, address: str, path: str, *, expected: int, timeout: int,
        method: str = "GET", body: bytes | None = None, attempt_timeout: int = 3,
    ) -> None:
        import http.client

        deadline = time.monotonic() + machine_remaining_seconds(timeout)
        while time.monotonic() < deadline:
            container.reload()
            if container.status != "running":
                raise CellResourceError("trusted preview service stopped during startup")
            if time.monotonic() >= deadline:
                break
            connection = http.client.HTTPConnection(
                address,
                3000,
                timeout=machine_remaining_seconds(
                    min(attempt_timeout, deadline - time.monotonic()),
                ),
            )
            try:
                connection.request(
                    method, path, body=body,
                    headers={"Content-Type": "application/json"} if body is not None else {},
                )
                response = connection.getresponse()
                response.read(4096)
                if response.status == expected:
                    return
            except (OSError, http.client.HTTPException):
                pass
            finally:
                connection.close()
            time.sleep(max(0, min(0.2, deadline - time.monotonic())))
        machine_remaining_seconds(timeout)
        raise CellResourceError("trusted preview HTTP readiness is unverified")

    def preview(self, state: Any) -> tuple[str, str] | None:
        if not self.exists(state.workspace_id):
            return None
        _machine, backend = self.parts(state)
        gateway = backend._lookup(
            backend.client.containers, backend.stem + "-gateway", "max-gateway"
        )
        if gateway is None:
            return None
        gateway.reload()
        address = (
            gateway.attrs["NetworkSettings"]["Networks"]
            .get(backend.internal_network, {})
            .get("IPAddress", "")
        )
        if gateway.status == "running":
            # Generation release can retain ingress, but its generated process
            # and dedicated database are physically removed. Owner-start must
            # resume them before treating that gateway as a working preview.
            for suffix, kind in (("dev", "development"), ("project-postgres", "project-postgres")):
                product = backend._lookup(
                    backend.client.containers, backend.stem + "-" + suffix, kind,
                )
                if product is None:
                    return "stopped", address
                product.reload()
                if product.status != "running":
                    return "stopped", address
            core = backend._lookup(
                backend.client.containers, backend.stem + "-max-core", "managed-max-core",
            )
            if core is None:
                return "stopped", address
            core.reload()
            preview_image = getattr(self.settings, "cell_preview_core_image", "")
            if preview_image:
                preview_image = backend.client.images.get(preview_image).id
            env = dict(item.split("=", 1)
                       for item in core.attrs.get("Config", {}).get("Env", []) if "=" in item)
            if (core.status != "running"
                    or env.get("OMNIA_OWNER_PREVIEW") != "1"
                    or env.get("OMNIA_PUBLIC_APP_ORIGIN")
                    or (preview_image and core.attrs.get("Image") != preview_image)):
                return "stopped", address
        return gateway.status, address

    async def logs(self, state: Any) -> str:
        if not self.exists(state.workspace_id):
            return ""
        _machine, backend = self.parts(state)
        tails = []
        for name, record in list(backend._metadata().get("services", {}).items())[-12:]:
            tail = await machine_effect(backend._read_log, record["log"])
            tails.append(f"[{name}] {tail}")
        return "\n".join(tails)[-24000:]

    async def halt(
        self, state: Any, *, remove_network: bool = False, capture: bool = True,
        retain_trusted: bool = False,
    ) -> None:
        if not self.exists(state.workspace_id):
            return
        machine, backend = self.parts(state)
        await machine_effect(backend.invalidate_retained_preview)
        recovery = self.recovery_required(state)
        had_machine = capture and not recovery and backend._container() is not None
        reference = None
        await machine_effect(backend._reconcile_recovery_helpers)
        if capture and not recovery:
            reference = await self.checkpoint(state)
        if capture and recovery:
            # Pause a failed environment without certifying or discarding its
            # stopped rootfs. Explicit checkpoint restoration is the recovery path.
            await machine_effect(backend.stop)
        else:
            await machine_effect(backend.remove)
        if retain_trusted and had_machine and reference is not None and not remove_network:
            # Only successful release may retain controller-owned services. The
            # receipt proves product death and no attachment to captured data;
            # an incomplete proof takes the ordinary full teardown path.
            if await machine_effect(
                backend.record_retained_preview, reference, epoch=machine.state()["epoch"],
                retain_trusted=True,
            ):
                return
        for suffix, kind in (
            ("gateway", "max-gateway"),
            ("max-core", "managed-max-core"),
            ("guard", "namespace-guard"),
            ("proxy", "egress-proxy"),
        ):
            container = backend._lookup(
                backend.client.containers, backend.stem + "-" + suffix, kind
            )
            if container is not None:
                await machine_effect(container.remove, force=True)
        if remove_network:
            network = backend._lookup(
                backend.client.networks, backend.stem + "-public", "public-egress"
            )
            if network is not None:
                await machine_effect(network.remove)
        if had_machine and reference is not None:
            await machine_effect(
                backend.record_retained_preview, reference, epoch=machine.state()["epoch"]
            )

    async def apply_owner_business_config(
        self, state: Any, *, version: int, config: dict[str, Any],
    ) -> bool:
        machine, backend = self.parts(state)
        path = machine.path.parent / "business-config.json"
        previous = json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
        if previous is not None and (
            previous["project_id"] != str(state.project_id)
            or previous["owner_id"] != str(state.owner_id)
        ):
            raise CellResourceError("MAX configuration ownership mismatch")
        if previous is not None and (
            previous["version"] > version
            or (previous["version"] == version and previous["config"] != config)
        ):
            raise CellResourceError("stale MAX configuration version")
        preview = self.preview(state)
        if (previous is not None and previous["config"] == config
                and previous.get("applied") is True and preview and preview[0] == "running"):
            # Repeated save must not bounce a working gateway.
            if previous["version"] != version:
                write_controller_json(path, {**previous, "version": version})
            return True
        manifest = MachineManifest.model_validate(machine.state()["manifest"])
        # Persist desired metadata before any runtime effect. Retrying or waking
        # replays the same data. No project source, DB or environment restore.
        write_controller_json(path, {
            "project_id": str(state.project_id), "owner_id": str(state.owner_id),
            "version": version, "config": config,
        })
        # Metadata must not allocate a sleeping project's CPU/memory envelope.
        # The normal preview/generation wake replays this controller-owned file.
        if preview is None or preview[0] != "running":
            return False
        await machine_effect(
            self._start_boundary, state, manifest, backend, machine.state()["epoch"],
        )
        write_controller_json(path, {
            "project_id": str(state.project_id), "owner_id": str(state.owner_id),
            "version": version, "config": config, "applied": True,
        })
        return True

    async def resume_preview(self, state: Any, *, epoch: int | None = None) -> None:
        machine, backend = self.parts(state)
        saved = machine.state()
        manifest = MachineManifest.model_validate(saved["manifest"])
        metadata = backend._metadata()
        if backend._container() is None and metadata.get("environment_ref"):
            reference = MachineEnvironmentRef.model_validate(metadata["environment_ref"])
            retained = await machine_effect(
                backend.consume_retained_preview, reference, epoch=saved["epoch"]
            )
            if not retained:
                store = MachineEnvironmentStore(
                    self.root / "artifacts", state.workspace_id, backend,
                    max_bytes=backend.disk_bytes,
                )
                # The checkpoint may predate the latest writes. A live project
                # database is the business record; only an explicit checkpoint
                # restoration may replace it.
                live_database = backend._lookup(
                    backend.client.volumes, backend.project_postgres_volume, "project-volume"
                )
                await machine_effect(
                    store.restore, reference, manifest_digest=reference.manifest_digest,
                    preserve_volumes=frozenset(
                        {backend.project_postgres_volume} if live_database is not None else ()
                    ),
                )
        runtime_epoch = epoch or saved["epoch"]
        await machine_effect(backend.ensure, manifest, runtime_epoch)
        for name in manifest.service_order():
            service = next(item for item in manifest.services if item.name == name)
            await machine_effect(backend.start_service, service, runtime_epoch)
            status = await machine_effect(
                backend.service_status, service, runtime_epoch, include_logs=False,
            )
            if not status["ready"]:
                raise CellResourceError(f"service {name} did not become ready after recreation")
        saved["epoch"] = runtime_epoch
        write_controller_json(machine.path, saved)
        await machine_effect(self._start_boundary, state, manifest, backend, runtime_epoch)
        if machine.state() != saved:
            raise CellIdentityConflict("preview resume machine state changed before readiness")
        completed = dict(saved)
        completed["ready_epoch"] = runtime_epoch
        write_controller_json(machine.path, completed)


def serving_resume_epochs(adapter: Any, state: Any) -> tuple[int | None, int | None]:
    """(serving epoch the cell expects, epoch the retained machine record holds).

    A wake after a host reboot advances the cell's fencing epoch, but nothing
    re-attaches the retained machine: its record stays on the old epoch and a
    later restoration refuses the source with «serving machine epoch is
    detached» (core, 25.09.2026, every cell woken after the outage). Every
    resume that is not itself driven by an explicit lifecycle mutation must
    therefore resume at the serving epoch, and re-attach even a running
    machine whose record lags behind it.
    """
    from yleum_orchestrator.services.restoration_binding import serving_fencing_epoch

    parts = getattr(adapter, "parts", None)
    if parts is None:
        return None, None
    try:
        machine, _backend = parts(state)
        machine_state = machine.state()
        epoch = machine_state.get("epoch")
        serving = serving_fencing_epoch(state, machine_state=machine_state)
    except Exception:
        # A machine or cell state we cannot read is resumed exactly as before
        # (no explicit epoch); the caller never fails because of this hint.
        return None, None
    return (
        serving if isinstance(serving, int) else None,
        epoch if isinstance(epoch, int) else None,
    )
