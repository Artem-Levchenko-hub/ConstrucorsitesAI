import hashlib
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from yleum_orchestrator.schemas.runtime import DeployRequest
from yleum_orchestrator.services import builder
from yleum_orchestrator.services.build_artifact_inventory import filtered_inventory
from yleum_orchestrator.services.builder import _is_next_template

_BUILT_IMAGE_ID = "sha256:" + "a" * 64


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _max_template_artifact_inventory() -> dict[str, str]:
    template = Path(__file__).resolve().parents[1] / "templates" / "max-miniapp-nextjs"
    return {
        "drizzle/0000_max_core.sql": _digest(template / "drizzle/0000_max_core.sql"),
        "drizzle/0001_business_core.sql": _digest(template / "drizzle/0001_business_core.sql"),
        "drizzle/0002_row_level_security.sql": _digest(
            template / "drizzle/0002_row_level_security.sql"
        ),
        "scripts/apply-migrations.mjs": _digest(template / "scripts/apply-migrations.mjs"),
    }


async def _run_max_builder(
    monkeypatch: pytest.MonkeyPatch,
    *,
    source_inventory: dict[str, str],
    image_inventory: dict[str, str] | None = None,
) -> tuple[AsyncMock, AsyncMock, dict[str, object]]:
    """Exercise the real builder while replacing only Docker/network boundaries."""
    from yleum_orchestrator.core.config import get_settings
    from yleum_orchestrator.services import provisioner

    project_id = "00000000-0000-0000-0000-000000000001"
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql://omnia_root:rootpw@localhost:5433/omnia_users",
    )
    monkeypatch.setenv("INTERNAL_TOKEN", "test-token-test-token-test-token")
    monkeypatch.setenv("AGENT_SANDBOX_ENABLED", "true")
    get_settings.cache_clear()  # type: ignore[attr-defined]

    captured: dict[str, object] = {}
    build = AsyncMock(return_value=_BUILT_IMAGE_ID)
    destroy = AsyncMock()

    async def copy_with_inventory(
        _container: str,
        source: str,
        _destination: str,
    ) -> dict[str, str] | None:
        if source == "/app/drizzle":
            return dict(source_inventory)
        if source == "/app/scripts":
            return {}
        return None

    async def capture_start(spec: object) -> str:
        captured["spec"] = spec
        return "prod-container-id"

    monkeypatch.setattr(
        builder.docker_client,
        "container_image_template",
        AsyncMock(return_value="max-miniapp-nextjs"),
    )
    monkeypatch.setattr(builder.docker_client, "unpause_container", AsyncMock())
    monkeypatch.setattr(
        builder.docker_client,
        "copy_path_from_container",
        AsyncMock(return_value=False),
    )
    monkeypatch.setattr(
        builder.docker_client,
        "copy_path_from_container_with_inventory",
        copy_with_inventory,
        raising=False,
    )
    monkeypatch.setattr(builder.docker_client, "build_image", build)
    monkeypatch.setattr(
        builder.docker_client,
        "image_path_inventory",
        AsyncMock(return_value=image_inventory or {}),
        raising=False,
    )
    monkeypatch.setattr(builder.docker_client, "destroy_container", destroy)
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
    return build, destroy, captured


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


def test_max_artifact_inventory_never_retains_sensitive_or_generated_paths() -> None:
    digest = "a" * 64
    assert filtered_inventory(
        {
            ".env": digest,
            ".env.production": digest,
            "secrets/token": digest,
            "node_modules/pkg/index.js": digest,
            ".next/server/app.js": digest,
            ".git/config": digest,
            "drizzle/0000_max_core.sql": digest,
        }
    ) == {"drizzle/0000_max_core.sql": digest}


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


@pytest.mark.parametrize("case", ["missing", "seed_masked"])
async def test_max_source_migration_gap_blocks_before_build_or_swap(
    monkeypatch: pytest.MonkeyPatch,
    case: str,
) -> None:
    expected = _max_template_artifact_inventory()
    source_inventory = {
        "drizzle/0000_max_core.sql": expected["drizzle/0000_max_core.sql"],
    }
    if case == "seed_masked":
        # The trusted container inventory says this source-owned migration changed,
        # while the materialized context still contains the template seed.
        source_inventory["drizzle/0001_business_core.sql"] = "f" * 64

    build, destroy, captured = await _run_max_builder(
        monkeypatch,
        source_inventory=source_inventory,
    )

    assert build.await_count == 0
    assert destroy.await_count == 0
    assert "spec" not in captured


@pytest.mark.parametrize(
    "tampered_path",
    ["drizzle/0001_business_core.sql", "scripts/apply-migrations.mjs"],
)
async def test_max_built_image_tamper_blocks_before_swap(
    monkeypatch: pytest.MonkeyPatch,
    tampered_path: str,
) -> None:
    expected = _max_template_artifact_inventory()
    source = {path: digest for path, digest in expected.items() if path.endswith(".sql")}
    image = dict(expected)
    image[tampered_path] = "e" * 64

    build, destroy, captured = await _run_max_builder(
        monkeypatch,
        source_inventory=source,
        image_inventory=image,
    )

    assert build.await_count == 1
    assert destroy.await_count == 0
    assert "spec" not in captured


async def test_max_starts_the_exact_verified_image_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected = _max_template_artifact_inventory()
    source = {path: digest for path, digest in expected.items() if path.endswith(".sql")}

    build, destroy, captured = await _run_max_builder(
        monkeypatch,
        source_inventory=source,
        image_inventory=expected,
    )

    assert build.await_count == 1
    assert destroy.await_count == 1
    spec = captured["spec"]
    assert isinstance(spec, builder.docker_client.ContainerSpec)
    assert spec.image == _BUILT_IMAGE_ID


async def test_max_production_container_uses_same_isolated_runtime_profile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from yleum_orchestrator.core.config import get_settings
    from yleum_orchestrator.services import provisioner

    project_id = "00000000-0000-0000-0000-000000000001"
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql://omnia_root:rootpw@localhost:5433/omnia_users",
    )
    monkeypatch.setenv("INTERNAL_TOKEN", "test-token-test-token-test-token")
    monkeypatch.setenv("AGENT_SANDBOX_ENABLED", "true")
    get_settings.cache_clear()  # type: ignore[attr-defined]

    captured: dict[str, object] = {}
    expected = _max_template_artifact_inventory()

    async def capture_start(spec: object) -> str:
        captured["spec"] = spec
        return "prod-container-id"

    monkeypatch.setattr(
        builder.docker_client,
        "container_image_template",
        AsyncMock(return_value="max-miniapp-nextjs"),
    )
    monkeypatch.setattr(builder.docker_client, "unpause_container", AsyncMock())
    monkeypatch.setattr(builder.docker_client, "copy_path_from_container", AsyncMock())
    monkeypatch.setattr(
        builder.docker_client,
        "copy_path_from_container_with_inventory",
        AsyncMock(
            side_effect=[
                {path: digest for path, digest in expected.items() if path.endswith(".sql")},
                {},
            ]
        ),
    )
    monkeypatch.setattr(
        builder.docker_client,
        "build_image",
        AsyncMock(return_value=_BUILT_IMAGE_ID),
    )
    monkeypatch.setattr(
        builder.docker_client,
        "image_path_inventory",
        AsyncMock(return_value=expected),
    )
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
    assert spec.image == _BUILT_IMAGE_ID
    assert spec.kind == "prod"
    assert spec.network_name == f"omnia-proj-{project_id}"
    assert spec.network_service_names == ("omnia-postgres-users",)
    assert spec.include_host_gateway is False
    assert spec.sandbox_profile == "max-runtime-v1"
    assert spec.harden is True
    assert spec.pids_limit >= 64
    assert spec.env["DATABASE_URL"] == "postgresql://scoped"
