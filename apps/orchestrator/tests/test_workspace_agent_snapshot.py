"""Post-command source inspection never initializes or repairs the workspace."""

from __future__ import annotations

from dataclasses import replace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from tests.test_workspace_router import _client, _CountingBootstrapDocker, _ready_provider
from yleum_orchestrator.core.config import get_settings
from yleum_orchestrator.routers import workspace
from yleum_orchestrator.services.generation_reader_inventory import ReaderInventoryUnavailable

_TOKEN = "test-internal-token-not-a-real-secret"


@pytest.fixture(autouse=True)
def _internal_settings(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://test:test@127.0.0.1:5432/test")
    monkeypatch.setenv("INTERNAL_TOKEN", _TOKEN)
    monkeypatch.delenv("WORKSPACE_PROVIDER", raising=False)
    monkeypatch.delenv("DOCKER_OWNER_CANARY_ENABLED", raising=False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
async def snapshot_cell(monkeypatch, tmp_path):
    workspace_id, run_id = uuid4(), uuid4()
    docker = _CountingBootstrapDocker()
    provider, manager, _, _ = await _ready_provider(
        tmp_path, workspace_id, generation_run_id=run_id, docker=docker,
    )
    state = manager.state_store.load(workspace_id)
    assert state is not None and state.resource_names is not None
    monkeypatch.setattr(workspace, "build_workspace_provider", lambda _settings: provider)
    seed = AsyncMock(side_effect=AssertionError("a snapshot must never seed source"))
    monkeypatch.setattr(workspace, "_ensure_seed_workspace_files", seed)
    docker.clear_calls = docker.write_calls = 0
    return workspace_id, run_id, manager, docker, state, seed


def _params(run_id, epoch=4):
    return {"generation_run_id": str(run_id), "fencing_epoch": epoch}


@pytest.mark.parametrize("files", [{}, {"src/app/page.tsx": "owner product source\n"}])
async def test_snapshot_reads_exact_source_without_initializing(snapshot_cell, files):
    workspace_id, run_id, _manager, docker, state, seed = snapshot_cell
    volume = docker.volumes[state.resource_names.workspace_volume]
    volume.files.update({path: content.encode() for path, content in files.items()})
    async with _client() as client:
        response = await client.get(
            f"/internal/workspaces/{workspace_id}/agent/snapshot",
            headers={"X-Internal-Token": _TOKEN}, params=_params(run_id),
        )
    assert response.status_code == 200
    assert response.json() == {
        "files": files, "seeded_from_project": False,
        "generation_run_id": str(run_id), "fencing_epoch": 4,
        "workspace_revision": workspace._workspace_revision(files),
    }
    assert volume.files == {path: content.encode() for path, content in files.items()}
    assert docker.clear_calls == docker.write_calls == 0
    seed.assert_not_awaited()


@pytest.mark.parametrize("token", [None, "wrong-token"])
async def test_snapshot_authenticates_before_provider_lookup(monkeypatch, token):
    provider = AsyncMock(side_effect=AssertionError("unauthenticated provider lookup"))
    monkeypatch.setattr(workspace, "_workspace_provider", provider)
    async with _client() as client:
        response = await client.get(
            f"/internal/workspaces/{uuid4()}/agent/snapshot",
            headers={"X-Internal-Token": token} if token else {}, params=_params(uuid4()),
        )
    assert response.status_code == 401
    provider.assert_not_called()


@pytest.mark.parametrize("bad_params", [
    {}, {"generation_run_id": "not-a-uuid", "fencing_epoch": 4},
    {"generation_run_id": str(uuid4()), "fencing_epoch": 0},
    {"generation_run_id": str(uuid4()), "fencing_epoch": -1},
])
async def test_snapshot_rejects_invalid_query_before_provider(monkeypatch, bad_params):
    provider = AsyncMock(side_effect=AssertionError("invalid query provider lookup"))
    monkeypatch.setattr(workspace, "_workspace_provider", provider)
    async with _client() as client:
        response = await client.get(
            f"/internal/workspaces/{uuid4()}/agent/snapshot",
            headers={"X-Internal-Token": _TOKEN}, params=bad_params,
        )
    assert response.status_code == 422
    provider.assert_not_called()


@pytest.mark.parametrize("failure", ["foreign_run", "stale_epoch", "no_lease", "foreign_volume"])
async def test_snapshot_rejects_changed_authority_before_source_read(
    monkeypatch, snapshot_cell, failure,
):
    workspace_id, run_id, manager, docker, state, seed = snapshot_cell
    params = _params(run_id)
    if failure == "foreign_run":
        params["generation_run_id"] = str(uuid4())
    elif failure == "stale_epoch":
        params["fencing_epoch"] = 5
    elif failure == "no_lease":
        manager.state_store._persist_state(replace(
            state, active_generation_run_id=None, active_generation_fencing_epoch=None,
        ))
    else:
        volume = docker.volumes[state.resource_names.workspace_volume]
        docker.volumes[volume.name] = replace(
            volume, labels={**volume.labels, "omnia.owner_id": str(uuid4())},
        )
    read = AsyncMock(side_effect=AssertionError("authority must be checked before reading"))
    monkeypatch.setattr(workspace, "_read_agent_workspace_files", read)
    async with _client() as client:
        response = await client.get(
            f"/internal/workspaces/{workspace_id}/agent/snapshot",
            headers={"X-Internal-Token": _TOKEN}, params=params,
        )
    assert response.status_code == 409
    read.assert_not_awaited()
    seed.assert_not_awaited()
    assert docker.clear_calls == docker.write_calls == 0


async def test_snapshot_readiness_denial_propagates_without_source_mutation(
    monkeypatch, snapshot_cell,
):
    workspace_id, run_id, _manager, docker, _state, seed = snapshot_cell
    read = AsyncMock(side_effect=ReaderInventoryUnavailable(
        "reader inventory is incompatible or stale",
    ))
    monkeypatch.setattr(workspace, "source_volume_call", read)
    async with _client() as client:
        response = await client.get(
            f"/internal/workspaces/{workspace_id}/agent/snapshot",
            headers={"X-Internal-Token": _TOKEN}, params=_params(run_id),
        )
    assert response.status_code == 503
    assert response.json()["error"] == {
        "code": "workspace_busy", "message": "reader inventory is incompatible or stale",
    }
    read.assert_awaited_once()
    assert read.await_args.args[2] == "read_workspace_source_files"
    seed.assert_not_awaited()
    assert docker.clear_calls == docker.write_calls == 0
