"""Per-project host port allocation in [3001, 3999].

Strategy: file-backed registry at `{projects_root}/.port-registry.json`.
Loaded into memory at boot, persisted on every acquire/release.
Concurrent safety: single asyncio.Lock — orchestrator is single-process.

R-01 (deep module): callers see `acquire(project_id) -> int` and
`release(project_id) -> None`. The on-disk file format, range bounds, and
collision-recovery logic are private.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from uuid import UUID

from omnia_orchestrator.core.config import get_settings
from omnia_orchestrator.core.errors import OrchestratorError


class PortAllocator:
    def __init__(
        self,
        *,
        port_range: tuple[int, int] | None = None,
        registry_filename: str = ".port-registry.json",
    ) -> None:
        s = get_settings()
        lo, hi = port_range or (s.port_range_min, s.port_range_max)
        self._range = range(lo, hi + 1)
        self._registry_path = Path(s.projects_root) / registry_filename
        self._lock = asyncio.Lock()
        self._loaded: dict[str, int] | None = None

    def _load(self) -> dict[str, int]:
        if self._loaded is not None:
            return self._loaded
        if not self._registry_path.exists():
            self._loaded = {}
            return self._loaded
        try:
            self._loaded = json.loads(self._registry_path.read_text())
        except (OSError, json.JSONDecodeError):
            self._loaded = {}
        return self._loaded

    def _save(self) -> None:
        assert self._loaded is not None
        self._registry_path.parent.mkdir(parents=True, exist_ok=True)
        self._registry_path.write_text(json.dumps(self._loaded, indent=2, sort_keys=True))

    async def acquire(self, project_id: UUID) -> int:
        """Return existing port if already allocated, else pick a free one."""
        async with self._lock:
            registry = self._load()
            key = str(project_id)
            if key in registry:
                return registry[key]
            taken = set(registry.values())
            for port in self._range:
                if port not in taken:
                    registry[key] = port
                    self._save()
                    return port
            raise OrchestratorError(
                code="port_exhausted",
                message=f"no free port in {self._range.start}..{self._range.stop}",
                status_code=503,
            )

    async def release(self, project_id: UUID) -> None:
        async with self._lock:
            registry = self._load()
            registry.pop(str(project_id), None)
            self._save()


_singleton: PortAllocator | None = None


def get_port_allocator() -> PortAllocator:
    global _singleton
    if _singleton is None:
        _singleton = PortAllocator()
    return _singleton


_prod_singleton: PortAllocator | None = None


def get_prod_port_allocator() -> PortAllocator:
    """Separate pool for deployed prod containers so a project's dev and prod
    ports never collide."""
    global _prod_singleton
    if _prod_singleton is None:
        s = get_settings()
        _prod_singleton = PortAllocator(
            port_range=(s.prod_port_range_min, s.prod_port_range_max),
            registry_filename=".prod-port-registry.json",
        )
    return _prod_singleton
