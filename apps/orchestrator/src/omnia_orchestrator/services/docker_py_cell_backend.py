"""docker-py adapter for Project Cell resource management."""

from __future__ import annotations

import asyncio
import contextlib
import io
import posixpath
import shlex
import tarfile
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from types import SimpleNamespace
from typing import Any, cast
from uuid import UUID, uuid4

import docker  # type: ignore[import-untyped]
import requests

from omnia_orchestrator.core.cell_resources import CellIdentityConflict, CellResourceError
from omnia_orchestrator.core.workspace_provider import WorkspaceProviderUnavailable
from omnia_orchestrator.services.docker_cell_resources import (
    DockerCommandResult,
    DockerContainerRecord,
    DockerContainerSpec,
    DockerNetworkRecord,
    DockerVolumeRecord,
)

_VOLUME_ROOT = "/volume"
_POSTGRES_ROOT = "/var/lib/postgresql"
_POSTGRES_DATA = "/var/lib/postgresql/PGDATA"
_POSTGRES_PASSWORD_FILE = "/run/secrets/postgres-password.txt"
_POSTGRES_CELL_HBA_RULE = "host all all samenet scram-sha-256"
_REDIS_DATA = "/data"
_WORKSPACE_SOURCE = "/workspace-src"
_WORKSPACE_RUN_ROOT = "/work"
_AGENT_HOME_ROOT = "/root"
# A file helper must not run forever if its owning orchestrator process dies.
# This exceeds the SDK's per-call timeout; it is not an agent execution limit.
_HELPER_SLEEP = ["sleep", "300"]
_HELPER_TMPFS = {
    "/tmp": "rw,nosuid,nodev,noexec,size=32m",
    "/run": "rw,nosuid,nodev,noexec,size=16m",
}
_DEFAULT_TMPFS = {
    "/tmp": "rw,nosuid,nodev,noexec,size=64m",
    "/run": "rw,nosuid,nodev,noexec,size=16m",
}
_POSTGRES_TMPFS = {
    **_DEFAULT_TMPFS,
    "/var/run/postgresql": "rw,nosuid,nodev,noexec,size=16m",
    # Mask the image-declared VOLUME. PGDATA lives in the explicitly managed
    # retained volume at /var/lib/postgresql/PGDATA; without this override
    # Docker creates an unlabeled anonymous volume for every cell.
    "/var/lib/postgresql/data": "rw,nosuid,nodev,noexec,size=1m",
}
_CPU_PERIOD = 100_000
_HELPER_MEMORY_LIMIT_BYTES = 256 * 1024 * 1024
_HELPER_PIDS_LIMIT = 64
_ARCHIVE_LIMIT_BYTES = 128 * 1024 * 1024
_WORKSPACE_SOURCE_ARCHIVE_PATH = "/tmp/workspace-source.tar"
_ONE_SHOT_HELPERS = frozenset({"postgres-ownership", "postgres-init"})
_WORKSPACE_SYNC_PRESERVE_PATTERNS = (
    "node_modules",
    ".next",
    ".pnpm-store",
    ".git",
    "dist",
    "build",
    ".venv",
    "vendor",
    "__pycache__",
    ".env",
    ".env.*",
    "secrets.json",
    "secrets.yaml",
    "secrets.yml",
)
_WORKSPACE_SYNC_EXCLUDES = (
    *(f"./{item}" for item in _WORKSPACE_SYNC_PRESERVE_PATTERNS),
    "*/.env",
    "*/.env.*",
    "*/secrets.json",
    "*/secrets.yaml",
    "*/secrets.yml",
)
_WORKSPACE_SOURCE_ARCHIVE_EXCLUDES = tuple(
    dict.fromkeys(
        (
            *(f"./{item}" for item in _WORKSPACE_SYNC_PRESERVE_PATTERNS),
            *(f"*/{item}" for item in _WORKSPACE_SYNC_PRESERVE_PATTERNS),
        )
    )
)


def _normalize_volume_path(path: str) -> str:
    candidate = PurePosixPath(str(path))
    normalized = posixpath.normpath(candidate.as_posix()).lstrip("/")
    normalized_path = PurePosixPath(normalized)
    if (
        normalized in {"", "."}
        or normalized_path.is_absolute()
        or ".." in normalized_path.parts
    ):
        raise CellResourceError(f"invalid volume path: {path}")
    return normalized_path.as_posix()


def _read_archive_bytes(
    chunks: Iterable[bytes | bytearray],
    *,
    label: str,
    max_bytes: int = _ARCHIVE_LIMIT_BYTES,
) -> bytes:
    parts: list[bytes] = []
    total = 0
    for chunk in chunks:
        if not isinstance(chunk, (bytes, bytearray)):
            raise CellResourceError(f"{label} returned invalid archive chunk")
        data = bytes(chunk)
        total += len(data)
        if total > max_bytes:
            raise CellResourceError(f"{label} archive exceeds {max_bytes} bytes")
        parts.append(data)
    return b"".join(parts)


def _archive_to_files(raw: bytes, *, root_prefix: str) -> dict[str, bytes]:
    result: dict[str, bytes] = {}
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:*") as archive:
        for member in archive.getmembers():
            if member.isfile() is False:
                continue
            normalized = posixpath.normpath(member.name).lstrip("/")
            parts = [part for part in normalized.split("/") if part not in {"", "."}]
            if parts and parts[0] == root_prefix:
                parts = parts[1:]
            if not parts or any(part == ".." for part in parts):
                continue
            payload = archive.extractfile(member)
            if payload is None:
                continue
            result["/".join(parts)] = payload.read()
    return result


def _files_to_archive(files: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as archive:
        seen_dirs: set[str] = set()
        for raw_path, payload in sorted(files.items()):
            normalized = _normalize_volume_path(raw_path)
            parts = normalized.split("/")
            for index in range(1, len(parts)):
                directory = "/".join(parts[:index])
                if directory in seen_dirs:
                    continue
                info = tarfile.TarInfo(name=directory)
                info.type = tarfile.DIRTYPE
                info.mode = 0o755
                info.mtime = 0
                archive.addfile(info)
                seen_dirs.add(directory)
            info = tarfile.TarInfo(name=normalized)
            info.size = len(payload)
            info.mode = 0o644
            info.mtime = 0
            archive.addfile(info, io.BytesIO(payload))
    return buffer.getvalue()


@dataclass(slots=True)
class DockerPyCellBackend:
    docker_host: str
    helper_image: str
    client_factory: Callable[[str], Any] | None = None
    archive_limit_bytes: int = _ARCHIVE_LIMIT_BYTES
    exec_memory_limit_bytes: int = 1024 * 1024 * 1024
    exec_cpu_cores: float = 0.5
    network_pool: str = ""
    base_url: str = field(init=False)
    api: Any = field(init=False, repr=False)
    _client: Any | None = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        if self.exec_memory_limit_bytes <= 0 or self.exec_cpu_cores < 0.01:
            raise ValueError("project cell executor requires a positive reserved resource budget")
        self.base_url = self.docker_host
        self.api = SimpleNamespace(base_url=self.docker_host)

    async def begin_operation(self, operation_id: UUID) -> None:
        _ = operation_id
        await asyncio.to_thread(self._ping_client)

    async def get_volume(self, name: str) -> DockerVolumeRecord | None:
        """Inspect identity without mounting or archiving a live application volume."""
        volume = await self._get_volume_obj(name)
        if volume is None:
            return None
        return DockerVolumeRecord(
            resource_id=self._resource_id(volume),
            name=name,
            labels=self._labels(volume),
            files={},
        )

    async def create_volume(self, name: str, labels: dict[str, str]) -> DockerVolumeRecord:
        self._require_identity_labels(labels)

        def _create() -> Any:
            return self._client_obj().volumes.create(name=name, driver="local", labels=labels)

        volume = await asyncio.to_thread(_create)
        return DockerVolumeRecord(
            resource_id=self._resource_id(volume),
            name=name,
            labels=dict(labels),
            files={},
        )

    async def remove_volume(self, name: str) -> None:
        volume = await self._get_volume_obj(name)
        if volume is None:
            return

        def _remove() -> None:
            volume.remove(force=True)

        await asyncio.to_thread(_remove)

    async def list_workspace_volumes(self, workspace_id: UUID) -> list[DockerVolumeRecord]:
        volumes = await asyncio.to_thread(
            lambda: self._client_obj().volumes.list(
                filters={"label": f"omnia.workspace_id={workspace_id}"}
            )
        )
        return [
            DockerVolumeRecord(
                resource_id=self._resource_id(volume),
                name=str(getattr(volume, "name", "")),
                labels=self._labels(volume),
                files={},
            )
            for volume in volumes
        ]

    async def read_volume_files(self, name: str) -> dict[str, bytes]:
        volume = await self._get_volume_obj(name)
        if volume is None:
            return {}
        labels = self._labels(volume)
        container = await self._start_helper_container(
            name=self._helper_name("volume-read", name),
            labels=self._helper_labels(labels, "volume-read"),
            volumes={name: {"bind": _VOLUME_ROOT, "mode": "ro"}},
        )
        try:
            raw = await asyncio.to_thread(self._get_archive_bytes, container, _VOLUME_ROOT, name)
            return _archive_to_files(raw, root_prefix=PurePosixPath(_VOLUME_ROOT).name)
        finally:
            await self._remove_container_object(container)

    async def read_workspace_source_files(self, name: str) -> dict[str, bytes]:
        """Read bounded project source without package/build/cache payloads."""
        volume = await self._get_volume_obj(name)
        if volume is None:
            return {}
        labels = self._labels(volume)
        source_tmpfs = dict(_HELPER_TMPFS)
        source_tmpfs["/tmp"] = (
            f"rw,nosuid,nodev,noexec,size={self.archive_limit_bytes + 1024 * 1024}"
        )
        container = await self._start_helper_container(
            name=self._helper_name("workspace-source-read", name),
            labels=self._helper_labels(labels, "workspace-source-read"),
            volumes={name: {"bind": _VOLUME_ROOT, "mode": "ro"}},
            tmpfs=source_tmpfs,
        )
        try:
            archive_command = [
                "tar",
                "-C",
                _VOLUME_ROOT,
                *(f"--exclude={item}" for item in _WORKSPACE_SOURCE_ARCHIVE_EXCLUDES),
                "-cf",
                _WORKSPACE_SOURCE_ARCHIVE_PATH,
                ".",
            ]
            await asyncio.to_thread(
                self._exec_checked,
                container,
                archive_command,
                f"archive workspace source {name}",
            )
            raw_size = await asyncio.to_thread(
                self._exec_checked,
                container,
                ["stat", "-c", "%s", _WORKSPACE_SOURCE_ARCHIVE_PATH],
                f"stat workspace source archive {name}",
            )
            try:
                archive_size = int(raw_size.strip())
            except ValueError as exc:
                raise CellResourceError("workspace source archive returned invalid size") from exc
            if archive_size > self.archive_limit_bytes:
                raise CellResourceError(
                    f"{name} source archive exceeds {self.archive_limit_bytes} bytes"
                )
            raw = await asyncio.to_thread(
                self._exec_checked,
                container,
                ["cat", _WORKSPACE_SOURCE_ARCHIVE_PATH],
                f"read workspace source archive {name}",
            )
            if len(raw) != archive_size:
                raise CellResourceError("workspace source archive size changed during read")
            return _archive_to_files(raw, root_prefix="")
        finally:
            await self._remove_container_object(container)

    async def write_volume_files(self, name: str, files: dict[str, bytes]) -> None:
        if not files:
            return
        volume = await self._get_volume_obj(name)
        if volume is None:
            raise CellResourceError(f"missing volume: {name}")
        labels = self._labels(volume)
        container = await self._start_helper_container(
            name=self._helper_name("volume-write", name),
            labels=self._helper_labels(labels, "volume-write"),
            volumes={name: {"bind": _VOLUME_ROOT, "mode": "rw"}},
        )
        try:
            archive = _files_to_archive(files)

            def _put() -> bool:
                return bool(container.put_archive(path=_VOLUME_ROOT, data=archive))

            ok = await asyncio.to_thread(_put)
            if ok is False:
                raise CellResourceError(f"put_archive returned False for {name}")
        finally:
            await self._remove_container_object(container)

    async def delete_volume_paths(self, name: str, paths: tuple[str, ...]) -> None:
        if not paths:
            return
        normalized = tuple(_normalize_volume_path(path) for path in paths)
        volume = await self._get_volume_obj(name)
        if volume is None:
            raise CellResourceError(f"missing volume: {name}")
        labels = self._labels(volume)
        container = await self._start_helper_container(
            name=self._helper_name("volume-delete", name),
            labels=self._helper_labels(labels, "volume-delete"),
            volumes={name: {"bind": _VOLUME_ROOT, "mode": "rw"}},
        )
        try:
            joined = " ".join(shlex.quote(path) for path in normalized)
            command = ["sh", "-eu", "-c", f"cd {_VOLUME_ROOT} && rm -rf -- {joined}"]
            await asyncio.to_thread(
                self._exec_checked,
                container,
                command,
                f"delete volume paths {name}",
            )
        finally:
            await self._remove_container_object(container)

    async def promote_volume_directory(
        self,
        name: str,
        staging_prefix: str,
        final_prefix: str,
    ) -> None:
        stage = _normalize_volume_path(staging_prefix)
        final = _normalize_volume_path(final_prefix)
        volume = await self._get_volume_obj(name)
        if volume is None:
            raise CellResourceError(f"missing volume: {name}")
        labels = self._labels(volume)
        container = await self._start_helper_container(
            name=self._helper_name("volume-promote", name),
            labels=self._helper_labels(labels, "volume-promote"),
            volumes={name: {"bind": _VOLUME_ROOT, "mode": "rw"}},
        )
        try:
            parent = posixpath.dirname(final) or "."
            script = " && ".join(
                [
                    f"cd {_VOLUME_ROOT}",
                    f"test -e {shlex.quote(stage)}",
                    f"test ! -e {shlex.quote(final)}",
                    f"mkdir -p -- {shlex.quote(parent)}",
                    f"mv -- {shlex.quote(stage)} {shlex.quote(final)}",
                ]
            )
            await asyncio.to_thread(
                self._exec_checked,
                container,
                ["sh", "-eu", "-c", script],
                f"promote volume directory {name}",
            )
        finally:
            await self._remove_container_object(container)

    async def clear_volume(self, name: str) -> None:
        volume = await self._get_volume_obj(name)
        if volume is None:
            raise CellResourceError(f"missing volume: {name}")
        labels = self._labels(volume)
        container = await self._start_helper_container(
            name=self._helper_name("volume-clear", name),
            labels=self._helper_labels(labels, "volume-clear"),
            volumes={name: {"bind": _VOLUME_ROOT, "mode": "rw"}},
        )
        try:
            await asyncio.to_thread(
                self._exec_checked,
                container,
                ["sh", "-eu", "-c", f"find {_VOLUME_ROOT} -mindepth 1 -exec rm -rf -- {{}} \\;"],
                f"clear volume {name}",
            )
        finally:
            await self._remove_container_object(container)

    async def get_network(self, name: str) -> DockerNetworkRecord | None:
        network = await self._get_network_obj(name)
        if network is None:
            return None
        return DockerNetworkRecord(
            resource_id=self._resource_id(network),
            name=name,
            labels=self._labels(network),
            internal=bool((getattr(network, "attrs", {}) or {}).get("Internal", False)),
        )

    async def create_network(
        self,
        name: str,
        labels: dict[str, str],
        *,
        internal: bool,
    ) -> DockerNetworkRecord:
        self._require_identity_labels(labels)

        def _create() -> Any:
            if self.network_pool:
                from omnia_orchestrator.services.machine_network_allocation import (
                    create_pool_network,
                )

                return create_pool_network(
                    self._client_obj(),
                    self.network_pool,
                    name,
                    driver="bridge",
                    check_duplicate=True,
                    internal=internal,
                    labels=labels,
                )
            return self._client_obj().networks.create(
                name,
                driver="bridge",
                check_duplicate=True,
                internal=internal,
                labels=labels,
            )

        network = await asyncio.to_thread(_create)
        return DockerNetworkRecord(
            resource_id=self._resource_id(network),
            name=name,
            labels=dict(labels),
            internal=internal,
        )

    async def remove_network(self, name: str) -> None:
        network = await self._get_network_obj(name)
        if network is None:
            return
        await asyncio.to_thread(network.remove)

    async def list_workspace_networks(self, workspace_id: UUID) -> list[DockerNetworkRecord]:
        networks = await asyncio.to_thread(
            lambda: self._client_obj().networks.list(
                filters={"label": f"omnia.workspace_id={workspace_id}"}
            )
        )
        return [
            DockerNetworkRecord(
                resource_id=self._resource_id(network),
                name=str(getattr(network, "name", "")),
                labels=self._labels(network),
                internal=bool((getattr(network, "attrs", {}) or {}).get("Internal", False)),
            )
            for network in networks
        ]

    async def get_container(self, name: str) -> DockerContainerRecord | None:
        container = await self._get_container_obj(name)
        if container is None:
            return None
        return self._container_record(container)

    async def read_container_logs(self, name: str, *, tail: int = 200) -> str:
        container = await self._get_container_obj(name)
        if container is None:
            return ""
        logs = await asyncio.to_thread(container.logs, tail=tail)
        if isinstance(logs, (bytes, bytearray)):
            return bytes(logs).decode("utf-8", "ignore")
        if logs is None:
            return ""
        return str(logs)

    async def create_container(self, spec: DockerContainerSpec) -> DockerContainerRecord:
        self._validate_container_spec(spec)
        labels = dict(spec.labels)
        if spec.helper:
            labels["omnia.helper"] = "true"
        kind = labels["omnia.resource_kind"]
        volumes = self._container_volumes(spec)
        environment = self._container_environment(spec)
        command = self._container_command(spec)
        tmpfs = self._container_tmpfs(spec)
        kwargs: dict[str, object] = {
            "name": spec.name,
            "detach": True,
            "labels": labels,
            "user": spec.user,
            "cap_add": list(spec.cap_add),
            "cap_drop": list(spec.cap_drop),
            "read_only": spec.read_only,
            "privileged": spec.privileged,
            "security_opt": list(spec.security_opt),
            "ports": self._container_ports(spec),
            "environment": environment,
            "volumes": volumes,
            "tmpfs": tmpfs,
            "pids_limit": spec.pids_limit,
        }
        if spec.memory_limit_bytes > 0:
            kwargs["mem_limit"] = spec.memory_limit_bytes
        if spec.cpu_quota > 0:
            kwargs["cpu_period"] = _CPU_PERIOD
            kwargs["cpu_quota"] = max(1, int(spec.cpu_quota * _CPU_PERIOD))
        if spec.network_names:
            kwargs["network"] = spec.network_names[0]
        else:
            kwargs["network"] = "none"

        def _create() -> Any:
            return self._client_obj().containers.create(spec.image, command=command, **kwargs)

        container = await asyncio.to_thread(_create)
        for network_name in spec.network_names[1:]:
            def _connect(current: str = network_name) -> None:
                self._client_obj().networks.get(current).connect(container)

            await asyncio.to_thread(_connect)
        if kind in _ONE_SHOT_HELPERS:
            await self.start_container(spec.name)
            await asyncio.to_thread(
                self._wait_checked, container, f"run helper {spec.name}", timeout_seconds=30
            )
            if hasattr(container, "reload"):
                await asyncio.to_thread(container.reload)
        return self._container_record(container)

    async def start_container(self, name: str) -> DockerContainerRecord:
        container = await self._require_container_obj(name)

        def _start() -> None:
            current = str(getattr(container, "status", ""))
            if current == "paused" and hasattr(container, "unpause"):
                container.unpause()
                return
            if current != "running":
                container.start()
            if hasattr(container, "reload"):
                container.reload()

        await asyncio.to_thread(_start)
        return self._container_record(container)

    async def stop_container(self, name: str) -> None:
        container = await self._get_container_obj(name)
        if container is None:
            return
        await asyncio.to_thread(lambda: container.stop(timeout=10))

    async def remove_container(self, name: str) -> None:
        container = await self._get_container_obj(name)
        if container is None:
            return
        await self._remove_container_object(container)

    async def list_workspace_containers(self, workspace_id: UUID) -> list[DockerContainerRecord]:
        containers = await asyncio.to_thread(
            lambda: self._client_obj().containers.list(
                all=True,
                filters={"label": f"omnia.workspace_id={workspace_id}"},
            )
        )
        return [self._container_record(container) for container in containers]

    async def postgres_dump(self, container_name: str, password: str) -> bytes:
        container = await self._require_container_obj(container_name)
        env = {"PGPASSWORD": password}
        payload = await asyncio.to_thread(
            self._exec_checked,
            container,
            ["pg_dump", "-Fc", "-U", "postgres", "-d", "postgres"],
            f"postgres dump {container_name}",
            env,
        )
        if not payload:
            raise CellResourceError("postgres dump returned an empty payload")
        if len(payload) > self.archive_limit_bytes:
            raise CellResourceError(
                f"postgres dump exceeds {self.archive_limit_bytes} bytes"
            )
        return payload

    async def postgres_restore(self, container_name: str, dump: bytes, password: str) -> None:
        container = await self._require_container_obj(container_name)
        dump_name = "project-cell.dump"
        # Docker's archive API writes below tmpfs mounts, so a file uploaded to
        # /tmp is hidden from exec inside the container. The maintenance helper
        # has a short-lived writable rootfs specifically for this restore file.
        dump_path = f"/{dump_name}"
        env = {"PGPASSWORD": password}
        archive = _files_to_archive({dump_name: dump})
        try:
            ok = await asyncio.to_thread(
                lambda: bool(container.put_archive(path="/", data=archive))
            )
            if ok is False:
                raise CellResourceError("postgres restore archive upload failed")
            await asyncio.to_thread(
                self._exec_checked,
                container,
                [
                    "pg_restore",
                    "--clean",
                    "--if-exists",
                    "-U",
                    "postgres",
                    "-d",
                    "postgres",
                    dump_path,
                ],
                f"postgres restore {container_name}",
                env,
            )
        finally:
            await asyncio.to_thread(
                self._exec_best_effort,
                container,
                ["rm", "-f", dump_path],
                env,
            )

    async def postgres_smoke_query(self, container_name: str, password: str) -> bool:
        container = await self._get_container_obj(container_name)
        if container is None:
            return False
        result = await asyncio.to_thread(
            lambda: container.exec_run(
                ["psql", "-U", "postgres", "-d", "postgres", "-tAc", "select 1"],
                environment={"PGPASSWORD": password},
                demux=False,
            )
        )
        exit_code, output = self._exec_result_parts(result)
        return exit_code == 0 and output.strip() == b"1"

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
        if not command.strip():
            raise CellResourceError("workspace command is empty")
        if timeout_seconds <= 0:
            raise CellResourceError("workspace command timeout must be positive")
        self._require_identity_labels(labels)
        if await self._get_volume_obj(workspace_volume_name) is None:
            raise CellResourceError(f"missing volume: {workspace_volume_name}")
        if await self._get_volume_obj(agent_home_volume_name) is None:
            raise CellResourceError(f"missing volume: {agent_home_volume_name}")
        if await self._get_network_obj(internal_network_name) is None:
            raise CellResourceError(f"missing network: {internal_network_name}")
        if await self._get_network_obj(egress_network_name) is None:
            raise CellResourceError(f"missing network: {egress_network_name}")
        merged_labels = self._helper_labels(labels, "agent-exec")
        container_name = self._helper_name("agent-exec", workspace_volume_name)
        existing = await self._get_container_obj(container_name)
        if existing is not None:
            if self._labels_match(self._labels(existing), merged_labels) is False:
                raise CellIdentityConflict("helper container identity mismatch")
            await self._remove_container_object(existing)
        sync_excludes = " ".join(
            f"--exclude={shlex.quote(item)}" for item in _WORKSPACE_SYNC_EXCLUDES
        )
        preserve_case = "|".join(_WORKSPACE_SYNC_PRESERVE_PATTERNS)
        script = "\n".join(
            [
                "set -eu",
                "clear_sync_targets() {",
                f"  mkdir -p {_WORKSPACE_SOURCE}",
                (
                    f"  for path in {_WORKSPACE_SOURCE}/* {_WORKSPACE_SOURCE}/.[!.]* "
                    f"{_WORKSPACE_SOURCE}/..?*; do"
                ),
                '    [ -e "$path" ] || continue',
                '    name="${path##*/}"',
                f'    case "$name" in {preserve_case}) continue ;; esac',
                '    rm -rf -- "$path"',
                "  done",
                "}",
                "sync_back() {",
                "  clear_sync_targets",
                (
                    f"  tar -C {_WORKSPACE_RUN_ROOT} {sync_excludes} -cf - . "
                    f"| tar -C {_WORKSPACE_SOURCE} -xf -"
                ),
                "}",
                "term_handler() {",
                "  set +e",
                "  sync_back",
                "  exit 124",
                "}",
                "trap 'term_handler' TERM INT",
                f"mkdir -p {_WORKSPACE_RUN_ROOT} {_WORKSPACE_SOURCE}",
                f"cp -a {_WORKSPACE_SOURCE}/. {_WORKSPACE_RUN_ROOT}/ 2>/dev/null || true",
                (
                    f"if [ ! -e {_WORKSPACE_RUN_ROOT}/node_modules ] "
                    f"&& [ -e /app/node_modules ]; then "
                    f"ln -s /app/node_modules {_WORKSPACE_RUN_ROOT}/node_modules; fi"
                ),
                f"cd {_WORKSPACE_RUN_ROOT}",
                "status=0",
                'sh -lc "$OMNIA_CELL_CMD" || status=$?',
                "sync_back",
                'exit "$status"',
            ]
        )
        container = await asyncio.to_thread(
            self._client_obj().containers.create,
            image,
            ["sh", "-eu", "-c", script],
            name=container_name,
            detach=True,
            labels=merged_labels,
            user="0:0",
            cap_add=[],
            cap_drop=["ALL"],
            read_only=False,
            privileged=False,
            security_opt=["no-new-privileges:true"],
            ports={},
            environment={"OMNIA_CELL_CMD": command, **environment},
            volumes={
                workspace_volume_name: {"bind": _WORKSPACE_SOURCE, "mode": "rw"},
                agent_home_volume_name: {"bind": _AGENT_HOME_ROOT, "mode": "rw"},
            },
            tmpfs=dict(_DEFAULT_TMPFS),
            pids_limit=_HELPER_PIDS_LIMIT,
            mem_limit=self.exec_memory_limit_bytes,
            memswap_limit=self.exec_memory_limit_bytes,
            cpu_period=_CPU_PERIOD,
            cpu_quota=int(self.exec_cpu_cores * _CPU_PERIOD),
            network=internal_network_name,
        )
        timed_out = False
        exit_code = 0
        output = ""
        try:
            await asyncio.to_thread(
                self._client_obj().networks.get(egress_network_name).connect,
                container,
            )
            await asyncio.to_thread(container.start)
            if hasattr(container, "reload"):
                await asyncio.to_thread(container.reload)
            try:
                exit_code = await asyncio.to_thread(
                    self._wait_status,
                    container,
                    f"run workspace command {container_name}",
                    timeout_seconds=timeout_seconds,
                )
            except requests.exceptions.ReadTimeout:
                timed_out = True
                with contextlib.suppress(Exception):
                    await asyncio.to_thread(container.stop, timeout=10)
                exit_code = await asyncio.to_thread(
                    self._wait_status,
                    container,
                    f"stop workspace command {container_name}",
                    timeout_seconds=15,
                )
            logs = await asyncio.to_thread(container.logs, tail=500)
            if isinstance(logs, (bytes, bytearray)):
                output = bytes(logs).decode("utf-8", "ignore")
            elif logs is not None:
                output = str(logs)
        finally:
            await self._remove_container_object(container)
        return DockerCommandResult(
            exit_code=124 if timed_out and exit_code == 0 else exit_code,
            output=output,
            timed_out=timed_out,
        )

    def info(self) -> dict[str, object]:
        return cast(dict[str, object], self._client_obj().info())

    def _client_obj(self) -> Any:
        if self._client is None:
            factory = self.client_factory or (lambda host: docker.DockerClient(base_url=host))
            try:
                self._client = factory(self.docker_host)
            except Exception as exc:  # pragma: no cover - trivial wrapper
                raise WorkspaceProviderUnavailable(f"cannot reach docker daemon: {exc}") from exc
        return self._client

    def _ping_client(self) -> None:
        try:
            self._client_obj().ping()
        except (docker.errors.DockerException, requests.RequestException, OSError) as exc:
            raise WorkspaceProviderUnavailable(f"cannot reach docker daemon: {exc}") from exc

    async def _get_volume_obj(self, name: str) -> Any | None:
        return await asyncio.to_thread(lambda: self._lookup_named(self._client_obj().volumes, name))

    async def _get_network_obj(self, name: str) -> Any | None:
        return await asyncio.to_thread(
            lambda: self._lookup_named(self._client_obj().networks, name)
        )

    async def _get_container_obj(self, name: str) -> Any | None:
        return await asyncio.to_thread(
            lambda: self._lookup_named(self._client_obj().containers, name)
        )

    async def _require_container_obj(self, name: str) -> Any:
        container = await self._get_container_obj(name)
        if container is None:
            raise CellResourceError(f"missing container: {name}")
        return container

    @staticmethod
    def _lookup_named(collection: Any, name: str) -> Any | None:
        try:
            return collection.get(name)
        except docker.errors.NotFound:
            return None

    @staticmethod
    def _resource_id(resource: Any) -> str:
        for candidate in (
            getattr(resource, "id", None),
            getattr(resource, "short_id", None),
            getattr(resource, "name", None),
        ):
            if isinstance(candidate, str) and candidate:
                return candidate
        attrs = getattr(resource, "attrs", {}) or {}
        return str(attrs.get("Name") or attrs.get("Id") or "")

    @staticmethod
    def _labels(resource: Any) -> dict[str, str]:
        attrs = getattr(resource, "attrs", {}) or {}
        raw = attrs.get("Labels")
        if not isinstance(raw, dict):
            config = attrs.get("Config") or {}
            raw = config.get("Labels") if isinstance(config, dict) else {}
        if isinstance(raw, dict):
            return {str(key): str(value) for key, value in raw.items()}
        return {}

    def _container_record(self, container: Any) -> DockerContainerRecord:
        attrs = cast(dict[str, Any], getattr(container, "attrs", {}) or {})
        config = cast(dict[str, Any], attrs.get("Config") or {})
        host_config = cast(dict[str, Any], attrs.get("HostConfig") or {})
        mounts = cast(list[dict[str, Any]], attrs.get("Mounts") or [])
        network_settings = cast(dict[str, Any], attrs.get("NetworkSettings") or {})
        networks = cast(dict[str, Any], network_settings.get("Networks") or {})
        port_bindings = cast(dict[str, Any], host_config.get("PortBindings") or {})
        env_items = cast(list[str], config.get("Env") or [])
        cpu_quota_raw = host_config.get("CpuQuota")
        cpu_period_raw = host_config.get("CpuPeriod")
        cpu_quota = 0.0
        if (
            isinstance(cpu_quota_raw, int)
            and isinstance(cpu_period_raw, int)
            and cpu_period_raw > 0
        ):
            cpu_quota = cpu_quota_raw / cpu_period_raw
        return DockerContainerRecord(
            resource_id=self._resource_id(container),
            name=str(getattr(container, "name", "")),
            image=str(config.get("Image") or ""),
            labels=self._labels(container),
            user=str(config.get("User") or ""),
            cap_add=[str(item) for item in host_config.get("CapAdd") or []],
            cap_drop=[str(item) for item in host_config.get("CapDrop") or []],
            read_only=bool(host_config.get("ReadonlyRootfs", False)),
            privileged=bool(host_config.get("Privileged", False)),
            security_opt=[str(item) for item in host_config.get("SecurityOpt") or []],
            ports=self._normalize_port_bindings(port_bindings),
            env=self._env_dict(env_items),
            volumes=tuple(
                str(item.get("Name"))
                for item in mounts
                if item.get("Type") == "volume" and item.get("Name")
            ),
            mounts=tuple(
                str(item.get("Destination"))
                for item in mounts
                if item.get("Type") == "bind"
                and isinstance(item.get("Destination"), str)
                and item.get("Destination")
            ),
            network_names=tuple(str(name) for name in networks.keys()),
            network_ipv4={
                str(name): str(endpoint["IPAddress"])
                for name, endpoint in networks.items()
                if isinstance(endpoint, dict) and endpoint.get("IPAddress")
            },
            state=str(getattr(container, "status", "") or ""),
            helper=bool(self._labels(container).get("omnia.helper") == "true"),
            tmpfs=tuple(str(item) for item in (host_config.get("Tmpfs") or {}).keys()),
            pids_limit=int(host_config.get("PidsLimit") or 0),
            memory_limit_bytes=int(host_config.get("Memory") or 0),
            cpu_quota=cpu_quota,
        )

    @staticmethod
    def _env_dict(items: list[str]) -> dict[str, str]:
        result: dict[str, str] = {}
        for item in items:
            if "=" not in item:
                continue
            key, value = item.split("=", 1)
            result[key] = value
        return result

    def _validate_container_spec(self, spec: DockerContainerSpec) -> None:
        labels = dict(spec.labels)
        self._require_identity_labels(labels)
        kind = labels["omnia.resource_kind"]
        if not spec.name:
            raise CellResourceError("container name is required")
        if spec.privileged:
            raise CellResourceError("privileged containers are forbidden")
        if spec.ports and kind != "draft-runtime":
            raise CellResourceError("host port publication is forbidden")
        if kind == "draft-runtime":
            if set(spec.ports) != {"3000/tcp"}:
                raise CellResourceError("draft runtime requires only 3000/tcp loopback publish")
            binding = spec.ports["3000/tcp"]
            if (
                binding.startswith("127.0.0.1:") is False
                or binding.removeprefix("127.0.0.1:").isdigit() is False
            ):
                raise CellResourceError("draft runtime requires 127.0.0.1 host binding")
        if "no-new-privileges:true" not in spec.security_opt:
            raise CellResourceError("no-new-privileges is required")
        if spec.helper is False and spec.read_only is False:
            raise CellResourceError("steady containers must use read-only rootfs")
        if (
            kind in {"postgres", "redis", "postgres-init", "postgres-maintenance"}
            and spec.user == ""
        ):
            raise CellResourceError("fixed user is required")
        if kind == "postgres-ownership" and spec.cap_add != ["CHOWN"]:
            raise CellResourceError("postgres ownership helper must add only CHOWN")
        if kind != "postgres-ownership" and spec.cap_add:
            raise CellResourceError("unexpected capability add")
        if "ALL" not in spec.cap_drop:
            raise CellResourceError("cap_drop=ALL is required")

    @staticmethod
    def _require_identity_labels(labels: dict[str, str]) -> None:
        required = {
            "omnia.managed",
            "omnia.project_cell",
            "omnia.workspace_id",
            "omnia.project_id",
            "omnia.owner_id",
            "omnia.provider",
            "omnia.profile_version",
            "omnia.resource_kind",
        }
        if any(not labels.get(key) for key in required):
            missing = sorted(key for key in required if not labels.get(key))
            raise CellResourceError(f"missing identity labels: {', '.join(missing)}")
        if labels.get("omnia.provider") != "docker_owner_canary":
            raise CellResourceError("unexpected workspace provider label")

    def _container_environment(self, spec: DockerContainerSpec) -> dict[str, str]:
        kind = spec.labels["omnia.resource_kind"]
        env = dict(spec.env)
        if kind.startswith("postgres"):
            env["PGDATA"] = _POSTGRES_DATA
        return env

    def _container_command(self, spec: DockerContainerSpec) -> list[str] | None:
        kind = spec.labels["omnia.resource_kind"]
        if kind == "postgres-ownership":
            return [
                "sh",
                "-eu",
                "-c",
                f"mkdir -p {_POSTGRES_DATA} && chown -R postgres:postgres {_POSTGRES_ROOT}",
            ]
        if kind == "postgres-init":
            return [
                "sh",
                "-eu",
                "-c",
                " ".join(
                    [
                        f"test -s {shlex.quote(_POSTGRES_PASSWORD_FILE)}",
                        "&&",
                        f"mkdir -p {shlex.quote(_POSTGRES_DATA)}",
                        "&&",
                        f"if [ ! -f {shlex.quote(_POSTGRES_DATA + '/PG_VERSION')} ]; then",
                        "initdb",
                        "--username=postgres",
                        "--auth-local=scram-sha-256",
                        "--auth-host=scram-sha-256",
                        f"--pwfile={shlex.quote(_POSTGRES_PASSWORD_FILE)}",
                        f"-D {shlex.quote(_POSTGRES_DATA)}",
                        "; fi",
                        "&&",
                        "if ! grep -Fqx",
                        shlex.quote(_POSTGRES_CELL_HBA_RULE),
                        shlex.quote(_POSTGRES_DATA + "/pg_hba.conf"),
                        "; then printf '%s\\n'",
                        shlex.quote(_POSTGRES_CELL_HBA_RULE),
                        ">>",
                        shlex.quote(_POSTGRES_DATA + "/pg_hba.conf"),
                        "; fi",
                    ]
                ),
            ]
        if kind == "postgres-maintenance":
            return ["postgres", "-c", "listen_addresses="]
        if kind == "draft-runtime":
            return [
                "sh",
                "-eu",
                "-c",
                "\n".join(
                    [
                        'env_file="${OMNIA_DRAFT_ENV_FILE:-/root/.omnia/draft-env.sh}"',
                        '[ -f "$env_file" ]',
                        '. "$env_file"',
                        "sync_lockfile() {",
                        f"  mkdir -p {_WORKSPACE_SOURCE}",
                        (
                            f"  if [ -f {_WORKSPACE_RUN_ROOT}/pnpm-lock.yaml ]; then "
                            f"cp {_WORKSPACE_RUN_ROOT}/pnpm-lock.yaml "
                            f"{_WORKSPACE_SOURCE}/pnpm-lock.yaml; fi"
                        ),
                        "}",
                        "term_handler() {",
                        "  set +e",
                        "  sync_lockfile",
                        (
                            '  if [ -n "${draft_pid:-}" ]; then '
                            'kill -TERM "$draft_pid" 2>/dev/null || true; fi'
                        ),
                        (
                            '  if [ -n "${draft_pid:-}" ]; then '
                            'wait "$draft_pid" 2>/dev/null || true; fi'
                        ),
                        (
                            '  if [ -n "${sync_pid:-}" ]; then '
                            'kill -TERM "$sync_pid" 2>/dev/null || true; fi'
                        ),
                        (
                            '  if [ -n "${sync_pid:-}" ]; then '
                            'wait "$sync_pid" 2>/dev/null || true; fi'
                        ),
                        "  exit 0",
                        "}",
                        "trap 'term_handler' TERM INT",
                        f"mkdir -p {_WORKSPACE_RUN_ROOT} {_WORKSPACE_SOURCE}",
                        f"cp -a {_WORKSPACE_SOURCE}/. {_WORKSPACE_RUN_ROOT}/ 2>/dev/null || true",
                        (
                            f"if [ ! -e {_WORKSPACE_RUN_ROOT}/node_modules ] "
                            f"&& [ -e /app/node_modules ]; then "
                            f"ln -s /app/node_modules {_WORKSPACE_RUN_ROOT}/node_modules; fi"
                        ),
                        "lock_sync_loop() {",
                        "  while :; do",
                        "    sleep 2",
                        "    sync_lockfile",
                        "  done",
                        "}",
                        "lock_sync_loop &",
                        "sync_pid=$!",
                        f"cd {_WORKSPACE_RUN_ROOT}",
                        # Next 15's Turbopack rejects the immutable dependency
                        # symlink from /work to /app. Use its default Webpack
                        # dev server without changing the generated manifest
                        # or widening the cell filesystem boundary.
                        (
                            "node /app/node_modules/next/dist/bin/next dev "
                            "--port 3000 --hostname 0.0.0.0 &"
                        ),
                        "draft_pid=$!",
                        'wait "$draft_pid"',
                        "status=$?",
                        'kill -TERM "$sync_pid" 2>/dev/null || true',
                        'wait "$sync_pid" 2>/dev/null || true',
                        "sync_lockfile",
                        'exit "$status"',
                    ]
                ),
            ]
        return None

    def _container_volumes(self, spec: DockerContainerSpec) -> dict[str, dict[str, str]]:
        kind = spec.labels["omnia.resource_kind"]
        volume_names = tuple(spec.volumes)
        if kind == "postgres":
            return self._postgres_volume_mapping(volume_names)
        if kind == "postgres-ownership":
            return self._postgres_volume_mapping(volume_names)
        if kind == "postgres-maintenance":
            return self._postgres_volume_mapping(volume_names)
        if kind == "postgres-init":
            if volume_names[:1] == ():
                raise CellResourceError("postgres init helper requires postgres volume")
            if spec.mounts != (_POSTGRES_PASSWORD_FILE,):
                raise CellResourceError("postgres init helper requires mounted password file")
            mapping = self._postgres_volume_mapping((volume_names[0],))
            if len(volume_names) != 2:
                raise CellResourceError("postgres init helper requires secret staging volume")
            mapping[volume_names[1]] = {"bind": "/run/secrets", "mode": "ro"}
            return mapping
        if kind == "redis":
            if len(volume_names) != 1:
                raise CellResourceError("redis requires exactly one retained volume")
            return {volume_names[0]: {"bind": _REDIS_DATA, "mode": "rw"}}
        if kind == "draft-runtime":
            if len(volume_names) != 2:
                raise CellResourceError("draft runtime requires workspace and agent-home volumes")
            return {
                volume_names[0]: {"bind": _WORKSPACE_SOURCE, "mode": "rw"},
                volume_names[1]: {"bind": _AGENT_HOME_ROOT, "mode": "rw"},
            }
        if volume_names:
            raise CellResourceError(f"unexpected volume attachment for {kind}")
        return {}

    @staticmethod
    def _postgres_volume_mapping(volume_names: tuple[str, ...]) -> dict[str, dict[str, str]]:
        if len(volume_names) != 1:
            raise CellResourceError("postgres container requires exactly one retained volume")
        return {volume_names[0]: {"bind": _POSTGRES_ROOT, "mode": "rw"}}

    def _container_tmpfs(self, spec: DockerContainerSpec) -> dict[str, str]:
        if spec.labels["omnia.resource_kind"].startswith("postgres"):
            return dict(_POSTGRES_TMPFS)
        if spec.labels["omnia.resource_kind"] == "draft-runtime":
            tmpfs = dict(_DEFAULT_TMPFS)
            tmpfs[_WORKSPACE_RUN_ROOT] = "rw,nosuid,nodev,size=256m"
            return tmpfs
        return dict(_HELPER_TMPFS if spec.helper else _DEFAULT_TMPFS)

    def _container_ports(self, spec: DockerContainerSpec) -> dict[str, object]:
        kind = spec.labels["omnia.resource_kind"]
        if kind != "draft-runtime":
            return {}
        binding = spec.ports["3000/tcp"]
        host_ip, host_port = binding.rsplit(":", 1)
        return {"3000/tcp": (host_ip, int(host_port))}

    def _normalize_port_bindings(self, port_bindings: dict[str, Any]) -> dict[str, str]:
        normalized: dict[str, str] = {}
        for key, raw_value in port_bindings.items():
            normalized[str(key)] = self._normalize_port_binding_value(raw_value)
        return normalized

    def _normalize_port_binding_value(self, value: Any) -> str:
        if isinstance(value, tuple) and len(value) == 2:
            return f"{value[0]}:{value[1]}"
        if isinstance(value, list) and value:
            first = value[0]
            if isinstance(first, dict):
                host_ip = str(first.get("HostIp") or "")
                host_port = str(first.get("HostPort") or "")
                return f"{host_ip}:{host_port}".strip(":")
            return self._normalize_port_binding_value(first)
        if isinstance(value, dict):
            host_ip = str(value.get("HostIp") or "")
            host_port = str(value.get("HostPort") or "")
            return f"{host_ip}:{host_port}".strip(":")
        return str(value)

    def _helper_labels(self, labels: dict[str, str], resource_kind: str) -> dict[str, str]:
        merged = dict(labels)
        merged["omnia.resource_kind"] = resource_kind
        merged["omnia.helper"] = "true"
        return merged

    def _helper_name(self, purpose: str, source_name: str) -> str:
        prefix = source_name[:48].rstrip("-")
        return f"{prefix}-{purpose}"

    async def _start_helper_container(
        self,
        *,
        name: str,
        labels: dict[str, str],
        volumes: dict[str, dict[str, str]],
        tmpfs: dict[str, str] | None = None,
    ) -> Any:
        self._require_identity_labels(labels)

        def _create() -> Any:
            return self._client_obj().containers.create(
                self.helper_image,
                command=_HELPER_SLEEP,
                # Helpers belong to a single call, not to the shared volume.
                # Never remove a namesake: it may be serving another request.
                name=f"{name}-{uuid4().hex}",
                detach=True,
                auto_remove=True,
                labels=labels,
                user="0:0",
                cap_add=[],
                cap_drop=["ALL"],
                read_only=True,
                privileged=False,
                security_opt=["no-new-privileges:true"],
                ports={},
                environment={},
                volumes=volumes,
                tmpfs=dict(_HELPER_TMPFS if tmpfs is None else tmpfs),
                pids_limit=_HELPER_PIDS_LIMIT,
                mem_limit=_HELPER_MEMORY_LIMIT_BYTES,
                network="none",
            )

        def _create_and_start() -> Any:
            for attempt in range(3):
                try:
                    container = _create()
                    break
                except docker.errors.APIError as exc:
                    detail = str(getattr(exc, "explanation", "") or "").lower()
                    if not (
                        attempt < 2
                        and exc.status_code == 409
                        and "container name" in detail
                        and "already in use" in detail
                    ):
                        raise
                    # Creation was rejected, so retry only allocation under a
                    # fresh name. Never replay a file write or shell command.
            try:
                container.start()
                if hasattr(container, "reload"):
                    container.reload()
                return container
            except BaseException:
                with contextlib.suppress(Exception):
                    container.remove(force=True)
                raise

        startup = asyncio.create_task(asyncio.to_thread(_create_and_start))
        try:
            return await asyncio.shield(startup)
        except asyncio.CancelledError:
            # A cancelled await does not stop docker-py's worker thread. Wait
            # for its result so a late-created helper cannot escape cleanup.
            while not startup.done():
                try:
                    await asyncio.shield(startup)
                except asyncio.CancelledError:
                    continue
                except Exception:
                    break
            if not startup.cancelled() and startup.exception() is None:
                await self._remove_container_object(startup.result())
            raise

    async def _remove_container_object(self, container: Any) -> None:
        try:
            await asyncio.to_thread(lambda: container.remove(force=True))
        except docker.errors.NotFound:
            return
        except docker.errors.APIError as exc:
            detail = f"{getattr(exc, 'explanation', '') or ''} {exc}".lower()
            if exc.status_code == 404 or (
                exc.status_code == 409
                and "removal of container" in detail
                and "already in progress" in detail
            ):
                return
            raise

    def _get_archive_bytes(self, container: Any, path: str, label: str) -> bytes:
        chunks, _ = container.get_archive(path)
        return _read_archive_bytes(
            cast(Iterable[bytes | bytearray], chunks),
            label=label,
            max_bytes=self.archive_limit_bytes,
        )

    def _exec_checked(
        self,
        container: Any,
        command: list[str],
        label: str,
        environment: dict[str, str] | None = None,
    ) -> bytes:
        result = container.exec_run(command, environment=environment, demux=False)
        exit_code, output = self._exec_result_parts(result)
        if exit_code != 0:
            detail = output.decode("utf-8", "ignore")
            raise CellResourceError(f"{label} failed with exit code {exit_code}: {detail}")
        return output

    @staticmethod
    def _exec_best_effort(
        container: Any,
        command: list[str],
        environment: dict[str, str] | None = None,
    ) -> None:
        try:
            container.exec_run(command, environment=environment, demux=False)
        except Exception:
            return

    def _wait_checked(self, container: Any, label: str, *, timeout_seconds: int) -> None:
        status_code = self._wait_status(container, label, timeout_seconds=timeout_seconds)
        if status_code != 0:
            logs = b""
            with contextlib.suppress(Exception):
                logs = cast(bytes, container.logs(tail=50))
            detail = logs.decode("utf-8", "ignore") if isinstance(logs, (bytes, bytearray)) else ""
            raise CellResourceError(f"{label} exited {status_code}: {detail}")

    @staticmethod
    def _wait_status(container: Any, label: str, *, timeout_seconds: int) -> int:
        result = container.wait(timeout=timeout_seconds)
        status = result.get("StatusCode") if isinstance(result, dict) else result
        if not isinstance(status, (int, str)):
            raise CellResourceError(f"{label} returned invalid wait result")
        try:
            return int(status)
        except ValueError as exc:
            raise CellResourceError(f"{label} returned invalid wait result") from exc

    @staticmethod
    def _exec_result_parts(result: Any) -> tuple[int, bytes]:
        if isinstance(result, tuple) and len(result) == 2:
            exit_code, output = result
        else:
            exit_code = getattr(result, "exit_code", None)
            output = getattr(result, "output", b"")
        if isinstance(output, tuple):
            output = b"".join(part for part in output if isinstance(part, (bytes, bytearray)))
        if not isinstance(output, (bytes, bytearray)):
            output = str(output).encode("utf-8")
        if exit_code is None:
            exit_code = 0
        return int(exit_code), bytes(output)

    @staticmethod
    def _labels_match(actual: dict[str, str], expected: dict[str, str]) -> bool:
        return all(actual.get(key) == value for key, value in expected.items())
