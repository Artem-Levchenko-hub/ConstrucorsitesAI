"""Security contract for MAX development preview bootstrapping."""

from __future__ import annotations

from pathlib import Path
from uuid import UUID

import pytest

from yleum_orchestrator.routers import runtime
from yleum_orchestrator.services import provisioner

PROJECT_ID = UUID("00000000-0000-0000-0000-000000000001")


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("INTERNAL_TOKEN", "test-token-test-token-test-token")
    monkeypatch.setenv("SECRETS_ROOT", str(tmp_path / "secrets"))
    monkeypatch.setenv("DATABASE_URL", "postgresql://omnia_root:rootpw@localhost:5433/omnia_users")
    from yleum_orchestrator.core.config import get_settings

    get_settings.cache_clear()  # type: ignore[attr-defined]


def test_max_preview_bootstrap_message_matches_template_contract() -> None:
    assert runtime._max_preview_bootstrap_message(str(PROJECT_ID), 1_893_456_000) == (
        b"omnia:max-preview-session:v1\n00000000-0000-0000-0000-000000000001\n1893456000"
    )


def test_existing_auth_secret_does_not_create_missing_project_state(tmp_path: Path) -> None:
    assert provisioner.load_existing_auth_secret(str(PROJECT_ID)) is None
    assert not (tmp_path / "secrets" / str(PROJECT_ID)).exists()
