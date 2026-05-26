"""Tests for `provisioner._load_or_create_auth_secret`.

The function is small but its contract matters: every Auth.js-enabled
generated app's user sessions depend on the value being stable across
restarts AND high-entropy from the first call. A regression here would
either log every user out on each container restart OR ship a guessable
secret.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from omnia_orchestrator.services.provisioner import _load_or_create_auth_secret


@pytest.fixture(autouse=True)
def _env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Each test gets a fresh `secrets_root` so on-disk state doesn't leak."""
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+asyncpg://omnia_root:rootpw@localhost:5433/omnia_users",
    )
    monkeypatch.setenv("INTERNAL_TOKEN", "test-token-test-token-test-token")
    monkeypatch.setenv("SECRETS_ROOT", str(tmp_path))
    from omnia_orchestrator.core.config import get_settings

    get_settings.cache_clear()  # type: ignore[attr-defined]


def test_first_call_creates_secret_with_enough_entropy() -> None:
    secret = _load_or_create_auth_secret("proj-1")
    # URL-safe base64 → 48 bytes encoded ≈ 64 chars, no padding.
    assert len(secret) >= 32
    # No predictable substring (was the actual bug we want to prevent).
    assert "proj-1" not in secret
    # Charset constraint — url-safe-b64 alphabet only.
    assert re.fullmatch(r"[A-Za-z0-9_-]+", secret)


def test_repeat_call_returns_same_value(tmp_path: Path) -> None:
    """Container restart MUST reuse the existing secret; otherwise every
    session cookie issued before the restart becomes invalid."""
    first = _load_or_create_auth_secret("proj-stable")
    second = _load_or_create_auth_secret("proj-stable")
    assert first == second


def test_different_projects_get_different_secrets() -> None:
    """Project isolation — leaking one project's auth tokens must not
    let an attacker forge sessions for any other project."""
    a = _load_or_create_auth_secret("proj-a")
    b = _load_or_create_auth_secret("proj-b")
    assert a != b


def test_secret_persisted_to_per_project_dir(tmp_path: Path) -> None:
    _load_or_create_auth_secret("proj-files")
    expected = tmp_path / "proj-files" / "auth.secret"
    assert expected.is_file()
    # File must be non-empty and match what the loader returns.
    saved = expected.read_text(encoding="utf-8").strip()
    assert saved == _load_or_create_auth_secret("proj-files")


def test_empty_file_triggers_regeneration(tmp_path: Path) -> None:
    """Pathological case: someone manually wipes auth.secret but leaves
    the empty file. Loader must treat empty as missing and regenerate."""
    project = "proj-empty"
    (tmp_path / project).mkdir()
    (tmp_path / project / "auth.secret").write_text("", encoding="utf-8")
    out = _load_or_create_auth_secret(project)
    assert len(out) >= 32
