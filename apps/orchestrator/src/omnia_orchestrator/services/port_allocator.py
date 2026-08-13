"""Collision-safe host-port allocation for project runtime containers.

The registry is a reservation journal, not an authority over the host. Before
every allocation it is reconciled with all Docker port bindings (including
stopped containers) and real listening sockets. This covers containers created
before the registry, daemon/manual restarts and stale registry files.
"""

from __future__ import annotations

import asyncio
import json
import os
import socket
import time
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

import docker  # type: ignore[import-untyped]

from omnia_orchestrator.core.config import get_settings
from omnia_orchestrator.core.errors import OrchestratorError

_PENDING_RESERVATION_SECONDS = 120.0


@dataclass(frozen=True, slots=True)
class HostPortBinding:
    port: int
    project_id: str | None
    kind: str
    container_name: str


def _discover_docker_bindings() -> dict[int, HostPortBinding]:
    """Return configured host bindings, including stopped/created containers."""

    client = docker.DockerClient(base_url=get_settings().docker_host)
    try:
        containers = client.containers.list(all=True)
        bindings: dict[int, HostPortBinding] = {}
        for container in containers:
            try:
                container.reload()
                attrs = container.attrs or {}
            except Exception:
                # A container can disappear between list and inspect. It cannot
                # retain a host binding after removal, so continue the snapshot.
                continue
            labels = (attrs.get("Config") or {}).get("Labels") or container.labels or {}
            port_bindings = (attrs.get("HostConfig") or {}).get("PortBindings") or {}
            for configured in port_bindings.values():
                for item in configured or []:
                    try:
                        port = int(str(item.get("HostPort") or ""))
                    except (TypeError, ValueError):
                        continue
                    if port <= 0:
                        continue
                    bindings[port] = HostPortBinding(
                        port=port,
                        project_id=(str(labels.get("omnia.project_id") or "") or None),
                        kind=str(labels.get("omnia.kind") or ""),
                        container_name=str(getattr(container, "name", "") or ""),
                    )
        return bindings
    finally:
        client.close()


def _port_is_listening(port: int) -> bool:
    """A bind probe catches non-Docker/manual listeners and Docker TOCTOU peers."""

    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 0)
        probe.bind(("127.0.0.1", port))
    except OSError:
        return True
    finally:
        probe.close()
    return False


class PortAllocator:
    def __init__(
        self,
        *,
        port_range: tuple[int, int] | None = None,
        registry_filename: str = ".port-registry.json",
        owned_kinds: frozenset[str] = frozenset({"dev"}),
    ) -> None:
        settings = get_settings()
        lo, hi = port_range or (settings.port_range_min, settings.port_range_max)
        self._range = range(lo, hi + 1)
        self._registry_path = Path(settings.projects_root) / registry_filename
        self._owned_kinds = owned_kinds
        self._lock = asyncio.Lock()
        self._loaded: dict[str, int] | None = None
        self._pending: dict[str, float] = {}

    def _load(self) -> dict[str, int]:
        if self._loaded is not None:
            return self._loaded
        if not self._registry_path.exists():
            self._loaded = {}
            return self._loaded
        try:
            raw = json.loads(self._registry_path.read_text(encoding="utf-8"))
            self._loaded = {
                str(key): int(value)
                for key, value in raw.items()
                if isinstance(value, int) and value in self._range
            }
        except (OSError, AttributeError, TypeError, ValueError, json.JSONDecodeError):
            self._loaded = {}
        return self._loaded

    def _save(self) -> None:
        assert self._loaded is not None
        self._registry_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._registry_path.with_suffix(self._registry_path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(self._loaded, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        os.replace(temporary, self._registry_path)

    async def _host_bindings(self) -> dict[int, HostPortBinding]:
        try:
            return await asyncio.to_thread(_discover_docker_bindings)
        except Exception as exc:
            # A socket probe cannot see a stopped container's configured port.
            # Fail closed until Docker returns rather than stealing that port.
            raise OrchestratorError(
                code="docker_unavailable",
                message="cannot reconcile runtime ports with Docker",
                status_code=503,
            ) from exc

    async def acquire(self, project_id: UUID) -> int:
        """Reserve a reconciled port, adopting an existing owned container."""

        async with self._lock:
            registry = self._load()
            key = str(project_id)
            now = time.monotonic()
            bindings = await self._host_bindings()

            # Docker is authoritative for an existing exact project runtime.
            owned = sorted(
                port
                for port, binding in bindings.items()
                if port in self._range
                and binding.project_id == key
                and binding.kind in self._owned_kinds
            )
            if owned:
                port = owned[0]
                for other_key, other_port in list(registry.items()):
                    if other_port == port and other_key != key:
                        registry.pop(other_key, None)
                        self._pending.pop(other_key, None)
                registry[key] = port
                self._pending.pop(key, None)
                self._save()
                return port

            # Remove registry ghosts. A fresh in-process reservation gets a
            # short lease; after restart, an entry without Docker/socket proof
            # is stale immediately and cannot permanently leak the pool.
            for owner, port in list(registry.items()):
                pending_at = self._pending.get(owner)
                pending = pending_at is not None and now - pending_at < _PENDING_RESERVATION_SECONDS
                if port in bindings or _port_is_listening(port) or pending:
                    continue
                registry.pop(owner, None)
                self._pending.pop(owner, None)

            existing = registry.get(key)
            if existing is not None:
                binding = bindings.get(existing)
                if binding is None and not _port_is_listening(existing):
                    return existing
                if (
                    binding is not None
                    and binding.project_id == key
                    and binding.kind in self._owned_kinds
                ):
                    return existing
                registry.pop(key, None)
                self._pending.pop(key, None)

            taken = set(registry.values()) | set(bindings)
            for port in self._range:
                if port in taken or _port_is_listening(port):
                    continue
                registry[key] = port
                self._pending[key] = now
                self._save()
                return port
            raise OrchestratorError(
                code="port_exhausted",
                message=(
                    f"no free port in {self._range.start}..{self._range.stop - 1} "
                    "after Docker/socket reconciliation"
                ),
                status_code=503,
            )

    async def confirm(self, project_id: UUID, port: int) -> None:
        """Mark a reservation as materialised by Docker without changing it."""

        async with self._lock:
            registry = self._load()
            if registry.get(str(project_id)) == port:
                self._pending.pop(str(project_id), None)

    async def reject(self, project_id: UUID, port: int) -> None:
        """Release only the exact failed reservation; never another runtime."""

        async with self._lock:
            registry = self._load()
            key = str(project_id)
            if registry.get(key) == port:
                registry.pop(key, None)
                self._pending.pop(key, None)
                self._save()

    async def release(self, project_id: UUID) -> None:
        async with self._lock:
            registry = self._load()
            key = str(project_id)
            registry.pop(key, None)
            self._pending.pop(key, None)
            self._save()


_singleton: PortAllocator | None = None


def get_port_allocator() -> PortAllocator:
    global _singleton
    if _singleton is None:
        _singleton = PortAllocator()
    return _singleton


_prod_singleton: PortAllocator | None = None


def get_prod_port_allocator() -> PortAllocator:
    global _prod_singleton
    if _prod_singleton is None:
        settings = get_settings()
        _prod_singleton = PortAllocator(
            port_range=(settings.prod_port_range_min, settings.prod_port_range_max),
            registry_filename=".prod-port-registry.json",
            owned_kinds=frozenset({"prod"}),
        )
    return _prod_singleton
