from __future__ import annotations

import asyncio
import json
from pathlib import Path
from uuid import UUID

import pytest

from omnia_orchestrator.services import port_allocator
from omnia_orchestrator.services.port_allocator import HostPortBinding, PortAllocator

P1 = UUID("00000000-0000-0000-0000-000000000001")
P2 = UUID("00000000-0000-0000-0000-000000000002")
P3 = UUID("00000000-0000-0000-0000-000000000003")


@pytest.fixture(autouse=True)
def _settings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("PROJECTS_ROOT", str(tmp_path))
    monkeypatch.setenv("INTERNAL_TOKEN", "test-token-test-token-test-token")
    monkeypatch.setenv(
        "DATABASE_URL", "postgresql://omnia_root:rootpw@localhost:5433/omnia_users"
    )
    monkeypatch.setenv("PORT_RANGE_MIN", "3200")
    monkeypatch.setenv("PORT_RANGE_MAX", "3209")
    from omnia_orchestrator.core.config import get_settings

    get_settings.cache_clear()  # type: ignore[attr-defined]
    monkeypatch.setattr(port_allocator, "_port_is_listening", lambda _port: False)


def _binding(port: int, project: UUID, *, kind: str = "dev") -> HostPortBinding:
    return HostPortBinding(
        port=port,
        project_id=str(project),
        kind=kind,
        container_name=f"omnia-{kind}-{project}",
    )


async def test_live_container_missing_from_registry_is_never_reallocated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        port_allocator,
        "_discover_docker_bindings",
        lambda: {3200: _binding(3200, P1)},
    )
    allocator = PortAllocator(port_range=(3200, 3202))

    assert await allocator.acquire(P2) == 3201


async def test_concurrent_projects_receive_distinct_atomic_reservations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(port_allocator, "_discover_docker_bindings", lambda: {})
    allocator = PortAllocator(port_range=(3200, 3202))

    ports = await asyncio.gather(allocator.acquire(P1), allocator.acquire(P2))

    assert sorted(ports) == [3200, 3201]
    persisted = json.loads(allocator._registry_path.read_text(encoding="utf-8"))
    assert len(set(persisted.values())) == 2


async def test_bind_toctou_rejection_selects_next_safe_port(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    occupied: set[int] = set()
    monkeypatch.setattr(port_allocator, "_discover_docker_bindings", lambda: {})
    monkeypatch.setattr(port_allocator, "_port_is_listening", lambda port: port in occupied)
    allocator = PortAllocator(port_range=(3200, 3202))

    first = await allocator.acquire(P1)
    occupied.add(first)
    await allocator.reject(P1, first)
    second = await allocator.acquire(P1)

    assert (first, second) == (3200, 3201)


async def test_stale_registry_reservation_is_reclaimed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(port_allocator, "_discover_docker_bindings", lambda: {})
    allocator = PortAllocator(port_range=(3200, 3202))
    allocator._registry_path.parent.mkdir(parents=True, exist_ok=True)
    allocator._registry_path.write_text(json.dumps({str(P1): 3200}), encoding="utf-8")

    assert await allocator.acquire(P2) == 3200
    persisted = json.loads(allocator._registry_path.read_text(encoding="utf-8"))
    assert str(P1) not in persisted


async def test_exact_project_adopts_its_existing_docker_port(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        port_allocator,
        "_discover_docker_bindings",
        lambda: {3205: _binding(3205, P1)},
    )
    allocator = PortAllocator(port_range=(3200, 3209))

    assert await allocator.acquire(P1) == 3205
    assert await allocator.acquire(P1) == 3205


async def test_failed_and_deleted_projects_leave_no_registry_ports(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(port_allocator, "_discover_docker_bindings", lambda: {})
    allocator = PortAllocator(port_range=(3200, 3202))

    failed = await allocator.acquire(P1)
    await allocator.reject(P1, failed)
    deleted = await allocator.acquire(P2)
    await allocator.confirm(P2, deleted)
    await allocator.release(P2)

    persisted = json.loads(allocator._registry_path.read_text(encoding="utf-8"))
    assert persisted == {}
    assert allocator._pending == {}
