from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from omnia_orchestrator.core.errors import OrchestratorError
from omnia_orchestrator.routers import workspace
from omnia_orchestrator.schemas.workspace import WorkspaceAgentBootstrapRequest
from omnia_orchestrator.services.restoration_database import load_policy
from tests.test_restoration_machine_policy import backend


@pytest.mark.parametrize("portable,has_runtime,legacy_volume", [
    (True, True, False), (False, True, False), (True, False, False), (True, True, True),
])
async def test_bootstrap_advertises_fresh_protection_under_lease_lock(
    tmp_path, monkeypatch, portable, has_runtime, legacy_volume,
):
    runtime_backend = backend(tmp_path)
    runtime_backend.client = SimpleNamespace(volumes=object())
    runtime_backend._lookup = lambda *_: object() if legacy_volume else None
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

    def capabilities(_):
        assert locked, "capabilities must describe the admitted state while lock is held"
        return {"database_admin": "protected" if load_policy(runtime_backend) else "full"}

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
        {".omnia/cell.json": "{}"} if portable else {}, False,
    )))
    with pytest.raises(OrchestratorError, match="workspace generation lease mismatch"):
        await workspace.bootstrap_workspace_agent(
            runtime_backend.workspace_id,
            WorkspaceAgentBootstrapRequest(generation_run_id=uuid4(), fencing_epoch=8),
        )
    assert load_policy(runtime_backend) is None
    response = await workspace.bootstrap_workspace_agent(
        runtime_backend.workspace_id,
        WorkspaceAgentBootstrapRequest(generation_run_id=run_id, fencing_epoch=7),
    )
    expected = portable and has_runtime and not legacy_volume
    assert (load_policy(runtime_backend) is not None) is expected
    assert response.capabilities == (
        {"database_admin": "protected" if expected else "full"} if has_runtime else {}
    )
    if expected:
        assert runtime_backend.project_database_env()["PGUSER"] == "omnia_runtime"
