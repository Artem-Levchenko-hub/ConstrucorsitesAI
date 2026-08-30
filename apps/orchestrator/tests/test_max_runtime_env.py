from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from omnia_orchestrator.schemas.runtime import DeployRequest
from omnia_orchestrator.services import builder
from omnia_orchestrator.services.builder import _is_next_template


def test_max_runtime_env_accepts_only_declared_secret_keys() -> None:
    request = DeployRequest(
        project_id="00000000-0000-0000-0000-000000000001",
        runtime_env={
            "MAX_BOT_TOKEN": "bot-secret",
            "MAX_WEBHOOK_SECRET": "hook-secret",
            "MAX_API_BASE_URL": "https://platform-api2.max.ru",
        },
    )
    assert request.runtime_env["MAX_BOT_TOKEN"] == "bot-secret"


def test_max_runtime_env_rejects_arbitrary_container_override() -> None:
    with pytest.raises(ValidationError):
        DeployRequest(
            project_id="00000000-0000-0000-0000-000000000001",
            runtime_env={"DATABASE_URL": "attacker-controlled"},
        )


def test_max_template_gets_next_production_build_guards() -> None:
    assert _is_next_template("max-miniapp-nextjs") is True
    assert _is_next_template("nextjs-postgres-drizzle") is True
    assert _is_next_template("vite-react-spa") is False


def test_max_dev_entrypoint_uses_deterministic_fail_closed_migrations() -> None:
    template = Path(__file__).resolve().parents[1] / "templates" / "max-miniapp-nextjs"
    entrypoint = (template / "docker-entrypoint.sh").read_text(encoding="utf-8")

    assert "drizzle-kit push" not in entrypoint
    assert "if ! timeout 45 node scripts/apply-migrations.mjs" in entrypoint
    assert "exit 1" in entrypoint


def test_max_prod_dockerfile_requires_frozen_lockfile_without_script_fallback() -> None:
    template = Path(__file__).resolve().parents[1] / "templates" / "max-miniapp-nextjs"
    dockerfile = (template / "Dockerfile.prod").read_text(encoding="utf-8")

    assert "pnpm install --frozen-lockfile --prod=false --ignore-scripts" in dockerfile
    assert "pnpm install --ignore-scripts\n" not in dockerfile
    assert "--frozen-lockfile --prod=false --ignore-scripts ||" not in dockerfile


def test_max_prod_build_caches_exact_pnpm_before_network_is_disabled() -> None:
    template = Path(__file__).resolve().parents[1] / "templates" / "max-miniapp-nextjs"
    dockerfile = (template / "Dockerfile.prod").read_text(encoding="utf-8")

    assert "corepack prepare pnpm@9.15.0 --activate" in dockerfile
    assert "corepack prepare pnpm@9 --activate" not in dockerfile
    assert (
        "COPY --from=deps --chown=node:node /root/.cache/node/corepack "
        "/home/node/.cache/node/corepack"
    ) in dockerfile
    assert dockerfile.index("/home/node/.cache/node/corepack") < dockerfile.index(
        "RUN --network=none pnpm exec next build"
    )


async def test_max_production_container_uses_same_isolated_runtime_profile(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from omnia_orchestrator.core.config import get_settings
    from omnia_orchestrator.services import provisioner

    project_id = "00000000-0000-0000-0000-000000000001"
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql://omnia_root:rootpw@localhost:5433/omnia_users",
    )
    monkeypatch.setenv("INTERNAL_TOKEN", "test-token-test-token-test-token")
    monkeypatch.setenv("AGENT_SANDBOX_ENABLED", "true")
    get_settings.cache_clear()  # type: ignore[attr-defined]

    template_dir = tmp_path / "template"
    template_dir.mkdir()
    dockerfile = builder.get_stack("max-miniapp-nextjs").production_dockerfile
    assert dockerfile is not None
    (template_dir / dockerfile).write_text("FROM scratch\n", encoding="utf-8")
    captured: dict[str, object] = {}

    async def capture_start(spec: object) -> str:
        captured["spec"] = spec
        return "prod-container-id"

    monkeypatch.setattr(
        builder.docker_client,
        "container_image_template",
        AsyncMock(return_value="max-miniapp-nextjs"),
    )
    monkeypatch.setattr(builder, "_template_source_dir", lambda _template: template_dir)
    monkeypatch.setattr(builder.docker_client, "unpause_container", AsyncMock())
    monkeypatch.setattr(builder.docker_client, "copy_path_from_container", AsyncMock())
    monkeypatch.setattr(builder.docker_client, "build_image", AsyncMock())
    monkeypatch.setattr(builder.docker_client, "destroy_container", AsyncMock())
    monkeypatch.setattr(builder.docker_client, "start_container", capture_start)
    monkeypatch.setattr(builder.docker_client, "prune_old_app_images", AsyncMock())
    monkeypatch.setattr(
        builder,
        "get_prod_port_allocator",
        lambda: SimpleNamespace(acquire=AsyncMock(return_value=4321)),
    )
    monkeypatch.setattr(builder, "_healthy", AsyncMock(return_value=True))
    monkeypatch.setattr(builder, "_resolve_runtime_dsn", lambda _p: "postgresql://scoped")
    monkeypatch.setattr(provisioner, "_load_or_create_auth_secret", lambda _p: "auth")
    monkeypatch.setattr(builder.nginx_writer, "prod_url", lambda slug: f"https://{slug}.test")
    monkeypatch.setattr(builder.nginx_writer, "prod_host", lambda slug: f"{slug}.test")
    monkeypatch.setattr(
        builder.nginx_writer,
        "publish",
        AsyncMock(return_value="https://max-app.test"),
    )
    monkeypatch.setattr(builder, "publish_project_event", AsyncMock())
    monkeypatch.setattr(builder.deploy_state, "update", lambda *_a, **_kw: None)
    monkeypatch.setattr(builder.deploy_state, "append_log", lambda *_a, **_kw: None)

    await builder._run(project_id, "max-app", "omnia-dev-max-app")

    assert "spec" in captured
    spec = captured["spec"]
    assert isinstance(spec, builder.docker_client.ContainerSpec)
    assert spec.kind == "prod"
    assert spec.network_name == f"omnia-proj-{project_id}"
    assert spec.network_service_names == ("omnia-postgres-users",)
    assert spec.include_host_gateway is False
    assert spec.sandbox_profile == "max-runtime-v1"
    assert spec.harden is True
    assert spec.pids_limit >= 64
    assert spec.env["DATABASE_URL"] == "postgresql://scoped"
