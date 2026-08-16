"""Security contract for MAX development preview bootstrapping."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock
from uuid import UUID

import pytest

from omnia_orchestrator.core.errors import OrchestratorError
from omnia_orchestrator.routers import runtime
from omnia_orchestrator.services import provisioner

PROJECT_ID = UUID("00000000-0000-0000-0000-000000000001")


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("INTERNAL_TOKEN", "test-token-test-token-test-token")
    monkeypatch.setenv("SECRETS_ROOT", str(tmp_path / "secrets"))
    monkeypatch.setenv("DATABASE_URL", "postgresql://omnia_root:rootpw@localhost:5433/omnia_users")
    from omnia_orchestrator.core.config import get_settings

    get_settings.cache_clear()  # type: ignore[attr-defined]


async def test_max_preview_bootstrap_is_hmac_signed_https_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        runtime,
        "find_project_container",
        AsyncMock(return_value="omnia-dev-max-demo"),
    )
    monkeypatch.setattr(
        runtime,
        "docker_container_status",
        AsyncMock(return_value={"state": "running"}),
    )
    monkeypatch.setattr(
        runtime,
        "container_image_template",
        AsyncMock(return_value="max-miniapp-nextjs"),
    )
    monkeypatch.setattr(runtime, "load_existing_auth_secret", lambda _project: "auth-secret")
    monkeypatch.setattr(
        runtime.nginx_writer,
        "dev_url",
        lambda _slug: "https://max-demo-dev.preview.example.test",
    )

    response = await runtime.create_max_preview_session(
        PROJECT_ID, "test-token-test-token-test-token"
    )

    assert response.project_id == PROJECT_ID
    assert response.model_dump(mode="json")["project_id"] == str(PROJECT_ID)
    assert response.bootstrap_url.startswith(
        "https://max-demo-dev.preview.example.test/api/omnia/preview-session?"
    )
    query = dict(part.split("=", 1) for part in response.bootstrap_url.split("?", 1)[1].split("&"))
    assert query["signature"] == runtime._max_preview_bootstrap_signature(
        "auth-secret", str(PROJECT_ID), int(query["expires"])
    )
    expires_at = datetime.fromisoformat(response.expires_at.replace("Z", "+00:00"))
    assert datetime.now(UTC) < expires_at


def test_max_preview_bootstrap_message_matches_template_contract() -> None:
    assert runtime._max_preview_bootstrap_message(str(PROJECT_ID), 1_893_456_000) == (
        b"omnia:max-preview-session:v1\n00000000-0000-0000-0000-000000000001\n1893456000"
    )


@pytest.mark.parametrize(
    ("container_name", "state", "template", "secret", "expected_code"),
    [
        (None, "running", "max-miniapp-nextjs", "auth-secret", "not_found"),
        (
            "omnia-dev-max-demo",
            "paused",
            "max-miniapp-nextjs",
            "auth-secret",
            "container_not_running",
        ),
        ("omnia-dev-max-demo", "running", "nextjs-entities", "auth-secret", "unsupported_stack"),
        ("omnia-dev-max-demo", "running", "max-miniapp-nextjs", None, "not_found"),
    ],
)
async def test_max_preview_bootstrap_refuses_non_live_or_unprovisioned_projects(
    monkeypatch: pytest.MonkeyPatch,
    container_name: str | None,
    state: str,
    template: str,
    secret: str | None,
    expected_code: str,
) -> None:
    monkeypatch.setattr(runtime, "find_project_container", AsyncMock(return_value=container_name))
    monkeypatch.setattr(
        runtime, "docker_container_status", AsyncMock(return_value={"state": state})
    )
    monkeypatch.setattr(runtime, "container_image_template", AsyncMock(return_value=template))
    monkeypatch.setattr(runtime, "load_existing_auth_secret", lambda _project: secret)

    with pytest.raises(OrchestratorError) as exc_info:
        await runtime.create_max_preview_session(PROJECT_ID, "test-token-test-token-test-token")
    assert exc_info.value.code == expected_code


def test_existing_auth_secret_does_not_create_missing_project_state(tmp_path: Path) -> None:
    assert provisioner.load_existing_auth_secret(str(PROJECT_ID)) is None
    assert not (tmp_path / "secrets" / str(PROJECT_ID)).exists()
