"""Unit tests for `services.provisioner` — the parts that don't need a real
Docker daemon / Postgres / nginx.

We mock every collaborator (template copy, port allocator, postgres, nginx,
container start, event bus) and assert the one thing that regresses silently
and matters for P0 infra-hardening: the `ContainerSpec` provision hands to
Docker. Specifically the memory ceiling (config-driven, default 4 GB so heavy
entity/fullstack apps don't OOM mid-compile) and the `unless-stopped` restart
policy (a crashed dev server self-heals; hibernation still wins because Docker
never restarts an API-stopped container).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock
from uuid import UUID

import pytest

from omnia_orchestrator.core.docker_client import ContainerSpec
from omnia_orchestrator.schemas.runtime import ProvisionRequest
from omnia_orchestrator.services import provisioner


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql://omnia_root:rootpw@localhost:5433/omnia_users",
    )
    monkeypatch.setenv("INTERNAL_TOKEN", "test-token-test-token-test-token")
    from omnia_orchestrator.core.config import get_settings

    get_settings.cache_clear()  # type: ignore[attr-defined]


async def _provision_capturing_spec(
    monkeypatch: pytest.MonkeyPatch,
) -> ContainerSpec:
    """Run `provision` with every side-effecting collaborator stubbed; return
    the ContainerSpec that would have been handed to Docker."""
    captured: dict[str, ContainerSpec] = {}

    async def fake_start(spec: ContainerSpec) -> str:
        captured["spec"] = spec
        return "deadbeef" * 8

    # Template copy + source resolution → no filesystem touch.
    monkeypatch.setattr(provisioner, "_template_source_dir", lambda _t: Path("."))
    monkeypatch.setattr(provisioner, "_copy_template", lambda _s, _d: None)
    monkeypatch.setattr(
        provisioner, "_load_or_create_auth_secret", lambda _p: "auth-secret"
    )

    # Port allocator → fixed port.
    allocator = type("A", (), {"acquire": AsyncMock(return_value=3210)})()
    monkeypatch.setattr(provisioner, "get_port_allocator", lambda: allocator)

    # Postgres → reuse an "existing" DSN so create_schema is never called.
    monkeypatch.setattr(
        provisioner.postgres_admin,
        "load_existing_dsn",
        lambda _p: "postgresql://u:p@host/db",
    )

    # nginx → no real reload / cert issuance.
    monkeypatch.setattr(provisioner.nginx_writer, "dev_host", lambda s: f"{s}-dev.test")
    monkeypatch.setattr(provisioner.nginx_writer, "dev_url", lambda s: f"https://{s}-dev.test")
    monkeypatch.setattr(provisioner.nginx_writer, "publish_http", AsyncMock())
    monkeypatch.setattr(
        provisioner.nginx_writer, "publish_tls_in_background", lambda *_a: None
    )

    monkeypatch.setattr(provisioner, "start_container", fake_start)
    monkeypatch.setattr(provisioner, "publish_project_event", AsyncMock())

    req = ProvisionRequest(
        project_id=UUID("00000000-0000-0000-0000-000000000001"),
        slug="demo-app",
        template="nextjs-entities",
        tier="free",
    )
    await provisioner.provision(req)
    return captured["spec"]


async def test_provision_sets_4gb_memory_ceiling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Dev containers get the config default (4 GB) — heavy app OOM fix."""
    spec = await _provision_capturing_spec(monkeypatch)
    assert spec.memory_mb == 4096


async def test_provision_sets_unless_stopped_restart_policy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Crashed dev server self-heals; hibernate stop still wins (Docker never
    restarts a daemon-stopped container)."""
    spec = await _provision_capturing_spec(monkeypatch)
    assert spec.restart_policy_name == "unless-stopped"


async def test_provision_memory_is_config_driven(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Operators can retune the ceiling via env without a code change (R-02)."""
    monkeypatch.setenv("DEV_CONTAINER_MEMORY_MB", "8192")
    from omnia_orchestrator.core.config import get_settings

    get_settings.cache_clear()  # type: ignore[attr-defined]

    spec = await _provision_capturing_spec(monkeypatch)
    assert spec.memory_mb == 8192
