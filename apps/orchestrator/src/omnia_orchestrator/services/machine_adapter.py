"""Reachable portable Cell adapter behind the existing owner/lease/source checks."""

from __future__ import annotations

import asyncio
import hashlib
import json
import socket
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast
from uuid import UUID, uuid4, uuid5

from omnia_orchestrator.core.cell_resources import (
    CellFenceRejected,
    CellResourceError,
    LifecycleMutation,
)
from omnia_orchestrator.core.project_machine import MachineManifest
from omnia_orchestrator.core.stack_registry import get_stack
from omnia_orchestrator.services.cell_state import CellCredentialStore
from omnia_orchestrator.services.docker_cell_resources import DockerCommandResult
from omnia_orchestrator.services.docker_machine_backend import DockerMachineBackend
from omnia_orchestrator.services.machine_environment import (
    MachineEnvironmentRef,
    MachineEnvironmentStore,
)
from omnia_orchestrator.services.machine_services import MachineServiceFailed, MachineServices
from omnia_orchestrator.services.project_machine import (
    MachineOperationResult,
    ProjectMachine,
    machine_budget,
    machine_effect,
    machine_remaining_seconds,
    write_controller_json,
)

MACHINE_APPLY_TIMEOUT_SECONDS = 900
MACHINE_APPLY_CLEANUP_RESERVE_SECONDS = 30
_PROJECT_POSTGRES_MIN_MEMORY_BYTES = 128 * 1024**2
_PROJECT_POSTGRES_TARGET_MEMORY_BYTES = 256 * 1024**2
_PROJECT_POSTGRES_MIN_CPU_CORES = 0.1
_PROJECT_POSTGRES_TARGET_CPU_CORES = 0.15


class MachineAdapter:
    def __init__(self, manager: Any, settings: Any) -> None:
        self.manager = manager
        self.settings = settings

    @property
    def root(self) -> Path:
        return Path(self.manager.state_store.root).parent / "project-machines"

    def capabilities(self) -> dict[str, object]:
        return {
            "portable_machine": True,
            "manifest_path": ".omnia/cell.json",
            "public_package_egress": True,
            "persistent_environment": True,
            "managed_max_boundary": True,
            "dedicated_postgres": True,
            "database_url_env": "DATABASE_URL",
            "database_admin": "full",
            "commands": ["bash", "build", "runtime_check"],
            "task_roles": ["bootstrap", "fast_check", "full_build"],
            "framework": "nextjs",
            "default_stack": "max-nextjs-typescript",
            "node_major": 22,
            "package_manager": "pnpm@9.15.0",
            "dependency_policy": "project-controlled",
        }

    def validate_available(self) -> None:
        from omnia_orchestrator.services.docker_machine_backend import _PIN

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
        heartbeat_seconds = int(
            getattr(self.settings, "cell_machine_command_heartbeat_seconds", 15)
        )
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
                await self._activate_runtime(state, manifest, request)
            except MachineServiceFailed as exc:
                # A failed product start is command evidence, not a transport
                # rejection. Finish the durable request and retain task logs.
                # finish() keeps the last 24k characters; bound this payload
                # first so successful task logs cannot displace the diagnosis.
                failure = str(exc)[:4000]
                task_tail = "\n".join(output)[-19000:]
                return await finish(exit_code=1, output=f"{failure}\n{task_tail}")
        return await finish(exit_code=0, output="\n".join(output))

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

    async def checkpoint(self, state: Any) -> MachineEnvironmentRef | None:
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
        reference = await machine_effect(
            store.capture,
            manifest_digest=manifest.digest(),
            base_image=backend.base_image,
            volumes=backend.snapshot_volume_names(manifest),
            manifest=manifest,
        )
        metadata = backend._metadata()
        metadata.update(
            environment_ref=reference.model_dump(mode="json"), restored_image=reference.image_id
        )
        write_controller_json(backend.metadata_path, metadata)
        return cast(MachineEnvironmentRef, reference)

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
        from omnia_orchestrator.schemas.workspace import WorkspaceAgentExecRequest

        result = await self.execute(
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

    def _start_boundary(
        self, state: Any, manifest: MachineManifest, backend: DockerMachineBackend, epoch: int
    ) -> None:
        client = backend.client
        names = state.resource_names
        secret = self.secret(state.workspace_id)
        core_name = backend.stem + "-max-core"
        core = backend._lookup(client.containers, core_name, "managed-max-core")
        if core is None:
            credentials = self.manager.credential_store.load_or_create(state.workspace_id)
            core = client.containers.create(
                get_stack("max-miniapp-nextjs").image_tag,
                name=core_name,
                labels=backend.labels("managed-max-core"),
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
                    "DATABASE_URL": f"postgresql://postgres:{credentials.postgres_password}"
                    f"@{names.postgres_container}:5432/postgres",
                    "REDIS_URL": f"redis://{names.redis_container}:6379/0",
                },
                mem_limit=self._max_core_memory_bytes(),
                memswap_limit=self._max_core_memory_bytes(),
                nano_cpus=int(self._max_core_cpu_cores() * 1_000_000_000),
                pids_limit=256,
            )
            # Only the immutable managed core can call its fixed platform API.
            # Generated project code still has the namespace DROP guard.
            backend._network(backend.stem + "-public", internal=False).connect(core)
        core.reload()
        if core.status != "running":
            core.start()
        core.reload()
        core_ip = core.attrs["NetworkSettings"]["Networks"][names.internal_network]["IPAddress"]
        self._wait_http(core, core_ip, "/api/health", expected=200, timeout=120)
        from omnia_orchestrator.services.machine_business_config import (
            apply_core_config,
            boundary_source,
        )

        business_config_path = self.parts(state)[0].path.parent / "business-config.json"
        business_config = (
            json.loads(business_config_path.read_text(encoding="utf-8"))
            if business_config_path.exists() else None
        )
        if business_config is not None:
            if (business_config["project_id"] != str(state.project_id)
                    or business_config["owner_id"] != str(state.owner_id)):
                raise CellResourceError("MAX configuration ownership mismatch")
            apply_core_config(core, core_ip, business_config["config"])
        gateway_name = backend.stem + "-gateway"
        old = backend._lookup(client.containers, gateway_name, "max-gateway")
        if old is not None:
            old.remove(force=True)
        gateway = client.containers.create(
            backend.guard_image,
            (["python3", "-c", "import os,time,runpy; "
              "p='/run/omnia-boundary/server.py'; "
              "exec('while not os.path.isfile(p): time.sleep(0.1)'); "
              "runpy.run_path(p,run_name='__main__')"]
             if business_config is not None else ["python3", "/opt/omnia/machine_boundary.py"]),
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
            nano_cpus=50_000_000,
            pids_limit=32,
        )
        gateway.start()
        config = {
            "secret": secret,
            "project_id": str(state.project_id),
            "epoch": epoch,
            "core_host": core_ip,
            "machine_host": backend.address(),
            "routes": [route.model_dump() for route in manifest.routes],
        }
        # Runtime tmpfs is invisible to Docker29/containerd archive APIs. Send
        # secrets through exec stdin and atomically publish inside the tmpfs;
        # neither image/rootfs nor Docker command arguments contain this config.
        script = (
            "import os,sys; data=sys.stdin.buffer.read(); "
            "p='/run/omnia-boundary/.next'; open(p,'wb').write(data); "
            "os.replace(p,'/run/omnia-boundary/config.json')"
        )
        wire_config: dict[str, Any] = config
        if business_config is not None:
            # Existing pinned guard images stay unchanged. Seed only trusted
            # controller code into gateway tmpfs; no project executable input.
            wire_config = {"config": config, "server": boundary_source()}
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
            connection.close()
        outcome = client.api.exec_inspect(execution["Id"])
        if outcome.get("Running") or outcome.get("ExitCode") != 0:
            raise CellResourceError("trusted preview configuration was not applied")
        gateway.reload()
        gateway_ip = gateway.attrs["NetworkSettings"]["Networks"][names.internal_network][
            "IPAddress"
        ]
        self._wait_http(gateway, gateway_ip, "/__omnia/identity", expected=401, timeout=30)

    @staticmethod
    def _wait_http(container: Any, address: str, path: str, *, expected: int, timeout: int) -> None:
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
                timeout=machine_remaining_seconds(min(3, deadline - time.monotonic())),
            )
            try:
                connection.request("GET", path)
                response = connection.getresponse()
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

    async def halt(self, state: Any, *, remove_network: bool = False, capture: bool = True) -> None:
        if not self.exists(state.workspace_id):
            return
        _machine, backend = self.parts(state)
        recovery = self.recovery_required(state)
        await machine_effect(backend._reconcile_recovery_helpers)
        if capture and not recovery:
            await self.checkpoint(state)
        if capture and recovery:
            # Pause a failed environment without certifying or discarding its
            # stopped rootfs. Explicit checkpoint restoration is the recovery path.
            await machine_effect(backend.stop)
        else:
            await machine_effect(backend.remove)
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

    async def apply_owner_business_config(
        self, state: Any, *, version: int, config: dict[str, Any],
    ) -> None:
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
            return
        manifest = MachineManifest.model_validate(machine.state()["manifest"])
        # Persist desired metadata before any runtime effect. Retrying or waking
        # replays the same data. No project source, DB or environment restore.
        write_controller_json(path, {
            "project_id": str(state.project_id), "owner_id": str(state.owner_id),
            "version": version, "config": config,
        })
        await machine_effect(self._start_boundary, state, manifest, backend, state.fencing_epoch)
        write_controller_json(path, {
            "project_id": str(state.project_id), "owner_id": str(state.owner_id),
            "version": version, "config": config, "applied": True,
        })

    async def resume_preview(self, state: Any, *, epoch: int | None = None) -> None:
        machine, backend = self.parts(state)
        saved = machine.state()
        manifest = MachineManifest.model_validate(saved["manifest"])
        metadata = backend._metadata()
        if backend._container() is None and metadata.get("environment_ref"):
            reference = MachineEnvironmentRef.model_validate(metadata["environment_ref"])
            store = MachineEnvironmentStore(
                self.root / "artifacts", state.workspace_id, backend, max_bytes=backend.disk_bytes
            )
            await machine_effect(
                store.restore, reference, manifest_digest=reference.manifest_digest
            )
        runtime_epoch = epoch or saved["epoch"]
        await machine_effect(backend.ensure, manifest, runtime_epoch)
        for name in manifest.service_order():
            service = next(item for item in manifest.services if item.name == name)
            await machine_effect(backend.start_service, service, runtime_epoch)
            status = await machine_effect(backend.service_status, service, runtime_epoch)
            if not status["ready"]:
                raise CellResourceError(f"service {name} did not become ready after recreation")
        saved["epoch"] = runtime_epoch
        write_controller_json(machine.path, saved)
        await machine_effect(self._start_boundary, state, manifest, backend, runtime_epoch)
