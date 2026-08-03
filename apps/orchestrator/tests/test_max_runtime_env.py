import pytest
from pydantic import ValidationError

from omnia_orchestrator.schemas.runtime import DeployRequest
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


def test_exact_deploy_source_rejects_unsafe_path() -> None:
    with pytest.raises(ValidationError):
        DeployRequest(
            project_id="00000000-0000-0000-0000-000000000001",
            commit_sha="a" * 40,
            slug="safe-app",
            template="max-miniapp-nextjs",
            source_files={"../escape.ts": "bad"},
        )


def test_exact_deploy_source_rejects_oversized_file() -> None:
    with pytest.raises(ValidationError):
        DeployRequest(
            project_id="00000000-0000-0000-0000-000000000001",
            commit_sha="a" * 40,
            slug="safe-app",
            template="max-miniapp-nextjs",
            source_files={"src/app/page.tsx": "x" * (2 * 1024 * 1024 + 1)},
        )
