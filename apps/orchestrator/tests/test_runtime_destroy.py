from __future__ import annotations

from unittest.mock import AsyncMock
from uuid import UUID

import pytest

from omnia_orchestrator.routers import runtime


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql://omnia_root:rootpw@localhost:5433/omnia_users",
    )
    monkeypatch.setenv("INTERNAL_TOKEN", "test-token-test-token-test-token")
    from omnia_orchestrator.core.config import get_settings

    get_settings.cache_clear()  # type: ignore[attr-defined]
    yield
    get_settings.cache_clear()  # type: ignore[attr-defined]


async def test_destroy_removes_isolated_project_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_id = "00000000-0000-0000-0000-000000000001"
    destroy_container = AsyncMock()
    destroy_project_network = AsyncMock()
    dev_allocator = type("Allocator", (), {"release": AsyncMock()})()
    prod_allocator = type("Allocator", (), {"release": AsyncMock()})()

    monkeypatch.setattr(runtime, "_verify_token", lambda _token: None)
    monkeypatch.setattr(runtime, "destroy_container", destroy_container)
    monkeypatch.setattr(runtime, "destroy_project_network", destroy_project_network)
    monkeypatch.setattr(runtime, "get_port_allocator", lambda: dev_allocator)
    monkeypatch.setattr(runtime, "get_prod_port_allocator", lambda: prod_allocator)
    monkeypatch.setattr(runtime.postgres_admin, "archive_schema", AsyncMock())
    monkeypatch.setattr(runtime.nginx_writer, "unpublish", AsyncMock())

    result = await runtime.destroy(project_id, "demo", "internal-token")

    assert result == {"state": "destroyed"}
    assert destroy_container.await_args_list[0].args == ("omnia-dev-demo",)
    assert destroy_container.await_args_list[1].args == ("omnia-app-demo",)
    destroy_project_network.assert_awaited_once_with(
        project_id,
        service_names=("omnia-postgres-users",),
    )
    dev_allocator.release.assert_awaited_once_with(UUID(project_id))
    prod_allocator.release.assert_awaited_once_with(UUID(project_id))
