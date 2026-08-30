from pathlib import Path
from unittest.mock import AsyncMock
from uuid import UUID

import pytest

from omnia_orchestrator.core.errors import OrchestratorError
from omnia_orchestrator.routers import runtime
from omnia_orchestrator.routers.runtime import (
    _command_exposes_environment,
    _redact_exec_output,
)
from omnia_orchestrator.schemas.runtime import HotReloadRequest


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql://omnia_root:rootpw@localhost:5433/omnia_users",
    )
    monkeypatch.setenv("INTERNAL_TOKEN", "test-token-test-token-test-token")
    monkeypatch.setenv("PROJECTS_ROOT", str(tmp_path / "projects"))
    monkeypatch.setenv("AGENT_SANDBOX_ENABLED", "true")
    from omnia_orchestrator.core.config import get_settings

    get_settings.cache_clear()  # type: ignore[attr-defined]


def test_blocks_environment_enumeration_commands() -> None:
    assert _command_exposes_environment("env")
    assert _command_exposes_environment("printenv | sort")
    assert _command_exposes_environment("node -e 'console.log(process.env)'")
    assert _command_exposes_environment("python -c 'import os; print(os.getenv(\"TOKEN\"))'")
    assert _command_exposes_environment("echo $DATABASE_URL")
    assert _command_exposes_environment("cat /proc/1/environ")
    assert _command_exposes_environment("cat .env")
    assert _command_exposes_environment("grep TOKEN config/.env.production")
    assert not _command_exposes_environment("pnpm build")
    assert not _command_exposes_environment("export NODE_ENV=test && pnpm test")


def test_redacts_secret_assignments_and_dsn_passwords() -> None:
    result = _redact_exec_output(
        "AUTH_SECRET=hidden\n"
        "DATABASE_URL=postgresql://app:password@postgres:5432/app\n"
        "Authorization: Bearer hidden-bearer\n"
        "sk-exampleexampleexampleexample\n"
        "build clean"
    )

    assert "hidden" not in result
    assert "password" not in result
    assert "sk-example" not in result
    assert result.endswith("build clean")


async def test_agent_sandbox_capabilities_attest_concrete_runtime(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    runtime_facts = {
        "ready": True,
        "missing": [],
        "profile": "max-runtime-v1",
        "runtime": "runsc",
        "networks": ["omnia-proj-project-1"],
    }
    monkeypatch.setattr(runtime, "_project_workspace_dir", lambda _p: workspace)
    image_name = AsyncMock(return_value="omnia-template-max-miniapp-nextjs:dev")
    security_facts = AsyncMock(return_value=runtime_facts)
    monkeypatch.setattr(runtime, "container_image_name", image_name)
    monkeypatch.setattr(runtime, "container_security_facts", security_facts)

    result = await runtime.agent_sandbox_capabilities(
        "project-1",
        "max-app",
        "test-token-test-token-test-token",
    )

    assert result["ready"] is True
    assert result["missing"] == []
    assert result["profile"] == "ephemeral-secretless-v1"
    assert result["capabilities"]["shell"] is True
    assert result["isolation"]["runtime_network"] is False
    assert result["isolation"]["host_workspace_writable"] is False
    assert result["isolation"]["runtime_secrets"] is False
    assert result["runtime_attestation"] == runtime_facts
    image_name.assert_awaited_once_with("omnia-dev-max-app")
    security_facts.assert_awaited_once_with("omnia-dev-max-app", "project-1")


async def test_agent_sandbox_capabilities_fail_closed_on_runtime_drift(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setattr(runtime, "_project_workspace_dir", lambda _p: workspace)
    monkeypatch.setattr(
        runtime,
        "container_image_name",
        AsyncMock(return_value="omnia-template-max-miniapp-nextjs:dev"),
    )
    monkeypatch.setattr(
        runtime,
        "container_security_facts",
        AsyncMock(
            return_value={
                "ready": False,
                "missing": ["project_network", "platform_secrets_absent"],
            }
        ),
    )

    result = await runtime.agent_sandbox_capabilities(
        "project-1",
        "max-app",
        "test-token-test-token-test-token",
    )

    assert result["ready"] is False
    assert result["missing"] == [
        "runtime:project_network",
        "runtime:platform_secrets_absent",
    ]


async def test_hot_reload_installs_changed_dependencies_without_lifecycle_scripts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lockfile = "lockfileVersion: '9.0'\npackages: {}\n"
    exec_mock = AsyncMock(
        side_effect=[
            {"exit_code": "0", "stdout": "", "stderr": ""},
            {"exit_code": "0", "stdout": lockfile, "stderr": ""},
        ]
    )
    monkeypatch.setattr(runtime, "record_activity", AsyncMock())
    monkeypatch.setattr(
        runtime,
        "write_files",
        AsyncMock(return_value={"written": "1", "total_bytes": "20", "dropped": ""}),
    )
    monkeypatch.setattr(
        runtime.demo_seed_writer,
        "seed_demo_data",
        AsyncMock(return_value={}),
    )
    monkeypatch.setattr(runtime, "exec_cmd", exec_mock)
    payload = HotReloadRequest(
        project_id=UUID("00000000-0000-0000-0000-000000000001"),
        files={"package.json": '{"dependencies":{"zod":"^4.0.0"}}'},
    )

    result = await runtime.hot_reload(
        payload,
        "max-app",
        "test-token-test-token-test-token",
    )

    assert result["package_exit_code"] == "0"
    assert result["pnpm_lockfile"] == lockfile
    assert exec_mock.await_count == 2
    exec_mock.assert_any_await(
        "omnia-dev-max-app",
        cmd=["pnpm", "install", "--no-frozen-lockfile", "--ignore-scripts"],
        workdir="/app",
        timeout_sec=240,
        max_output=runtime._AGENT_MAX_BUILD,
    )
    exec_mock.assert_any_await(
        "omnia-dev-max-app",
        cmd=["cat", "--", "pnpm-lock.yaml"],
        workdir="/app",
        max_output=runtime._SANDBOX_SYNC_MAX_FILE_BYTES + 1,
    )


async def test_hot_reload_rejects_a_stale_sandbox_revision(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    project_id = UUID("00000000-0000-0000-0000-000000000002")
    workspace = tmp_path / "projects" / str(project_id)
    workspace.mkdir(parents=True)
    (workspace / "page.tsx").write_text("current", encoding="utf-8")
    stale_revision = runtime._workspace_revision({"page.tsx": "older"})
    write = AsyncMock(return_value={"written": "1", "total_bytes": "3", "dropped": ""})
    monkeypatch.setattr(runtime, "record_activity", AsyncMock())
    monkeypatch.setattr(runtime, "write_files", write)

    payload = HotReloadRequest(
        project_id=project_id,
        files={"page.tsx": "stale change"},
        base_workspace_revision=stale_revision,
    )

    with pytest.raises(OrchestratorError, match="workspace changed"):
        await runtime.hot_reload(payload, "max-app", "test-token-test-token-test-token")
    write.assert_not_awaited()


async def test_hot_reload_never_forces_data_loss_migrations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    exec_mock = AsyncMock(return_value={"exit_code": "0", "stdout": "", "stderr": ""})
    monkeypatch.setattr(runtime, "record_activity", AsyncMock())
    monkeypatch.setattr(
        runtime,
        "write_files",
        AsyncMock(return_value={"written": "1", "total_bytes": "20", "dropped": ""}),
    )
    monkeypatch.setattr(
        runtime.demo_seed_writer,
        "seed_demo_data",
        AsyncMock(return_value={}),
    )
    monkeypatch.setattr(runtime, "exec_cmd", exec_mock)
    payload = HotReloadRequest(
        project_id=UUID("00000000-0000-0000-0000-000000000001"),
        files={"src/lib/db/schema.ts": "export const userOwnedTable = {};"},
    )

    result = await runtime.hot_reload(
        payload,
        "max-app",
        "test-token-test-token-test-token",
    )

    assert result["drizzle_exit_code"] == "0"
    command = exec_mock.await_args.kwargs["cmd"]
    assert command == [
        "npx",
        "--yes",
        "drizzle-kit",
        "push",
        "--config=drizzle.config.ts",
    ]
    assert "--force" not in command
