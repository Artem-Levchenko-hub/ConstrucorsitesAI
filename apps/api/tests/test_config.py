from __future__ import annotations

import os
from pathlib import Path

import pytest
from pydantic import ValidationError

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://x:x@localhost/x")
os.environ.setdefault("JWT_SECRET", "test-secret")

from yleum_api.core.config import Settings, get_settings


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
        "RESTORATION_ADAPTATION_EDIT_SECONDS",
        "RESTORATION_ADAPTATION_REPAIR_SECONDS",
        "RESTORATION_ADAPTATION_ACTIVATION_SECONDS",
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
    # 2700: первый ход адаптации не помещался в срок обычной правки — живой
    # прогон dab6c832 работал до самой отсечки и был остановлен на ходу.
    assert settings.restoration_adaptation_edit_seconds == 2700
    # 3600: сначала 900 не хватило, потом и 1800 — два живых прогона подряд
    # открыли починку и не успели замкнуть даже один круг с пересборкой.
    assert settings.restoration_adaptation_repair_seconds == 3600
    assert settings.restoration_adaptation_activation_seconds == 2400
    assert settings.project_cell_heartbeat_seconds == 15
    assert settings.project_cell_watchdog_grace_seconds == 20


def test_product_advisor_default_uses_a_current_gateway_model(
    isolated_settings_env: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("PRODUCT_ADVISOR_MODEL", raising=False)

    # Haiku was removed from the gateway: accepting it here made every real
    # advice request fail with a 404 despite mocked completion tests passing.
    assert get_settings().product_advisor_model == "claude-sonnet-5"


def test_generation_window_override_adds_thirty_minutes_to_edit_and_repair(
    isolated_settings_env: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from datetime import UTC, datetime, timedelta
    from uuid import uuid4

    from yleum_api.models.generation_run import GenerationRun
    from yleum_api.services.generation_deadline import generation_deadline, source_edit_deadline

    baseline = get_settings()
    started = datetime(2026, 11, 10, tzinfo=UTC)
    run = GenerationRun(id=uuid4(), status="running", created_at=started, started_at=started,
                        agent_state={})
    old_deadline = generation_deadline(run).at
    assert old_deadline is not None
    monkeypatch.setenv("MAX_GENERATION_DEADLINE_SECONDS", "3300")
    get_settings.cache_clear()  # type: ignore[attr-defined]

    settings = get_settings()
    deadline = generation_deadline(run).at
    assert settings.max_generation_deadline_seconds == 3300
    assert deadline == old_deadline + timedelta(minutes=30)
    assert source_edit_deadline(deadline, started_at=started, reserve_seconds=180) == (
        source_edit_deadline(old_deadline, started_at=started, reserve_seconds=180)
        + timedelta(minutes=30)
    )
    for name in (
        "restoration_adaptation_edit_seconds",
        "restoration_adaptation_repair_seconds",
        "restoration_adaptation_activation_seconds",
    ):
        assert getattr(settings, name) == getattr(baseline, name)


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("max_generation_deadline_seconds", 0),
        ("restoration_adaptation_repair_seconds", 0),
        ("restoration_adaptation_activation_seconds", 0),
        ("project_cell_heartbeat_seconds", 0),
        ("project_cell_watchdog_grace_seconds", 0),
    ],
    ids=[
        "deadline",
        "adaptation-repair",
        "adaptation-activation",
        "heartbeat",
        "watchdog-grace",
    ],
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
