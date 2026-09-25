from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from tests.test_project_machine_manifest import payload
from tests.test_restoration_machine_policy import backend
from yleum_orchestrator.core.errors import OrchestratorError
from yleum_orchestrator.core.project_machine import MachineManifest
from yleum_orchestrator.routers import workspace
from yleum_orchestrator.schemas.workspace import WorkspaceAgentBootstrapRequest


@pytest.mark.parametrize("portable,has_runtime", [(True, True), (False, True), (True, False)])
async def test_bootstrap_keeps_new_projects_on_their_ordinary_database(
    tmp_path, monkeypatch, portable, has_runtime,
):
    runtime_backend = backend(tmp_path)
    runtime_backend.client = SimpleNamespace(volumes=object())
    runtime_backend._lookup = lambda *_: None
    runtime_backend._container = lambda: None
    runtime_backend._project_postgres = lambda: None
    locked = []
    run_id = uuid4()

    @asynccontextmanager
    async def hold(_):
        locked.append(True)
        try:
            yield
        finally:
            locked.pop()

    def capabilities():
        assert locked, "capabilities must describe the admitted state while lock is held"
        return {"database_admin": "full"}

    runtime = SimpleNamespace(
        parts=lambda _: (None, runtime_backend), exists=lambda _: False,
        capabilities=capabilities,
    ) if has_runtime else None
    manager = SimpleNamespace(
        operation_lock=SimpleNamespace(hold=hold), machine_runtime=runtime,
    )
    monkeypatch.setattr(workspace, "verify_internal_token", lambda _: None)
    monkeypatch.setattr(workspace, "_workspace_provider", lambda _: None)
    monkeypatch.setattr(workspace, "_require_docker_resource_manager", lambda _: manager)
    monkeypatch.setattr(workspace, "_workspace_volume_identity", AsyncMock(return_value=(
        SimpleNamespace(workspace_id=runtime_backend.workspace_id), "source",
    )))
    monkeypatch.setattr(workspace, "_require_active_generation_lease", lambda _: (run_id, 7))
    monkeypatch.setattr(workspace, "_ensure_seed_workspace_files", AsyncMock(return_value=(
        {".omnia/cell.json": MachineManifest.model_validate(payload()).model_dump_json()}
        if portable else {}, False,
    )))
    with pytest.raises(OrchestratorError, match="workspace generation lease mismatch"):
        await workspace.bootstrap_workspace_agent(
            runtime_backend.workspace_id,
            WorkspaceAgentBootstrapRequest(generation_run_id=uuid4(), fencing_epoch=8),
        )
    response = await workspace.bootstrap_workspace_agent(
        runtime_backend.workspace_id,
        WorkspaceAgentBootstrapRequest(generation_run_id=run_id, fencing_epoch=7),
    )
    assert response.capabilities == ({"database_admin": "full"} if has_runtime else {})
    assert not (tmp_path / str(runtime_backend.workspace_id) / "data-policy.json").exists()
    assert not (tmp_path / str(runtime_backend.workspace_id) / "initial-database.json").exists()
    env = runtime_backend.project_database_env()
    assert env["PGUSER"] == "postgres"
    assert env["PGPASSWORD"] == "old-agent-password"


def test_bootstrap_request_no_longer_accepts_protection_enrollment():
    with pytest.raises(ValueError):
        WorkspaceAgentBootstrapRequest(
            generation_run_id=uuid4(), fencing_epoch=1, protect_existing_data=True,
        )
