"""Prospective command receipt boundary; isolated controller files, no daemon."""

from dataclasses import replace
from types import SimpleNamespace
from uuid import uuid4

import pytest

from tests.test_workspace_router import _client, _ready_provider
from tests.test_workspace_router import _internal_settings as _internal_settings
from yleum_orchestrator.routers import workspace
from yleum_orchestrator.schemas.workspace import WorkspaceAgentExecResponse

ORIGINAL_EXECUTOR = workspace._execute_agent_command_locked

HEADERS = {"X-Internal-Token": "test-internal-token-not-a-real-secret"}


@pytest.fixture
async def prepared(tmp_path, monkeypatch, _internal_settings):
    wid, oid = uuid4(), uuid4()
    provider, manager, docker, run = await _ready_provider(tmp_path, wid)
    monkeypatch.setattr(workspace, "build_workspace_provider", lambda _: provider)
    state = manager.state_store.load(wid)
    manager.machine_runtime = SimpleNamespace(exists=lambda _: False)
    calls = []
    from tests.test_project_machine_manifest import payload
    from yleum_orchestrator.core.project_machine import MachineManifest

    manifest = MachineManifest.model_validate(payload())
    monkeypatch.setattr(workspace, "_machine_manifest", lambda *a, **kw: manifest)

    async def execute(manager, workspace_id, request):
        calls.append(request.operation_id)
        before = workspace._workspace_identity_digest(
            {},
            manifest=None,
            environment_digest="e" * 64,
        )
        return WorkspaceAgentExecResponse(
            ok=True,
            exit_code=0,
            detail="ok",
            workspace_revision=request.expected_revision,
            operation_id=request.operation_id,
            before_identity=before,
            after_identity=before,
            environment_mutated=False,
        )

    monkeypatch.setattr(workspace, "_execute_agent_command_locked", execute, raising=False)
    body = {
        "operation_id": str(oid),
        "generation_run_id": str(run),
        "fencing_epoch": 4,
        "expected_revision": workspace._workspace_revision({}),
        "cmd": "npm run build",
        "timeout_seconds": 180,
        "task_role": "full_build",
        "track_receipt": True,
    }
    return wid, oid, manager, docker, run, state, body, calls


async def test_terminal_cold_status_with_new_current_fence_never_reexecutes(prepared):
    wid, oid, manager, _docker, run, state, body, calls = prepared
    async with _client() as client:
        first = await client.post(
            f"/internal/workspaces/{wid}/agent/exec", headers=HEADERS, json=body
        )
        assert first.status_code == 200, first.text
        receipt = first.json()["command_receipt"]
        assert receipt["dispatch_fencing_epoch"] == 4
        manager.state_store._persist_state(replace(state, active_generation_fencing_epoch=5))
        status = await client.get(
            f"/internal/workspaces/{wid}/agent/command-operations/{oid}",
            headers=HEADERS,
            params={"generation_run_id": str(run), "fencing_epoch": 5},
        )
        assert status.status_code == 200, status.text
        assert status.json()["receipt"] == receipt and status.json()["state"] == "terminal"
        assert "command_receipt" not in status.json()["terminal_response"]
        old = await client.get(
            f"/internal/workspaces/{wid}/agent/command-operations/{oid}",
            headers=HEADERS,
            params={"generation_run_id": str(run), "fencing_epoch": 4},
        )
        assert old.status_code == 409
    assert calls == [oid]


@pytest.mark.parametrize("drift", ["source", "incarnation", "owner", "run"])
async def test_terminal_receipt_never_certifies_changed_current_binding(
    prepared, monkeypatch, drift
):
    wid, oid, manager, docker, run, state, body, calls = prepared
    async with _client() as client:
        assert (
            await client.post(f"/internal/workspaces/{wid}/agent/exec", headers=HEADERS, json=body)
        ).status_code == 200
        if drift == "source":
            await docker.write_volume_files(
                state.resource_names.workspace_volume, {"x": b"changed"}
            )
        elif drift == "incarnation":

            async def identity(_name):
                return "f" * 64

            monkeypatch.setattr(docker, "source_volume_identity", identity)
        elif drift == "owner":
            manager.state_store._persist_state(replace(state, owner_id=uuid4()))
        elif drift == "run":
            manager.state_store._persist_state(replace(state, active_generation_run_id=uuid4()))
        status = await client.get(
            f"/internal/workspaces/{wid}/agent/command-operations/{oid}",
            headers=HEADERS,
            params={"generation_run_id": str(run), "fencing_epoch": 4},
        )
        assert status.status_code == 409, status.text
    assert calls == [oid]


async def test_unknown_legacy_status_not_upgraded_and_no_dispatch(prepared):
    wid, oid, _manager, _docker, run, _state, _body, calls = prepared
    async with _client() as client:
        status = await client.get(
            f"/internal/workspaces/{wid}/agent/command-operations/{oid}",
            headers=HEADERS,
            params={"generation_run_id": str(run), "fencing_epoch": 4},
        )
    assert status.status_code == 200, status.text
    assert status.json() == {
        "operation_id": str(oid),
        "state": "unknown",
        "receipt": None,
        "terminal_response": None,
    }
    assert calls == []


async def test_pending_after_effect_loss_never_replays_or_releases(prepared, monkeypatch):
    wid, oid, manager, _docker, run, _state, body, calls = prepared
    from yleum_orchestrator.core.cell_resources import CellIndeterminateOperation, LifecycleMutation

    async def lost(*args):
        calls.append(oid)
        raise RuntimeError("lost terminal acknowledgement")

    monkeypatch.setattr(workspace, "_execute_agent_command_locked", lost)
    async with _client() as client:
        url = f"/internal/workspaces/{wid}/agent/exec"
        assert (await client.post(url, headers=HEADERS, json=body)).status_code == 500
        status = await client.get(
            f"/internal/workspaces/{wid}/agent/command-operations/{oid}",
            headers=HEADERS,
            params={"generation_run_id": str(run), "fencing_epoch": 4},
        )
        assert status.json()["state"] == "pending", status.text
        assert (await client.post(url, headers=HEADERS, json=body)).status_code == 409
        assert (
            await client.post(url, headers=HEADERS, json={**body, "operation_id": str(uuid4())})
        ).status_code == 409
        assert (
            await client.post(url, headers=HEADERS, json={**body, "track_receipt": False})
        ).status_code == 409
    with pytest.raises(CellIndeterminateOperation):
        await manager.release_generation(
            wid, LifecycleMutation(uuid4(), 4, "d" * 64), generation_run_id=run
        )
    assert calls == [oid]


@pytest.mark.parametrize("changed", ["cmd", "timeout_seconds", "task_role", "expected_revision"])
async def test_same_id_changed_request_never_dispatches(prepared, changed):
    wid, oid, _manager, _docker, _run, _state, body, calls = prepared
    async with _client() as client:
        url = f"/internal/workspaces/{wid}/agent/exec"
        assert (await client.post(url, headers=HEADERS, json=body)).status_code == 200
        values = {
            "cmd": "different",
            "timeout_seconds": 181,
            "task_role": "fast_check",
            "expected_revision": "f" * 64,
        }
        result = await client.post(url, headers=HEADERS, json={**body, changed: values[changed]})
        assert result.status_code == 409, result.text
    assert calls == [oid]


@pytest.mark.parametrize("missing", ["runtime", "manifest"])
async def test_unsupported_tracking_denies_before_effect_or_intent(prepared, monkeypatch, missing):
    wid, _oid, manager, _docker, _run, _state, body, calls = prepared
    if missing == "runtime":
        manager.machine_runtime = None
    else:
        monkeypatch.setattr(workspace, "_machine_manifest", lambda *a, **kw: None)
    async with _client() as client:
        result = await client.post(
            f"/internal/workspaces/{wid}/agent/exec", headers=HEADERS, json=body
        )
    assert result.status_code == 409, result.text
    assert calls == []
    assert not (manager.state_store.root.parent / "command-operations" / str(wid)).exists()


async def test_untracked_legacy_shape_omits_new_receipt(prepared):
    wid, oid, _manager, _docker, _run, _state, body, calls = prepared
    async with _client() as client:
        result = await client.post(
            f"/internal/workspaces/{wid}/agent/exec",
            headers=HEADERS,
            json={**body, "track_receipt": False},
        )
    assert result.status_code == 200, result.text
    assert "command_receipt" not in result.json()
    assert (
        "compiled_asset_receipt" in result.json() and "project_migration_receipt" in result.json()
    )
    assert calls == [oid]


@pytest.mark.parametrize("boundary", ["intent", "terminal"])
async def test_strict_directory_fsync_failure_never_dispatches_or_certifies_twice(
    prepared,
    monkeypatch,
    boundary,
):
    from yleum_orchestrator.services import command_receipts

    wid, oid, manager, _docker, run, _state, body, calls = prepared
    journal = command_receipts.CommandJournal(manager.state_store.root, wid)
    original = command_receipts._sync_directory

    def fail(path):
        if path == journal.root:
            import json

            payload = json.loads((path / f"{oid}.json").read_text())
            if boundary == "intent" or payload.get("receipt") is not None:
                raise OSError("injected strict directory fsync failure")
        return original(path)

    monkeypatch.setattr(command_receipts, "_sync_directory", fail)
    async with _client() as client:
        url = f"/internal/workspaces/{wid}/agent/exec"
        assert (await client.post(url, headers=HEADERS, json=body)).status_code == 500
        get = f"/internal/workspaces/{wid}/agent/command-operations/{oid}"
        params = {"generation_run_id": str(run), "fencing_epoch": 4}
        blocked = await client.get(get, headers=HEADERS, params=params)
        if boundary == "terminal":
            assert blocked.status_code == 500  # Readable rename is not durable authority.
            assert calls == [oid]
        else:
            assert blocked.json()["state"] == "pending"
            assert calls == []
        monkeypatch.setattr(command_receipts, "_sync_directory", original)
        cold = await client.get(get, headers=HEADERS, params=params)
        assert cold.status_code == 200, cold.text
        assert cold.json()["state"] == ("terminal" if boundary == "terminal" else "pending")
        assert calls == ([oid] if boundary == "terminal" else [])


async def test_legacy_machine_record_cannot_be_retrofitted(prepared):
    from yleum_orchestrator.services.machine_identity import machine_identity_root
    from yleum_orchestrator.services.project_machine import write_controller_json

    wid, oid, manager, _docker, run, _state, body, calls = prepared
    write_controller_json(
        machine_identity_root(manager.state_store.root, wid) / "machine.json",
        {
            "workspace_id": str(wid),
            "operations": {str(oid): {"kind": "request", "state": "completed", "epoch": 4}},
        },
    )
    async with _client() as client:
        denied = await client.post(
            f"/internal/workspaces/{wid}/agent/exec", headers=HEADERS, json=body
        )
        assert denied.status_code == 409, denied.text
        status = await client.get(
            f"/internal/workspaces/{wid}/agent/command-operations/{oid}",
            headers=HEADERS,
            params={"generation_run_id": str(run), "fencing_epoch": 4},
        )
        assert status.json()["state"] == "unknown"
    assert calls == []


async def test_pending_command_blocks_source_write_and_machine_writer(prepared, monkeypatch):
    from yleum_orchestrator.core.cell_resources import CellIndeterminateOperation
    from yleum_orchestrator.services.adaptation_recovery_barrier import (
        require_machine_writers_allowed,
    )
    from yleum_orchestrator.services.machine_identity import machine_identity_root

    wid, _oid, manager, _docker, run, _state, body, _calls = prepared

    async def lost(*args):
        raise RuntimeError("lost command")

    monkeypatch.setattr(workspace, "_execute_agent_command_locked", lost)
    async with _client() as client:
        assert (
            await client.post(f"/internal/workspaces/{wid}/agent/exec", headers=HEADERS, json=body)
        ).status_code == 500
        write = await client.post(
            f"/internal/workspaces/{wid}/agent/write-files",
            headers=HEADERS,
            json={
                "generation_run_id": str(run),
                "fencing_epoch": 4,
                "expected_revision": body["expected_revision"],
                "files": {"x": "changed"},
            },
        )
        assert write.status_code == 409, write.text
    with pytest.raises(CellIndeterminateOperation):
        require_machine_writers_allowed(
            machine_identity_root(manager.state_store.root, wid) / "machine.json", action="ensure"
        )


@pytest.mark.parametrize("drift", ["fence", "source", "incarnation"])
async def test_post_dispatch_drift_leaves_only_pending_intent(prepared, monkeypatch, drift):
    from yleum_orchestrator.services.command_receipts import CommandJournal

    wid, oid, manager, docker, _run, state, body, calls = prepared
    original = workspace._execute_agent_command_locked

    async def execute(*args):
        response = await original(*args)
        if drift == "fence":
            manager.state_store._persist_state(replace(state, active_generation_fencing_epoch=5))
        elif drift == "source":
            await docker.write_volume_files(
                state.resource_names.workspace_volume, {"x": b"changed"}
            )
        else:

            async def identity(_name):
                return "f" * 64

            monkeypatch.setattr(docker, "source_volume_identity", identity)
        return response

    monkeypatch.setattr(workspace, "_execute_agent_command_locked", execute)
    async with _client() as client:
        response = await client.post(
            f"/internal/workspaces/{wid}/agent/exec", headers=HEADERS, json=body
        )
    assert response.status_code == 409, response.text
    assert (
        calls == [oid] and CommandJournal(manager.state_store.root, wid).load(oid).receipt is None
    )


async def test_portable_production_route_dispatch_is_bracketed_by_durable_binding(
    prepared, monkeypatch
):
    from unittest.mock import AsyncMock

    from tests.test_project_machine_manifest import payload
    from yleum_orchestrator.core.project_machine import MachineManifest
    from yleum_orchestrator.services.command_receipts import CommandJournal
    from yleum_orchestrator.services.docker_cell_resources import DockerCommandResult
    from yleum_orchestrator.services.machine_adapter import MachineAdapter

    wid, oid, manager, _docker, _run, state, body, _calls = prepared
    journal = CommandJournal(manager.state_store.root, wid)
    events = []

    async def ensure(*args):
        assert journal.load(oid).receipt is None
        events.append("ensure-with-intent")

    async def execute(*args):
        assert journal.load(oid).receipt is None
        events.append("physical-command-with-intent")
        return DockerCommandResult(exit_code=0, output="ok", timed_out=False)

    async def store(**kwargs):
        assert journal.load(oid).receipt is None
        events.append("transport-result")

    machine = SimpleNamespace(
        ensure=ensure,
        request_status=AsyncMock(return_value=SimpleNamespace(transport_response=None)),
        store_transport_response=store,
    )
    backend = SimpleNamespace(
        workspace_volume=state.resource_names.workspace_volume,
        configure_cache_identity=lambda **kw: None,
        environment_digest=lambda: "e" * 64,
    )
    manager.machine_runtime = SimpleNamespace(
        exists=lambda _: False,
        parts=lambda _: (machine, backend),
        _request_digest=MachineAdapter._request_digest,
        guarded_execute=execute,
        compilation_receipt=lambda *a: None,
        migration_receipt=lambda *a: None,
    )
    monkeypatch.setattr(workspace, "_execute_agent_command_locked", ORIGINAL_EXECUTOR)
    monkeypatch.setattr(
        workspace,
        "_prepare_portable_write",
        AsyncMock(return_value=MachineManifest.model_validate(payload())),
    )
    async with _client() as client:
        response = await client.post(
            f"/internal/workspaces/{wid}/agent/exec", headers=HEADERS, json=body
        )
    assert response.status_code == 200, response.text
    assert events == ["ensure-with-intent", "physical-command-with-intent", "transport-result"]
    assert journal.load(oid).receipt is not None


async def test_canonical_binding_stores_no_command_and_never_drops_nullable_receipts(prepared):
    import hashlib
    import json

    from yleum_orchestrator.schemas.workspace import WorkspaceAgentExecRequest
    from yleum_orchestrator.services.command_receipts import CommandJournal

    wid, oid, manager, _docker, _run, _state, body, _calls = prepared
    body["cmd"] = "private-command-marker-данные"
    async with _client() as client:
        response = await client.post(
            f"/internal/workspaces/{wid}/agent/exec", headers=HEADERS, json=body
        )
    assert response.status_code == 200, response.text
    journal = CommandJournal(manager.state_store.root, wid)
    raw = (journal.root / f"{oid}.json").read_text()
    assert body["cmd"] not in raw and "private-command-marker" not in raw
    record = journal.load(oid)

    def canonical(value):
        return hashlib.sha256(
            json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
        ).hexdigest()

    expected = {
        "protocol": "tracked-command/v1",
        "workspace_id": str(wid),
        "request": WorkspaceAgentExecRequest.model_validate(body).model_dump(mode="json"),
    }
    assert record.binding.request_sha256 == canonical(expected)
    assert record.receipt.response_sha256 == canonical(record.response.model_dump(mode="json"))
    assert record.response.project_migration_receipt is None
    assert record.response.compiled_asset_receipt is None


async def test_finished_command_scope_cannot_authorize_late_inherited_writer(prepared, monkeypatch):
    from yleum_orchestrator.core.cell_resources import CellIndeterminateOperation
    from yleum_orchestrator.services.command_receipts import CommandJournal

    wid, oid, manager, _docker, _run, _state, body, _calls = prepared
    original = workspace._execute_agent_command_locked
    checked = []

    async def execute(*args):
        response = await original(*args)
        journal = CommandJournal(manager.state_store.root, wid)
        binding = journal.load(oid).binding
        journal.finish(binding, response)
        with pytest.raises(CellIndeterminateOperation):
            journal.require_clear()
        checked.append(True)
        raise RuntimeError("simulated terminal committed within inherited scope")

    monkeypatch.setattr(workspace, "_execute_agent_command_locked", execute)
    async with _client() as client:
        assert (
            await client.post(f"/internal/workspaces/{wid}/agent/exec", headers=HEADERS, json=body)
        ).status_code == 500
    assert checked == [True]


async def test_status_rechecks_current_lease_after_last_physical_probe(prepared, monkeypatch):
    wid, oid, manager, docker, run, state, body, _calls = prepared
    async with _client() as client:
        assert (
            await client.post(f"/internal/workspaces/{wid}/agent/exec", headers=HEADERS, json=body)
        ).status_code == 200
        original = docker.source_volume_identity
        probes = 0

        async def identity(name):
            nonlocal probes
            probes += 1
            if probes == 2:
                manager.state_store._persist_state(
                    replace(state, active_generation_fencing_epoch=5)
                )
            return await original(name)

        monkeypatch.setattr(docker, "source_volume_identity", identity)
        status = await client.get(
            f"/internal/workspaces/{wid}/agent/command-operations/{oid}",
            headers=HEADERS,
            params={"generation_run_id": str(run), "fencing_epoch": 4},
        )
    assert probes == 2 and status.status_code == 409, status.text
