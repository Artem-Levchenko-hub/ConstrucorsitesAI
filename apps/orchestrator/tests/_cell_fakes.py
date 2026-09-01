from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from uuid import UUID

from omnia_orchestrator.services.docker_cell_resources import (
    CellInventorySnapshot,
    DockerCommandResult,
    DockerContainerRecord,
    DockerContainerSpec,
    DockerNetworkRecord,
    DockerVolumeRecord,
)


class SimulatedProcessCrash(RuntimeError):
    pass


class FakeDockerBackend:
    def __init__(self) -> None:
        self.begin_operation_calls = 0
        self.operation_ids: list[UUID] = []
        self.counter = 0
        self.base_url = "unix:///var/run/docker.sock"
        self.api = type("API", (), {"base_url": "unix:///var/run/docker.sock"})()
        self.docker_root_dir = "/daemon-disk"
        self.volumes: dict[str, DockerVolumeRecord] = {}
        self.networks: dict[str, DockerNetworkRecord] = {}
        self.containers: dict[str, DockerContainerRecord] = {}
        self.container_history: list[DockerContainerRecord] = []
        self.removed_resources: list[str] = []
        self.crash_after: str | None = None
        self.block_next_side_effect = False
        self.block_on_phase: str | None = None
        self.block_on_phase_hit = 1
        self._blocked = asyncio.Event()
        self._release = asyncio.Event()
        self._phase_counts: dict[str, int] = {}
        self.fail_after_workspace_extract = False
        self.rollback_completed = False
        self.postgres_smoke_calls = 0
        self.postgres_smoke_fail_on_calls: set[int] = set()
        self.created_refs: list[str] = []
        self.finalized_paths: list[str] = []
        self.remaining_tmp_paths: list[str] = []
        self.workspace_command_calls: list[dict[str, object]] = []
        self.workspace_command_result = DockerCommandResult(exit_code=0, output="ok")
        self.workspace_command_volume_files: dict[str, bytes] | None = None

    def info(self) -> dict[str, object]:
        return {
            "ID": "local-test-daemon",
            "DockerRootDir": self.docker_root_dir,
            "Name": "local-test",
            "OperatingSystem": "Linux",
        }

    async def wait_until_blocked(self) -> None:
        await asyncio.wait_for(self._blocked.wait(), timeout=2)

    def release_block(self) -> None:
        self._release.set()

    async def begin_operation(self, operation_id: UUID) -> None:
        self.begin_operation_calls += 1
        self.operation_ids.append(operation_id)

    async def get_volume(self, name: str) -> DockerVolumeRecord | None:
        return self.volumes.get(name)

    async def create_volume(self, name: str, labels: dict[str, str]) -> DockerVolumeRecord:
        await self._before_mutation("volume_create")
        record = DockerVolumeRecord(
            resource_id=self._next_id("volume"),
            name=name,
            labels=dict(labels),
            files={},
        )
        self.volumes[name] = record
        return record

    async def remove_volume(self, name: str) -> None:
        record = self.volumes.pop(name, None)
        if record is not None:
            self.removed_resources.append(record.resource_id)

    async def list_workspace_volumes(self, workspace_id: UUID) -> list[DockerVolumeRecord]:
        return [
            item
            for item in self.volumes.values()
            if item.labels.get("omnia.workspace_id") == str(workspace_id)
        ]

    async def read_volume_files(self, name: str) -> dict[str, bytes]:
        record = self.volumes.get(name)
        return {} if record is None else dict(record.files)

    async def write_volume_files(self, name: str, files: dict[str, bytes]) -> None:
        await self._before_mutation("volume_write")
        record = self.volumes[name]
        new_files = dict(record.files)
        for path, payload in files.items():
            new_files[path] = payload
        self.volumes[name] = replace(record, files=new_files)

    async def delete_volume_paths(self, name: str, paths: tuple[str, ...]) -> None:
        record = self.volumes[name]
        new_files = dict(record.files)
        for path in paths:
            new_files.pop(path, None)
        self.volumes[name] = replace(record, files=new_files)

    async def promote_volume_directory(
        self,
        name: str,
        staging_prefix: str,
        final_prefix: str,
    ) -> None:
        record = self.volumes[name]
        files = dict(record.files)
        moved: dict[str, bytes] = {}
        for path, payload in list(files.items()):
            if path.startswith(f"{staging_prefix}/"):
                moved[f"{final_prefix}/{path.removeprefix(f'{staging_prefix}/')}"] = payload
                del files[path]
        files.update(moved)
        self.volumes[name] = replace(record, files=files)
        self.created_refs.append(staging_prefix)
        self.finalized_paths.append(final_prefix)
        self.remaining_tmp_paths = sorted(path for path in files if ".tmp-" in path)

    async def clear_volume(self, name: str) -> None:
        await self._before_mutation("volume_clear")
        record = self.volumes[name]
        self.volumes[name] = replace(record, files={})

    async def get_network(self, name: str) -> DockerNetworkRecord | None:
        return self.networks.get(name)

    async def create_network(
        self,
        name: str,
        labels: dict[str, str],
        *,
        internal: bool,
    ) -> DockerNetworkRecord:
        await self._before_mutation("network_create")
        record = DockerNetworkRecord(
            resource_id=self._next_id("network"),
            name=name,
            labels=dict(labels),
            internal=internal,
        )
        self.networks[name] = record
        return record

    async def remove_network(self, name: str) -> None:
        record = self.networks.pop(name, None)
        if record is not None:
            self.removed_resources.append(record.resource_id)

    async def list_workspace_networks(self, workspace_id: UUID) -> list[DockerNetworkRecord]:
        return [
            item
            for item in self.networks.values()
            if item.labels.get("omnia.workspace_id") == str(workspace_id)
        ]

    async def get_container(self, name: str) -> DockerContainerRecord | None:
        return self.containers.get(name)

    async def create_container(self, spec: DockerContainerSpec) -> DockerContainerRecord:
        await self._before_mutation("container_create")
        actual_tmpfs = set(spec.tmpfs)
        if spec.labels.get("omnia.resource_kind", "").startswith("postgres"):
            actual_tmpfs.update({"/var/run/postgresql", "/var/lib/postgresql/data"})
        if (
            spec.labels.get("omnia.resource_kind") == "postgres-init"
            and spec.volumes
        ):
            postgres_volume = self.volumes[spec.volumes[0]]
            postgres_volume.files["PGDATA/PG_VERSION"] = b"16\n"
        record = DockerContainerRecord(
            resource_id=self._next_id("container"),
            name=spec.name,
            image=spec.image,
            labels=dict(spec.labels),
            user=spec.user,
            cap_add=list(spec.cap_add),
            cap_drop=list(spec.cap_drop),
            read_only=spec.read_only,
            privileged=spec.privileged,
            security_opt=list(spec.security_opt),
            ports=dict(spec.ports),
            env=dict(spec.env),
            volumes=tuple(spec.volumes),
            mounts=tuple(spec.mounts),
            network_names=tuple(spec.network_names),
            state="exited" if spec.helper else "created",
            helper=spec.helper,
            tmpfs=tuple(sorted(actual_tmpfs)),
            pids_limit=spec.pids_limit,
            memory_limit_bytes=spec.memory_limit_bytes,
            cpu_quota=spec.cpu_quota,
        )
        self.containers[spec.name] = record
        self.container_history.append(record)
        return record

    async def start_container(self, name: str) -> DockerContainerRecord:
        await self._before_mutation("container_start")
        record = self.containers[name]
        updated = replace(record, state="running")
        self.containers[name] = updated
        self.container_history.append(updated)
        return updated

    async def stop_container(self, name: str) -> None:
        record = self.containers.get(name)
        if record is None:
            return
        await self._before_mutation("container_stop")
        updated = replace(record, state="exited")
        self.containers[name] = updated
        self.container_history.append(updated)

    async def remove_container(self, name: str) -> None:
        record = self.containers.pop(name, None)
        if record is None:
            return
        self.removed_resources.append(record.resource_id)
        updated = replace(record, removed_in_finally=True, state="removed")
        self.container_history.append(updated)

    async def list_workspace_containers(self, workspace_id: UUID) -> list[DockerContainerRecord]:
        return [
            item
            for item in self.containers.values()
            if item.labels.get("omnia.workspace_id") == str(workspace_id)
        ]

    async def postgres_dump(self, container_name: str, password: str) -> bytes:
        _ = password
        record = self.containers.get(container_name)
        if record is None:
            raise RuntimeError("postgres container missing")
        volume_name = record.volumes[0]
        files = self.volumes[volume_name].files
        rows = json.loads(files.get("db.json", b"[]").decode("utf-8"))
        return json.dumps(rows, sort_keys=True).encode("utf-8")

    async def postgres_restore(self, container_name: str, dump: bytes, password: str) -> None:
        _ = password
        if self.fail_after_workspace_extract:
            self.fail_after_workspace_extract = False
            raise RuntimeError("restore failure injected")
        record = self.containers.get(container_name)
        if record is None:
            raise RuntimeError("postgres container missing")
        rows = json.loads(dump.decode("utf-8"))
        volume_name = record.volumes[0]
        await self.write_volume_files(volume_name, {"db.json": json.dumps(rows).encode("utf-8")})
        self.rollback_completed = True

    async def postgres_smoke_query(self, container_name: str, password: str) -> bool:
        _ = password
        self.postgres_smoke_calls += 1
        if self.postgres_smoke_calls in self.postgres_smoke_fail_on_calls:
            return False
        return container_name in self.containers

    async def run_workspace_command(
        self,
        *,
        workspace_volume_name: str,
        agent_home_volume_name: str,
        labels: dict[str, str],
        image: str,
        command: str,
        internal_network_name: str,
        egress_network_name: str,
        environment: dict[str, str],
        timeout_seconds: int,
    ) -> DockerCommandResult:
        self.workspace_command_calls.append(
            {
                "workspace_volume_name": workspace_volume_name,
                "agent_home_volume_name": agent_home_volume_name,
                "labels": dict(labels),
                "image": image,
                "command": command,
                "internal_network_name": internal_network_name,
                "egress_network_name": egress_network_name,
                "environment": dict(environment),
                "timeout_seconds": timeout_seconds,
            }
        )
        if self.workspace_command_volume_files is not None:
            record = self.volumes[workspace_volume_name]
            self.volumes[workspace_volume_name] = replace(
                record,
                files=dict(self.workspace_command_volume_files),
            )
        return self.workspace_command_result

    def seed_volume(
        self, name: str, labels: dict[str, str], *, files: dict[str, bytes] | None = None
    ) -> None:
        self.counter += 1
        self.volumes[name] = DockerVolumeRecord(
            resource_id=f"seed-volume-{self.counter}",
            name=name,
            labels=dict(labels),
            files=dict(files or {}),
        )

    def last_container(self, kind: str) -> DockerContainerRecord:
        for record in reversed(self.container_history):
            if record.labels.get("omnia.resource_kind") == kind:
                return record
        raise AssertionError(f"missing container kind {kind}")

    def inventory_for_workspace(self, workspace_id: UUID) -> CellInventorySnapshot:
        retained = tuple(
            item.name
            for item in self.volumes.values()
            if item.labels.get("omnia.workspace_id") == str(workspace_id)
            and item.labels.get("omnia.resource_kind")
            in {"workspace", "agent-home", "postgres", "redis", "checkpoints"}
        )
        helper_ids = tuple(
            item.resource_id
            for item in self.containers.values()
            if item.labels.get("omnia.workspace_id") == str(workspace_id) and item.helper
        )
        secret_ids = tuple(
            item.resource_id
            for item in self.volumes.values()
            if item.labels.get("omnia.workspace_id") == str(workspace_id)
            and item.labels.get("omnia.resource_kind") == "secret-staging"
        )
        env_matches: list[str] = []
        for item in self.containers.values():
            if item.labels.get("omnia.workspace_id") != str(workspace_id) or item.helper:
                continue
            if any("password" in value.casefold() for value in item.env.values()):
                env_matches.append(item.name)
        return CellInventorySnapshot(
            retained_volume_names=retained,
            helper_container_ids=helper_ids,
            secret_staging_volume_ids=secret_ids,
            persistent_container_env_secret_matches=tuple(env_matches),
        )

    async def _before_mutation(self, phase: str) -> None:
        phase_count = self._phase_counts.get(phase, 0) + 1
        self._phase_counts[phase] = phase_count
        if self.block_next_side_effect:
            self.block_next_side_effect = False
            self._blocked.set()
            await self._release.wait()
            self._release = asyncio.Event()
            self._blocked = asyncio.Event()
        if self.block_on_phase == phase and phase_count == self.block_on_phase_hit:
            self._blocked.set()
            await self._release.wait()
            self._release = asyncio.Event()
            self._blocked = asyncio.Event()
        if self.crash_after == phase:
            raise SimulatedProcessCrash(phase)

    def _next_id(self, prefix: str) -> str:
        self.counter += 1
        return f"{prefix}-{self.counter}"
