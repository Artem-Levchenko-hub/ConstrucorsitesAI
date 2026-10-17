"""Candidate barriers deny old authority even before the source SQL lease is released."""

from types import SimpleNamespace
from uuid import uuid4

import pytest

from tests.test_workspace_router import _client, _ready_provider
from tests.test_workspace_router import _internal_settings as _internal_settings
from yleum_orchestrator.core.cell_resources import CellIdentityConflict, LifecycleMutation
from yleum_orchestrator.routers import workspace
from yleum_orchestrator.services.machine_identity import machine_identity_root
from yleum_orchestrator.services.project_machine import write_controller_json


@pytest.mark.parametrize("action", ["write", "exec", "wake", "ensure", "release"])
async def test_persisted_candidate_barrier_denies_old_authority_before_any_effect(
    tmp_path,
    monkeypatch,
    _internal_settings,
    action,
):
    wid = uuid4()
    provider, manager, docker, run = await _ready_provider(tmp_path, wid)
    monkeypatch.setattr(workspace, "build_workspace_provider", lambda _: provider)
    state = manager.state_store.load(wid)
    path = machine_identity_root(manager.state_store.root, wid) / "machine.json"
    write_controller_json(
        path,
        {
            "workspace_id": str(wid),
            "epoch": 4,
            "operations": {},
            "adaptation_recovery_barrier": {
                "version": 1,
                "state": "held",
                "transition_id": str(uuid4()),
            },
        },
    )
    before = state.to_dict() if hasattr(state, "to_dict") else state

    async def forbidden(*args, **kwargs):
        pytest.fail("old authority reached physical effect")

    manager.machine_runtime = SimpleNamespace(exists=lambda _: False, halt=forbidden)
    monkeypatch.setattr(docker, "write_volume_files", forbidden)
    monkeypatch.setattr(docker, "delete_volume_paths", forbidden)
    if action in {"write", "exec"}:
        async with _client() as http:
            body = {"generation_run_id": str(run), "fencing_epoch": 4}
            if action == "write":
                body.update(
                    files={"a.txt": "late"},
                    deletes=[],
                    expected_revision=workspace._workspace_revision({}),
                )
                route = "write-files"
            else:
                body.update(
                    operation_id=str(uuid4()),
                    cmd="echo stale",
                    expected_revision=workspace._workspace_revision({}),
                )
                route = "exec"
            response = await http.post(
                f"/internal/workspaces/{wid}/agent/{route}",
                headers={"X-Internal-Token": "test-internal-token-not-a-real-secret"},
                json=body,
            )
            assert response.status_code == 409, response.text
    else:
        with pytest.raises(CellIdentityConflict):
            mutation = LifecycleMutation(uuid4(), 5, "e" * 64)
            if action == "wake":
                await manager.wake(wid, mutation)
            elif action == "ensure":
                await manager.ensure(manager._spec_from_state(state), mutation)
            else:
                await manager.release_generation(wid, mutation, generation_run_id=run)
    after = manager.state_store.load(wid)
    assert (after.to_dict() if hasattr(after, "to_dict") else after) == before


@pytest.mark.parametrize("action", ["boundary", "config", "restore", "checkpoint"])
async def test_adapter_entrypoints_deny_barrier_before_metadata_or_physical_access(
    tmp_path, monkeypatch, action,
):
    from yleum_orchestrator.services.machine_adapter import MachineAdapter
    wid = uuid4()
    _, manager, _, _ = await _ready_provider(tmp_path, wid)
    state = manager.state_store.load(wid)
    path = machine_identity_root(manager.state_store.root, wid) / "machine.json"
    write_controller_json(path, {"adaptation_recovery_barrier": {"version": "future"}})
    adapter = MachineAdapter(manager, SimpleNamespace())

    def forbidden(*args, **kwargs):
        pytest.fail("old adapter authority reached metadata/physical implementation")

    monkeypatch.setattr(adapter, "parts", forbidden)
    monkeypatch.setattr(adapter, "_start_boundary_impl", forbidden)
    with pytest.raises(CellIdentityConflict):
        if action == "boundary":
            adapter._start_boundary(state, None, None, 4)
        elif action == "config":
            await adapter.apply_owner_business_config(state, version=1, config={})
        elif action == "restore":
            await adapter.restore_payload(state, b"untrusted")
        else:
            await adapter.checkpoint(state)
