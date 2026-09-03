"""Reachable portable Cell adapter behind the existing owner/lease/source checks."""

from __future__ import annotations

import asyncio
import hashlib
import json
import socket
import time
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4, uuid5

from omnia_orchestrator.core.cell_resources import CellResourceError, LifecycleMutation
from omnia_orchestrator.core.project_machine import MachineManifest
from omnia_orchestrator.core.stack_registry import get_stack
from omnia_orchestrator.services.cell_state import CellCredentialStore
from omnia_orchestrator.services.docker_cell_resources import DockerCommandResult
from omnia_orchestrator.services.docker_machine_backend import DockerMachineBackend
from omnia_orchestrator.services.machine_environment import (
    MachineEnvironmentRef,
    MachineEnvironmentStore,
)
from omnia_orchestrator.services.machine_services import MachineServices
from omnia_orchestrator.services.project_machine import (
    ProjectMachine,
    machine_budget,
    machine_effect,
    machine_remaining_seconds,
    write_controller_json,
)

MACHINE_APPLY_TIMEOUT_SECONDS = 900
MACHINE_APPLY_CLEANUP_RESERVE_SECONDS = 30


class MachineAdapter:
    def __init__(self, manager: Any, settings: Any) -> None:
        self.manager = manager
        self.settings = settings

    @property
    def root(self) -> Path:
        return self.manager.state_store.root.parent / "project-machines"

    def capabilities(self) -> dict[str, object]:
        return {
            "portable_machine": True,
            "manifest_path": ".omnia/cell.json",
            "public_package_egress": True,
            "persistent_environment": True,
            "managed_max_boundary": True,
            "commands": ["bash", "build", "runtime_check"],
            "task_roles": ["bootstrap", "build", "test"],
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
            or not self.settings.cell_machine_denied_cidrs
            or not self.settings.cell_network_pool
        ):
            raise CellResourceError(
                "portable machine images, public host denies and pool are required"
            )
        client = self.manager.docker._client_obj()
        for image in (
            self.settings.cell_machine_base_image,
            self.settings.cell_machine_guard_image,
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
            network_pool=self.settings.cell_network_pool,
            denied_cidrs=tuple(self.settings.cell_machine_denied_cidrs),
            # Proxy64 + guard32 + gateway32 MiB and .2 CPU stay inside the
            # previously reserved executor slice. Managed core uses draft slice.
            cpu_cores=profile.executor_cpu_cores - 0.2,
            memory_bytes=profile.executor_memory_bytes - 128 * 1024**2,
            disk_bytes=profile.required_free_disk_bytes,
            pids=512,
            namespace=self.manager.namespace,
        )

        def epoch():
            current = self.manager.state_store.load(state.workspace_id)
            if current is None or current.active_generation_run_id is None:
                return None
            return current.active_generation_fencing_epoch

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

    async def execute(
        self, state: Any, manifest: MachineManifest, request: Any
    ) -> DockerCommandResult:
        try:
            with machine_budget(request.timeout_seconds):
                async with asyncio.timeout(machine_remaining_seconds(request.timeout_seconds)):
                    return await self._execute(state, manifest, request)
        except TimeoutError:
            machine, _backend = self.parts(state)
            with machine_budget(None):
                await machine.cancel(
                    LifecycleMutation(
                        request.operation_id, request.fencing_epoch, manifest.digest()
                    )
                )
            return DockerCommandResult(
                exit_code=124, output="request budget exhausted; machine fenced", timed_out=True
            )

    async def _execute(
        self, state: Any, manifest: MachineManifest, request: Any
    ) -> DockerCommandResult:
        request_deadline = time.monotonic() + machine_remaining_seconds(request.timeout_seconds)
        role = request.task_role
        if role == "build" and not any(task.role == "test" for task in manifest.tasks):
            raise ValueError("portable build requires a declared test task")
        machine, _backend = self.parts(state)
        digest = hashlib.sha256(
            json.dumps(
                {"cmd": request.cmd, "role": role, "revision": request.expected_revision},
                sort_keys=True,
            ).encode()
        ).hexdigest()
        mutation = LifecycleMutation(request.operation_id, request.fencing_epoch, digest)
        await machine.ensure(manifest, mutation)
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
        output = []
        for name, argv, cwd, timeout in commands:
            operation_mutation = LifecycleMutation(
                uuid5(request.operation_id, name), mutation.fencing_epoch, digest
            )
            if time.monotonic() >= request_deadline:
                with machine_budget(None):
                    await machine.cancel(operation_mutation)
                return DockerCommandResult(
                    exit_code=124, output="request budget exhausted; machine fenced", timed_out=True
                )
            deadline = min(request_deadline, time.monotonic() + timeout)
            operation = await machine.exec_start(argv, cwd, operation_mutation)
            while True:
                result = await machine.exec_status(operation, operation_mutation)
                if time.monotonic() >= deadline:
                    with machine_budget(None):
                        await machine.cancel(operation_mutation)
                    return DockerCommandResult(
                        exit_code=124,
                        output="\n".join(output)
                        + f"\n{name} timed out; machine fenced and stopped",
                        timed_out=True,
                    )
                if result.state == "completed":
                    output.append(f"[{name}] {result.output}")
                    if result.exit_code != 0:
                        return DockerCommandResult(
                            exit_code=result.exit_code or 1,
                            output="\n".join(output),
                            timed_out=False,
                        )
                    break
                await asyncio.sleep(0.2)
        return DockerCommandResult(exit_code=0, output="\n".join(output), timed_out=False)

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
            volumes=tuple(backend.volume_mapping(manifest)),
            manifest=manifest,
        )
        metadata = backend._metadata()
        metadata.update(
            environment_ref=reference.model_dump(mode="json"), restored_image=reference.image_id
        )
        write_controller_json(backend.metadata_path, metadata)
        return reference

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
                task_role="build",
                operation_id=uuid4(),
                timeout_seconds=900,
            ),
        )
        if result.exit_code:
            return result
        await self.checkpoint(state)
        machine, backend = self.parts(state)
        mutation = LifecycleMutation(uuid4(), request.fencing_epoch, manifest.digest())
        await machine.ensure(manifest, mutation)
        await MachineServices(machine, backend).reconcile(manifest, mutation)
        await machine_effect(self._start_boundary, state, manifest, backend, request.fencing_epoch)
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
                mem_limit=self.manager.profile.draft_memory_bytes,
                memswap_limit=self.manager.profile.draft_memory_bytes,
                nano_cpus=int(self.manager.profile.draft_cpu_cores * 1_000_000_000),
                pids_limit=256,
                environment={
                    "NODE_ENV": "development",
                    "AUTH_SECRET": secret,
                    "OMNIA_PROJECT_ID": str(state.project_id),
                    "DATABASE_URL": f"postgresql://postgres:{credentials.postgres_password}"
                    f"@{names.postgres_container}:5432/postgres",
                    "REDIS_URL": f"redis://{names.redis_container}:6379/0",
                },
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
        gateway_name = backend.stem + "-gateway"
        old = backend._lookup(client.containers, gateway_name, "max-gateway")
        if old is not None:
            old.remove(force=True)
        gateway = client.containers.create(
            backend.guard_image,
            ["python3", "/opt/omnia/machine_boundary.py"],
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
        execution = client.api.exec_create(gateway.id, ["python3", "-c", script], stdin=True)
        connection = client.api.exec_start(execution["Id"], socket=True)
        try:
            connection._sock.settimeout(machine_remaining_seconds(15))
            connection._sock.sendall(json.dumps(config).encode())
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
    def _wait_http(container, address: str, path: str, *, expected: int, timeout: int) -> None:
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
