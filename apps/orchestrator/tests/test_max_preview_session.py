"""Security contract for MAX development preview bootstrapping."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import httpx
import pytest

from omnia_orchestrator.core.errors import OrchestratorError
from omnia_orchestrator.routers import runtime
from omnia_orchestrator.services import history_cleanup, provisioner

PROJECT_ID = UUID("00000000-0000-0000-0000-000000000001")


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("INTERNAL_TOKEN", "test-token-test-token-test-token")
    monkeypatch.setenv("SECRETS_ROOT", str(tmp_path / "secrets"))
    monkeypatch.setenv("OMNIA_HISTORY_CLEANUP_PATH", str(tmp_path / "history-cleanup.json"))
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


class _HistoryReadyClient:
    def __init__(self, statuses: list[int | None]) -> None:
        self.statuses = statuses

    async def __aenter__(self) -> _HistoryReadyClient:
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    async def get(self, url: str) -> object:
        status = self.statuses.pop(0)
        if status is None:
            raise httpx.ConnectError("cold", request=httpx.Request("GET", url))
        return type("Response", (), {"status_code": status})()


async def test_history_session_waits_through_cold_compile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _HistoryReadyClient([None, 503, 307, 200])
    sleep = AsyncMock()
    monkeypatch.setattr(runtime.httpx, "AsyncClient", lambda **_kwargs: client)
    monkeypatch.setattr(runtime.asyncio, "sleep", sleep)

    await runtime._wait_history_session_ready(31_234, "/bootstrap")

    assert sleep.await_count == 2


async def test_history_session_refuses_unready_container(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _HistoryReadyClient([503, 503])
    monkeypatch.setattr(runtime.httpx, "AsyncClient", lambda **_kwargs: client)
    monkeypatch.setattr(runtime, "_HISTORY_SESSION_READY_TRIES", 2)
    monkeypatch.setattr(runtime.asyncio, "sleep", AsyncMock())

    with pytest.raises(OrchestratorError) as exc_info:
        await runtime._wait_history_session_ready(31_234, "/bootstrap")
    assert exc_info.value.code == "container_failure"


async def test_history_session_refuses_a_bootstrapped_error_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _HistoryReadyClient([307, 404])
    monkeypatch.setattr(runtime.httpx, "AsyncClient", lambda **_kwargs: client)
    monkeypatch.setattr(runtime, "_HISTORY_SESSION_READY_TRIES", 1)

    with pytest.raises(OrchestratorError):
        await runtime._wait_history_session_ready(31_234, "/bootstrap")


def test_history_session_uses_ephemeral_auth_and_a_stable_host() -> None:
    first = runtime._history_environment(PROJECT_ID, "postgresql://isolated")
    second = runtime._history_environment(PROJECT_ID, "postgresql://isolated")

    assert first["AUTH_SECRET"] != second["AUTH_SECRET"]
    assert first["OMNIA_PROJECT_ID"] == str(PROJECT_ID)
    assert runtime._history_session_slug(PROJECT_ID) == "history-0000000000000000"


async def test_history_cleanup_is_generation_safe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot_id = uuid4()
    session_id = uuid4()
    labels = AsyncMock(return_value={})
    remove = AsyncMock(return_value={})
    monkeypatch.setattr(runtime, "history_preview_container_labels", labels)
    monkeypatch.setattr(runtime, "remove_history_preview_session", remove)

    removed = await runtime._drop_selected_history_session(PROJECT_ID, snapshot_id, session_id)

    assert removed is False
    labels.assert_awaited_once_with(
        str(PROJECT_ID),
        snapshot_id=str(snapshot_id),
        purpose="session",
        session_id=str(session_id),
    )
    remove.assert_not_awaited()


async def test_history_cleanup_attempts_database_after_unpublish_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_id = uuid4()
    monkeypatch.setattr(
        runtime.nginx_writer,
        "unpublish",
        AsyncMock(side_effect=RuntimeError("nginx unavailable")),
    )
    drop = AsyncMock()
    monkeypatch.setattr(runtime.postgres_admin, "drop_schema", drop)

    released = await runtime._release_history_resources(
        {
            "omnia.history_origin": "https://history.preview.example.test",
            "omnia.history_database_id": str(database_id),
        }
    )

    assert released is False
    drop.assert_awaited_once_with(database_id)


async def test_history_cleanup_keeps_container_when_resource_release_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot_id = uuid4()
    session_id = uuid4()
    labels = {
        "omnia.kind": "history-session",
        "omnia.project_id": str(PROJECT_ID),
        "omnia.snapshot_id": str(snapshot_id),
        "omnia.history_session_id": str(session_id),
    }
    monkeypatch.setattr(
        runtime,
        "history_preview_container_labels",
        AsyncMock(return_value=labels),
    )
    monkeypatch.setattr(
        runtime,
        "_release_history_resources",
        AsyncMock(return_value=False),
    )
    remove = AsyncMock(return_value=labels)
    monkeypatch.setattr(runtime, "remove_history_preview_session", remove)

    with pytest.raises(OrchestratorError) as exc_info:
        await runtime._drop_selected_history_session(PROJECT_ID, snapshot_id, session_id)

    assert exc_info.value.code == "container_failure"
    remove.assert_not_awaited()


def test_history_databases_are_isolated_by_snapshot_and_purpose() -> None:
    snapshot_a = UUID("00000000-0000-0000-0000-00000000000a")
    snapshot_b = UUID("00000000-0000-0000-0000-00000000000b")

    session_a = runtime._history_database_key(PROJECT_ID, snapshot_a, purpose="session")
    session_b = runtime._history_database_key(PROJECT_ID, snapshot_b, purpose="session")
    artifact_a = runtime._history_database_key(PROJECT_ID, snapshot_a, purpose="artifact")

    assert len({session_a, session_b, artifact_a}) == 3


def test_history_cleanup_journal_is_durable_and_secretless() -> None:
    snapshot_id = uuid4()
    database_id = uuid4()
    record = history_cleanup.HistoryCleanupRecord(
        project_id=str(PROJECT_ID),
        snapshot_id=str(snapshot_id),
        purpose="session",
        database_id=str(database_id),
        origin="https://history.example.test",
        session_id=str(uuid4()),
    )

    history_cleanup.remember(record)

    assert history_cleanup.list_records() == [record]
    assert "password" not in history_cleanup._state_path().read_text(encoding="utf-8").lower()

    history_cleanup.forget(str(database_id))
    assert history_cleanup.list_records() == []


async def test_max_preview_capability_is_project_bound_and_live(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = 1_893_456_000
    expires = now + 900
    secret = "auth-secret"
    signature = runtime._max_preview_capability_signature(secret, str(PROJECT_ID), expires)
    token = f"v1.{expires}.{signature}"
    monkeypatch.setattr(runtime, "time", lambda: float(now))
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
    monkeypatch.setattr(runtime, "load_existing_auth_secret", lambda _project: secret)

    response = await runtime.validate_max_preview_capability(
        PROJECT_ID,
        runtime.MaxPreviewCapabilityValidateRequest(token=token),
        "test-token-test-token-test-token",
    )

    assert response.valid is True
    assert response.project_id == PROJECT_ID


@pytest.mark.parametrize("offset", [-1, 901])
async def test_max_preview_capability_rejects_expired_or_excessive_lifetime(
    monkeypatch: pytest.MonkeyPatch,
    offset: int,
) -> None:
    now = 1_893_456_000
    expires = now + offset
    signature = runtime._max_preview_capability_signature("auth-secret", str(PROJECT_ID), expires)
    monkeypatch.setattr(runtime, "time", lambda: float(now))

    with pytest.raises(OrchestratorError) as exc_info:
        await runtime.validate_max_preview_capability(
            PROJECT_ID,
            runtime.MaxPreviewCapabilityValidateRequest(token=f"v1.{expires}.{signature}"),
            "test-token-test-token-test-token",
        )

    assert exc_info.value.code == "unauthorized"


async def test_max_preview_capability_rejects_wrong_project_signature(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = 1_893_456_000
    expires = now + 600
    other_project = uuid4()
    signature = runtime._max_preview_capability_signature(
        "auth-secret", str(other_project), expires
    )
    monkeypatch.setattr(runtime, "time", lambda: float(now))
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

    with pytest.raises(OrchestratorError) as exc_info:
        await runtime.validate_max_preview_capability(
            PROJECT_ID,
            runtime.MaxPreviewCapabilityValidateRequest(token=f"v1.{expires}.{signature}"),
            "test-token-test-token-test-token",
        )

    assert exc_info.value.code == "unauthorized"


async def test_history_sweeper_releases_pre_container_journal_record(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot_id = uuid4()
    database_id = uuid4()
    history_cleanup.remember(
        history_cleanup.HistoryCleanupRecord(
            project_id=str(PROJECT_ID),
            snapshot_id=str(snapshot_id),
            purpose="artifact",
            database_id=str(database_id),
            created_epoch=1.0,
        )
    )
    monkeypatch.setattr(runtime, "history_preview_cleanup_candidates", AsyncMock(return_value=[]))
    monkeypatch.setattr(runtime, "history_preview_container_labels", AsyncMock(return_value={}))
    drop = AsyncMock()
    monkeypatch.setattr(runtime.postgres_admin, "drop_schema", drop)

    assert await runtime._sweep_history_previews() == 0

    drop.assert_awaited_once_with(database_id)
    assert history_cleanup.list_records() == []


async def test_history_sweeper_does_not_race_active_pre_container_provision(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot_id = uuid4()
    database_id = uuid4()
    history_cleanup.remember(
        history_cleanup.HistoryCleanupRecord(
            project_id=str(PROJECT_ID),
            snapshot_id=str(snapshot_id),
            purpose="session",
            database_id=str(database_id),
        )
    )
    monkeypatch.setattr(runtime, "history_preview_cleanup_candidates", AsyncMock(return_value=[]))
    labels = AsyncMock(return_value={})
    monkeypatch.setattr(runtime, "history_preview_container_labels", labels)
    drop = AsyncMock()
    monkeypatch.setattr(runtime.postgres_admin, "drop_schema", drop)

    assert await runtime._sweep_history_previews() == 0

    labels.assert_not_awaited()
    drop.assert_not_awaited()
    assert len(history_cleanup.list_records()) == 1
