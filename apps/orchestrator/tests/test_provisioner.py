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

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock
from uuid import UUID

import pytest

from omnia_orchestrator.core.docker_client import ContainerSpec
from omnia_orchestrator.core.errors import OrchestratorError
from omnia_orchestrator.schemas.runtime import ProvisionRequest, ProvisionResponse
from omnia_orchestrator.services import provisioner


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql://omnia_root:rootpw@localhost:5433/omnia_users",
    )
    monkeypatch.setenv("INTERNAL_TOKEN", "test-token-test-token-test-token")
    monkeypatch.setenv("PROJECTS_ROOT", str(tmp_path / "projects"))
    from omnia_orchestrator.core.config import get_settings

    get_settings.cache_clear()  # type: ignore[attr-defined]


async def _provision_capturing_spec(
    monkeypatch: pytest.MonkeyPatch,
    *,
    template: str = "nextjs-entities",
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
    monkeypatch.setattr(provisioner, "_load_or_create_auth_secret", lambda _p: "auth-secret")
    monkeypatch.setattr(provisioner, "_restore_max_platform_core", lambda *_a: None)

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
    monkeypatch.setattr(provisioner.nginx_writer, "publish_tls_in_background", lambda *_a: None)

    monkeypatch.setattr(provisioner, "start_container", fake_start)
    monkeypatch.setattr(provisioner, "find_project_container", AsyncMock(return_value=None))
    monkeypatch.setattr(provisioner, "unpause_container", AsyncMock())
    monkeypatch.setattr(provisioner, "copy_path_from_container", AsyncMock())
    monkeypatch.setattr(provisioner, "write_files", AsyncMock())
    monkeypatch.setattr(provisioner, "publish_project_event", AsyncMock())

    req = ProvisionRequest(
        project_id=UUID("00000000-0000-0000-0000-000000000001"),
        slug="demo-app",
        template=template,
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


async def test_duplicate_project_provisions_are_serialized(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    active = 0
    max_active = 0

    async def fake_provision_once(req: ProvisionRequest) -> ProvisionResponse:
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        await asyncio.sleep(0)
        active -= 1
        return ProvisionResponse(
            project_id=req.project_id,
            container_name=f"omnia-dev-{req.slug}",
            port=3210,
            dev_url=f"https://{req.slug}-dev.test",
            state="running",
        )

    monkeypatch.setattr(provisioner, "_provision_once", fake_provision_once)
    req = ProvisionRequest(
        project_id=UUID("00000000-0000-0000-0000-000000000099"),
        slug="same-project",
        template="max-miniapp-nextjs",
        tier="free",
    )

    await asyncio.gather(provisioner.provision(req), provisioner.provision(req))

    assert max_active == 1


# ── Phase 1 egress + network isolation (default OFF = current behaviour) ─────


async def test_provision_default_no_egress_proxy_and_shared_net(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Defaults OFF: no proxy env injected, network_name None (shared net) —
    byte-identical to pre-Phase-1."""
    spec = await _provision_capturing_spec(monkeypatch)
    assert "HTTP_PROXY" not in spec.env
    assert "http_proxy" not in spec.env
    assert spec.network_name is None


async def test_provision_injects_egress_proxy_when_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With container_egress_proxy set, the container is forced through the
    allowlisting proxy (HTTP(S)_PROXY) while internal services bypass via
    NO_PROXY — the real egress allowlist for the agent's bash."""
    monkeypatch.setenv("CONTAINER_EGRESS_PROXY", "http://omnia-egress:3128")
    from omnia_orchestrator.core.config import get_settings

    get_settings.cache_clear()  # type: ignore[attr-defined]

    spec = await _provision_capturing_spec(monkeypatch)
    assert spec.env["HTTP_PROXY"] == "http://omnia-egress:3128"
    assert spec.env["HTTPS_PROXY"] == "http://omnia-egress:3128"
    assert spec.env["http_proxy"] == "http://omnia-egress:3128"
    assert "omnia-postgres-users" in spec.env["NO_PROXY"]


async def test_provision_isolates_network_when_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With isolate_project_network, the container joins its OWN per-project
    bridge net instead of the shared runtime net (no lateral reach)."""
    monkeypatch.setenv("ISOLATE_PROJECT_NETWORK", "true")
    from omnia_orchestrator.core.config import get_settings

    get_settings.cache_clear()  # type: ignore[attr-defined]

    spec = await _provision_capturing_spec(monkeypatch)
    assert spec.network_name == "omnia-proj-00000000-0000-0000-0000-000000000001"


def test_max_integration_env_never_injects_shared_platform_secrets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OMNIA_MINIO_ACCESS_KEY", "shared-access")
    monkeypatch.setenv("OMNIA_MINIO_SECRET_KEY", "shared-secret")
    monkeypatch.setenv("OMNIA_SMTP_PASS", "shared-smtp")
    monkeypatch.setenv("OMNIA_LLM_GATEWAY_URL", "http://shared-gateway")

    assert provisioner._integration_env("max-miniapp-nextjs") == {}


async def test_max_provision_uses_isolated_secretless_runtime_profile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OMNIA_MINIO_ACCESS_KEY", "shared-access")
    monkeypatch.setenv("OMNIA_MINIO_SECRET_KEY", "shared-secret")
    monkeypatch.setenv("OMNIA_SMTP_PASS", "shared-smtp")
    monkeypatch.setenv("OMNIA_LLM_GATEWAY_URL", "http://shared-gateway")

    spec = await _provision_capturing_spec(
        monkeypatch,
        template="max-miniapp-nextjs",
    )

    assert spec.network_name == "omnia-proj-00000000-0000-0000-0000-000000000001"
    assert spec.network_service_names == ("omnia-postgres-users",)
    assert spec.include_host_gateway is False
    assert spec.sandbox_profile == "max-runtime-v1"
    assert spec.recreate_on_profile_change is True
    assert spec.harden is True
    assert spec.pids_limit >= 64
    assert spec.cpu_quota == 1.0
    assert spec.memory_mb == 4096
    forbidden = {
        "MINIO_ACCESS_KEY",
        "MINIO_SECRET_KEY",
        "SMTP_PASS",
        "SMTP_PASSWORD",
        "LLM_GATEWAY_URL",
        "ORCHESTRATOR_INTERNAL_TOKEN",
        "DOCKER_HOST",
    }
    assert forbidden.isdisjoint(spec.env)
    assert spec.env["DATABASE_URL"].startswith("postgresql://")
    assert spec.env["AUTH_SECRET"] == "auth-secret"


def test_copy_template_preserves_generated_files_and_seeds_missing_files(
    tmp_path: Path,
) -> None:
    template = tmp_path / "template"
    project = tmp_path / "project"
    (template / "src" / "app").mkdir(parents=True)
    (template / "src" / "app" / "page.tsx").write_text("template page", encoding="utf-8")
    (template / "src" / "lib").mkdir(parents=True)
    (template / "src" / "lib" / "client.ts").write_text("template client", encoding="utf-8")
    (template / ".next").mkdir()
    (template / ".next" / "cache").write_text("generated", encoding="utf-8")
    (project / "src" / "app").mkdir(parents=True)
    (project / "src" / "app" / "page.tsx").write_text("user generated page", encoding="utf-8")

    provisioner._copy_template(template, project)

    assert (project / "src" / "app" / "page.tsx").read_text(encoding="utf-8") == (
        "user generated page"
    )
    assert (project / "src" / "lib" / "client.ts").read_text(encoding="utf-8") == (
        "template client"
    )
    assert not (project / ".next").exists()


def test_restore_max_platform_core_reseeds_managed_files_only(tmp_path: Path) -> None:
    template = tmp_path / "template"
    workspace = tmp_path / "workspace"
    (template / "src" / "lib" / "omnia").mkdir(parents=True)
    (template / "src" / "lib" / "omnia" / "client.ts").write_text(
        "template omnia client",
        encoding="utf-8",
    )
    (template / "src" / "app" / "api" / "omnia").mkdir(parents=True)
    (template / "src" / "app" / "api" / "omnia" / "actions.ts").write_text(
        "template actions",
        encoding="utf-8",
    )
    (template / "src" / "components").mkdir(parents=True)
    (template / "src" / "components" / "MaxAppProvider.tsx").write_text(
        "template provider",
        encoding="utf-8",
    )
    (workspace / "src" / "lib" / "omnia").mkdir(parents=True)
    (workspace / "src" / "lib" / "omnia" / "client.ts").write_text(
        "stale runtime client",
        encoding="utf-8",
    )
    (workspace / "src" / "app").mkdir(parents=True)
    (workspace / "src" / "app" / "page.tsx").write_text(
        "user product page",
        encoding="utf-8",
    )

    provisioner._restore_max_platform_core(workspace, template)

    assert (workspace / "src" / "lib" / "omnia" / "client.ts").read_text(
        encoding="utf-8"
    ) == "template omnia client"
    assert (workspace / "src" / "app" / "api" / "omnia" / "actions.ts").read_text(
        encoding="utf-8"
    ) == "template actions"
    assert (workspace / "src" / "components" / "MaxAppProvider.tsx").read_text(
        encoding="utf-8"
    ) == "template provider"
    assert (workspace / "src" / "app" / "page.tsx").read_text(encoding="utf-8") == (
        "user product page"
    )


def test_collect_max_runtime_overlay_skips_platform_core_but_keeps_schema(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    (workspace / "src" / "app").mkdir(parents=True)
    (workspace / "src" / "app" / "page.tsx").write_text(
        "user product page",
        encoding="utf-8",
    )
    (workspace / "src" / "lib" / "omnia").mkdir(parents=True)
    (workspace / "src" / "lib" / "omnia" / "client.ts").write_text(
        "managed core",
        encoding="utf-8",
    )
    (workspace / "src" / "lib" / "db").mkdir(parents=True)
    (workspace / "src" / "lib" / "db" / "schema.ts").write_text(
        "user schema extension",
        encoding="utf-8",
    )
    (workspace / "package.json").write_text('{"name":"max-app"}', encoding="utf-8")
    (workspace / "pnpm-lock.yaml").write_text("lockfileVersion: '9.0'", encoding="utf-8")

    files = provisioner._collect_max_runtime_overlay(workspace)

    assert files["src/app/page.tsx"] == "user product page"
    assert files["src/lib/db/schema.ts"] == "user schema extension"
    assert files["package.json"] == '{"name":"max-app"}'
    assert files["pnpm-lock.yaml"] == "lockfileVersion: '9.0'"
    assert "src/lib/omnia/client.ts" not in files


async def test_sync_max_runtime_applies_core_and_installs_user_dependencies(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    files = {
        "package.json": '{"name":"max-app"}',
        "pnpm-lock.yaml": "lockfileVersion: '9.0'",
        "src/lib/omnia/client.ts": "current managed core",
        "src/lib/db/schema.ts": "user schema",
    }
    write = AsyncMock()
    execute = AsyncMock(
        side_effect=[
            {"exit_code": "0", "stdout": "", "stderr": ""},
            {
                "exit_code": "0",
                "stdout": "lockfileVersion: '9.0'\npackages: {}\n",
                "stderr": "",
            },
        ]
    )
    monkeypatch.setattr(provisioner, "_workspace_text_files", lambda _root: files)
    monkeypatch.setattr(provisioner, "write_files", write)
    monkeypatch.setattr(provisioner, "exec_cmd", execute)

    await provisioner._sync_max_runtime_workspace("omnia-dev-max", tmp_path)

    write.assert_awaited_once_with("omnia-dev-max", files)
    assert execute.await_count == 2
    command = execute.await_args_list[0].kwargs["cmd"]
    assert command == ["pnpm", "install", "--no-frozen-lockfile", "--ignore-scripts"]
    assert (tmp_path / "pnpm-lock.yaml").read_text(encoding="utf-8") == (
        "lockfileVersion: '9.0'\npackages: {}\n"
    )


async def test_sync_max_runtime_fails_closed_on_dependency_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        provisioner,
        "_workspace_text_files",
        lambda _root: {"package.json": '{"name":"broken"}'},
    )
    monkeypatch.setattr(provisioner, "write_files", AsyncMock())
    monkeypatch.setattr(
        provisioner,
        "exec_cmd",
        AsyncMock(return_value={"exit_code": "1", "stdout": "", "stderr": "bad lock"}),
    )

    with pytest.raises(OrchestratorError, match="MAX dependency sync failed"):
        await provisioner._sync_max_runtime_workspace("omnia-dev-max", tmp_path)
