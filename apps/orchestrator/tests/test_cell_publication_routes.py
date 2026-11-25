from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID

import pytest

from tests.test_cell_publication import request
from yleum_orchestrator.core.cell_resources import CellIdentityConflict
from yleum_orchestrator.core.errors import OrchestratorError
from yleum_orchestrator.routers import cell_publication as routes
from yleum_orchestrator.schemas.runtime import DeployResponse


async def test_publication_route_rejects_project_mismatch_before_dispatch(monkeypatch):
    dispatch = AsyncMock()
    monkeypatch.setattr(routes, "verify_internal_token", lambda _: None)
    monkeypatch.setattr(
        routes, "get_cell_publication_service", lambda: SimpleNamespace(submit=dispatch)
    )
    with pytest.raises(OrchestratorError) as error:
        await routes.publish_cell(UUID(int=99), request(), "test")
    assert error.value.status_code == 409
    dispatch.assert_not_awaited()


async def test_configuration_revoke_is_empty_dict_not_noop(monkeypatch):
    configure = AsyncMock(return_value={"applied": True})
    monkeypatch.setattr(routes, "verify_internal_token", lambda _: None)
    monkeypatch.setattr(
        routes,
        "get_cell_publication_service",
        lambda: SimpleNamespace(configure=configure),
    )
    body = routes.PublicCellConfigRequest(owner_id=UUID(int=3), runtime_env={})
    assert await routes.configure_cell(UUID(int=2), body, "test") == {"applied": True}
    assert configure.await_args.kwargs["runtime_env"] == {}


async def test_controller_exception_does_not_disclose_private_details(monkeypatch):
    submit = AsyncMock(side_effect=CellIdentityConflict("private-canary-secret"))
    monkeypatch.setattr(routes, "verify_internal_token", lambda _: None)
    monkeypatch.setattr(
        routes, "get_cell_publication_service", lambda: SimpleNamespace(submit=submit)
    )
    with pytest.raises(OrchestratorError) as error:
        await routes.publish_cell(UUID(int=2), request(), "test")
    assert "private-canary" not in str(error.value)
    assert error.value.status_code == 409


def test_exact_snapshot_binding_survives_durable_status_roundtrip():
    value = DeployResponse(
        project_id=UUID(int=2),
        phase="done",
        snapshot_id=UUID(int=4),
        commit_sha="a" * 40,
    )
    restored = DeployResponse.model_validate_json(value.model_dump_json())
    assert restored.snapshot_id == UUID(int=4)
    assert restored.commit_sha == "a" * 40


async def test_monitoring_uses_terminal_releases(monkeypatch):
    project_id = UUID(int=2)
    now = datetime.now(UTC)
    first = now - timedelta(days=2)
    checked = []

    def history(_):
        return [
            DeployResponse(project_id=project_id, phase="done", run_id="first",
                           prod_url="https://example.test/", finished_at=first.isoformat()),
            DeployResponse(project_id=project_id, phase="done", run_id="shortcut",
                           detail="already_current", finished_at=now.isoformat()),
            DeployResponse(project_id=project_id, phase="failed", run_id="failure",
                           error="fixture-only message", finished_at=now.isoformat()),
        ]

    monkeypatch.setattr(routes, "verify_internal_token", checked.append)
    monkeypatch.setattr(
        routes, "get_cell_publication_service", lambda: SimpleNamespace(history=history),
    )
    body = routes.PublicationHistoryRequest(
        project_ids={project_id}, since=now - timedelta(hours=24),
    )
    result = await routes.monitoring_publication_history(body, "fixture")
    assert checked == ["fixture"]
    assert result.first_success == {project_id: first}
    assert [item.run_id for item in result.facts] == ["failure"]
    assert "fixture-only" not in result.model_dump_json()
    assert "example.test" not in result.model_dump_json()
