"""Host-capacity admission checks for Project Cell bundles."""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Protocol, cast

from omnia_orchestrator.core.cell_resources import (
    AdmissionDecision,
    CellResourceProfile,
    DockerDaemonIdentity,
    FilesystemCapacityEvidence,
    HostCapacitySnapshot,
)


class StatVfsLike(Protocol):
    f_bavail: int
    f_frsize: int
    f_favail: int
    f_blocks: int
    f_files: int


class DockerInfoReader(Protocol):
    api: object
    base_url: str

    def info(self) -> dict[str, object]: ...


def _missing_statvfs(_path: str) -> os.stat_result:
    raise OSError("os.statvfs unavailable on this platform")


_DEFAULT_STATVFS = cast(Callable[[str], StatVfsLike], getattr(os, "statvfs", _missing_statvfs))
_DEFAULT_LOADAVG = cast(
    Callable[[], tuple[float, float, float]],
    getattr(os, "getloadavg", lambda: (0.0, 0.0, 0.0)),
)


def _read_memory_from_proc() -> tuple[int, int]:
    total: int | None = None
    available: int | None = None
    with open("/proc/meminfo", encoding="utf-8") as handle:
        for line in handle:
            if line.startswith("MemTotal:"):
                total = int(line.split()[1]) * 1024
            if line.startswith("MemAvailable:"):
                available = int(line.split()[1]) * 1024
    if total is None or available is None:
        raise ValueError("/proc/meminfo does not contain MemTotal/MemAvailable")
    return total, available


@dataclass(slots=True)
class DockerHostCapacityReader:
    docker: DockerInfoReader
    docker_host: str = ""
    state_path: str = "/opt/omnia-runtime/state/project-cells.json"
    statvfs: Callable[[str], StatVfsLike] = _DEFAULT_STATVFS
    meminfo_reader: Callable[[], int | tuple[int, int]] = _read_memory_from_proc
    loadavg_reader: Callable[[], tuple[float, float, float]] = _DEFAULT_LOADAVG
    cpu_count_reader: Callable[[], int | None] = os.cpu_count
    active_bundle_counter: Callable[[], int] = lambda: 0
    local_daemon_root_is_verifiable: bool = True

    def read(self) -> HostCapacitySnapshot:
        try:
            info = self.docker.info()
        except Exception:
            return self._failure("daemon_filesystem_unverifiable")

        daemon_id = str(info.get("ID") or "")
        docker_root_dir = str(info.get("DockerRootDir") or "")
        daemon = DockerDaemonIdentity(
            id=daemon_id,
            name=str(info.get("Name") or ""),
            docker_root_dir=docker_root_dir,
            operating_system=str(info.get("OperatingSystem") or ""),
        )
        if not daemon_id or not docker_root_dir:
            return self._failure("daemon_filesystem_unverifiable", daemon=daemon)
        if not self._local_daemon_is_proven() or not self._is_local_root(docker_root_dir):
            return self._failure(
                "daemon_filesystem_unverifiable",
                daemon=daemon,
                path=docker_root_dir,
            )
        try:
            filesystems = tuple(
                FilesystemCapacityEvidence(
                    path=path,
                    free_bytes=int(stats.f_bavail) * int(stats.f_frsize),
                    free_inodes=int(stats.f_favail),
                    total_bytes=int(getattr(stats, "f_blocks", stats.f_bavail))
                    * int(stats.f_frsize),
                    total_inodes=int(getattr(stats, "f_files", stats.f_favail)),
                )
                for path, stats in (
                    (path, self.statvfs(path)) for path in self._required_paths(docker_root_dir)
                )
            )
            memory_reading = self.meminfo_reader()
            if isinstance(memory_reading, tuple):
                memory_total, memory_available = memory_reading
            else:
                memory_total = memory_available = memory_reading
            load_1m, _, _ = self.loadavg_reader()
            cpu_count = self.cpu_count_reader() or 0
        except Exception:
            return self._failure(
                "daemon_filesystem_unverifiable",
                daemon=daemon,
                path=docker_root_dir,
            )
        if not filesystems:
            return self._failure(
                "daemon_filesystem_unverifiable",
                daemon=daemon,
                path=docker_root_dir,
            )
        free_bytes = min(item.free_bytes for item in filesystems)
        free_inodes = min(item.free_inodes for item in filesystems)
        total_bytes = min(item.total_bytes or item.free_bytes for item in filesystems)
        total_inodes = min(item.total_inodes or item.free_inodes for item in filesystems)

        return HostCapacitySnapshot(
            cpu_count=cpu_count,
            load_1m=float(load_1m),
            memory_available_bytes=int(memory_available),
            disk_free_bytes=free_bytes,
            disk_free_inodes=free_inodes,
            active_bundle_count=int(self.active_bundle_counter()),
            disk_path=docker_root_dir,
            memory_total_bytes=int(memory_total),
            disk_total_bytes=total_bytes,
            disk_total_inodes=total_inodes,
            filesystem_evidence=filesystems,
            daemon=daemon,
            failure_reason=None,
        )

    def _local_daemon_is_proven(self) -> bool:
        if not self.local_daemon_root_is_verifiable:
            return False
        endpoint = self._daemon_endpoint()
        if not endpoint:
            return False
        return self._is_local_socket_endpoint(endpoint)

    def _daemon_endpoint(self) -> str:
        if self.docker_host:
            return self.docker_host
        docker_api = getattr(self.docker, "api", None)
        for candidate in (
            getattr(docker_api, "base_url", None),
            getattr(self.docker, "base_url", None),
        ):
            if isinstance(candidate, str) and candidate:
                return candidate
        return ""

    @staticmethod
    def _is_local_socket_endpoint(endpoint: str) -> bool:
        normalized = endpoint.casefold()
        if normalized.startswith(("unix://", "npipe://")):
            return True
        # docker-py normalizes proven local transports to http+docker URLs.
        return normalized in {
            "http+docker://localhost",
            "http+docker://localnpipe",
        }

    def _is_local_root(self, docker_root_dir: str) -> bool:
        return Path(docker_root_dir).is_absolute() or PurePosixPath(docker_root_dir).is_absolute()

    def _required_paths(self, docker_root_dir: str) -> tuple[str, ...]:
        paths = [docker_root_dir]
        state_parent = Path(self.state_path).expanduser().parent
        if not state_parent.as_posix():
            return (docker_root_dir,)
        state_parent_text = state_parent.as_posix()
        if state_parent_text not in paths:
            paths.append(state_parent_text)
        return tuple(paths)

    @staticmethod
    def _failure(
        reason: str,
        *,
        daemon: DockerDaemonIdentity | None = None,
        path: str = "",
    ) -> HostCapacitySnapshot:
        return HostCapacitySnapshot(
            cpu_count=0,
            load_1m=0.0,
            memory_available_bytes=0,
            disk_free_bytes=0,
            disk_free_inodes=0,
            active_bundle_count=0,
            disk_path=path,
            filesystem_evidence=(),
            daemon=daemon,
            failure_reason=reason,
        )


@dataclass(frozen=True, slots=True)
class CellAdmissionGate:
    profile: CellResourceProfile

    def check(
        self,
        snapshot: HostCapacitySnapshot,
        *,
        existing_bundle: bool,
        running_bundle: bool,
        reserved: object | None = None,
        provisional: object | None = None,
    ) -> AdmissionDecision:
        from omnia_orchestrator.services.cell_reservations import ReservedCapacity

        reserved_capacity = (
            reserved if isinstance(reserved, ReservedCapacity) else ReservedCapacity()
        )
        provisional_capacity = (
            provisional
            if isinstance(provisional, ReservedCapacity)
            else ReservedCapacity()
        )
        required = ReservedCapacity.from_profile(self.profile)
        if snapshot.failure_reason:
            return AdmissionDecision(False, snapshot.failure_reason)
        if running_bundle:
            return AdmissionDecision(True, "running_bundle_reuse")
        if snapshot.cpu_count - reserved_capacity.cpu_cores - required.cpu_cores < (
            self.profile.host_cpu_reserve_cores
        ) or snapshot.cpu_count - snapshot.load_1m - provisional_capacity.cpu_cores - (
            required.cpu_cores
        ) < self.profile.host_cpu_reserve_cores:
            return AdmissionDecision(False, "insufficient_cpu")
        memory_total = snapshot.memory_total_bytes or snapshot.memory_available_bytes
        if memory_total - reserved_capacity.memory_bytes - required.memory_bytes < (
            self.profile.host_memory_reserve_bytes
        ) or snapshot.memory_available_bytes - provisional_capacity.memory_bytes - (
            required.memory_bytes
        ) < self.profile.host_memory_reserve_bytes:
            return AdmissionDecision(False, "insufficient_memory")
        disk_total = snapshot.disk_total_bytes or snapshot.disk_free_bytes
        if disk_total - reserved_capacity.disk_bytes - required.disk_bytes < (
            self.profile.host_disk_reserve_bytes
        ) or snapshot.disk_free_bytes - provisional_capacity.disk_bytes - required.disk_bytes < (
            self.profile.host_disk_reserve_bytes
        ):
            return AdmissionDecision(False, "insufficient_disk")
        inode_total = snapshot.disk_total_inodes or snapshot.disk_free_inodes
        if inode_total - reserved_capacity.inodes - required.inodes < (
            self.profile.host_inode_reserve
        ) or snapshot.disk_free_inodes - provisional_capacity.inodes - required.inodes < (
            self.profile.host_inode_reserve
        ):
            return AdmissionDecision(False, "insufficient_inodes")
        return AdmissionDecision(True, "admitted")
