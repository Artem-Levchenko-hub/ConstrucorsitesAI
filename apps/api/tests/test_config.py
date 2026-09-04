from __future__ import annotations

import os
from pathlib import Path

import pytest
from pydantic import ValidationError

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://x:x@localhost/x")
os.environ.setdefault("JWT_SECRET", "test-secret")

from omnia_api.core.config import Settings, get_settings


@pytest.fixture
def isolated_settings_env(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://x:x@localhost/x")
    monkeypatch.setenv("JWT_SECRET", "test-secret")
    for name in (
        "USE_MAX_FINALIZATION_COORDINATOR",
        "USE_PROJECT_CELL_ACTIVITY_WATCHDOG",
        "USE_GENERATION_EVENT_REPLAY",
        "USE_CELL_RESOURCE_PROFILE_V2",
        "MAX_GENERATION_DEADLINE_SECONDS",
        "PROJECT_CELL_HEARTBEAT_SECONDS",
        "PROJECT_CELL_WATCHDOG_GRACE_SECONDS",
    ):
        monkeypatch.delenv(name, raising=False)
    get_settings.cache_clear()  # type: ignore[attr-defined]
    yield
    get_settings.cache_clear()  # type: ignore[attr-defined]


def test_max_finalization_defaults_are_dark_and_deadlines_are_exact(
    isolated_settings_env: None,
) -> None:
    settings = get_settings()

    assert settings.use_max_finalization_coordinator is False
    assert settings.use_project_cell_activity_watchdog is False
    assert settings.use_generation_event_replay is False
    assert settings.use_cell_resource_profile_v2 is False
    assert settings.max_generation_deadline_seconds == 1500
    assert settings.project_cell_heartbeat_seconds == 15
    assert settings.project_cell_watchdog_grace_seconds == 20


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("max_generation_deadline_seconds", 0),
        ("project_cell_heartbeat_seconds", 0),
        ("project_cell_watchdog_grace_seconds", 0),
    ],
    ids=["deadline", "heartbeat", "watchdog-grace"],
)
def test_max_finalization_bounds_reject_nonpositive_values(
    field_name: str,
    value: int,
) -> None:
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            database_url="postgresql+asyncpg://x:x@localhost/x",
            jwt_secret="test-secret",
            **{field_name: value},
        )
