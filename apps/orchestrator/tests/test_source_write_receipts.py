"""Controller-owned file effects; no Docker daemon or external service."""

import asyncio
from dataclasses import replace
from uuid import uuid4

import pytest

from tests.test_workspace_router import _client, _ready_provider
from tests.test_workspace_router import _internal_settings as _internal_settings
from yleum_orchestrator.routers import workspace

HEADERS = {"X-Internal-Token": "test-internal-token-not-a-real-secret"}


@pytest.mark.usefixtures("_internal_settings")
async def test_ack_lost_replays_receipt_not_file_effect(tmp_path, monkeypatch):
    workspace_id, operation_id = uuid4(), uuid4()
    provider, manager, docker, run_id = await _ready_provider(tmp_path, workspace_id)
    monkeypatch.setattr(workspace, "build_workspace_provider", lambda _settings: provider)
    writes = 0
    original = docker.write_volume_files

    async def write(name, files):
        nonlocal writes
        writes += 1
        await original(name, files)

    monkeypatch.setattr(docker, "write_volume_files", write)
    body = {
        "operation_id": str(operation_id),
        "generation_run_id": str(run_id),
        "fencing_epoch": 4,
        "expected_revision": workspace._workspace_revision({}),
        "files": {"src/result.txt": "once"},
        "deletes": [],
    }
    url = f"/internal/workspaces/{workspace_id}/agent/write-files"
    async with _client() as client:
        first = await client.post(url, headers=HEADERS, json=body)
        assert first.status_code == 200, first.text
        second = await client.post(url, headers=HEADERS, json=body)
        assert second.status_code == 200, second.text
        assert first.json() == second.json()
        status = await client.get(
            f"/internal/workspaces/{workspace_id}/agent/write-operations/{operation_id}",
            headers=HEADERS,
            params={"generation_run_id": str(run_id)},
        )
    assert status.status_code == 200, status.text
    assert status.json()["state"] == "terminal"
    assert status.json()["receipt"] == first.json()["receipt"]
    assert writes == 1
    assert not (
        manager.state_store.root.parent / "project-machines" / str(workspace_id) / "machine.json"
    ).exists()


@pytest.fixture
async def prepared(tmp_path, monkeypatch, _internal_settings):
    wid, oid = uuid4(), uuid4()
    provider, manager, docker, run = await _ready_provider(tmp_path, wid)
    monkeypatch.setattr(workspace, "build_workspace_provider", lambda _: provider)
    state = manager.state_store.load(wid)
    body = {
        "operation_id": str(oid),
        "generation_run_id": str(run),
        "fencing_epoch": 4,
        "expected_revision": workspace._workspace_revision({}),
        "files": {"src/a.txt": "new"},
        "deletes": [],
    }
    return wid, oid, manager, docker, run, state, body


@pytest.mark.parametrize("boundary", ["prepare", "partial_delete", "after_write"])
async def test_preterminal_loss_is_pending_blocks_replay_and_release(
    prepared, monkeypatch, boundary
):
    from yleum_orchestrator.core.cell_resources import CellIndeterminateOperation, LifecycleMutation
    from yleum_orchestrator.services.source_write_journal import SourceWriteJournal

    wid, oid, manager, docker, run, state, body = prepared
    calls = []
    if boundary == "partial_delete":
        await docker.write_volume_files(state.resource_names.workspace_volume, {"old.txt": b"old"})
        body["deletes"] = ["old.txt"]
        body["expected_revision"] = workspace._workspace_revision({"old.txt": "old"})
    original = docker.write_volume_files

    async def write(name, files):
        calls.append("write")
        if boundary == "partial_delete":
            raise RuntimeError("simulated controller loss")
        await original(name, files)

    async def prepare(*args):
        raise RuntimeError("simulated prepare loss")

    def finish(*args, **kwargs):
        raise RuntimeError("simulated terminal persistence loss")

    monkeypatch.setattr(docker, "write_volume_files", write)
    if boundary == "prepare":
        monkeypatch.setattr(workspace, "_prepare_portable_write", prepare)
    if boundary == "after_write":
        monkeypatch.setattr(SourceWriteJournal, "finish", finish)
    async with _client() as client:
        url = f"/internal/workspaces/{wid}/agent/write-files"
        assert (await client.post(url, headers=HEADERS, json=body)).status_code == 500
        status = await client.get(
            f"/internal/workspaces/{wid}/agent/write-operations/{oid}",
            headers=HEADERS,
            params={"generation_run_id": str(run)},
        )
        assert status.json() == {"operation_id": str(oid), "state": "pending", "receipt": None}
        assert (await client.post(url, headers=HEADERS, json=body)).status_code == 409
        assert (
            await client.post(url, headers=HEADERS, json={**body, "operation_id": str(uuid4())})
        ).status_code == 409
        assert (
            await client.post(
                url, headers=HEADERS, json={k: v for k, v in body.items() if k != "operation_id"}
            )
        ).status_code == 409
    with pytest.raises(CellIndeterminateOperation):
        await manager.release_generation(
            wid, LifecycleMutation(uuid4(), 4, "d" * 64), generation_run_id=run
        )
    assert calls == ([] if boundary == "prepare" else ["write"])
    if boundary == "partial_delete":
        assert "old.txt" not in await docker.read_volume_files(
            state.resource_names.workspace_volume
        )
    assert manager.state_store.load(wid).active_generation_run_id == run


@pytest.mark.parametrize("changed", ["payload", "fence", "run", "revision"])
async def test_completed_id_cannot_be_rebound(prepared, changed):
    wid, _oid, _manager, docker, _run, state, body = prepared
    async with _client() as client:
        url = f"/internal/workspaces/{wid}/agent/write-files"
        assert (await client.post(url, headers=HEADERS, json=body)).status_code == 200
        updates = {
            "payload": {"files": {"src/a.txt": "foreign"}},
            "fence": {"fencing_epoch": 5},
            "run": {"generation_run_id": str(uuid4())},
            "revision": {"expected_revision": "f" * 64},
        }[changed]
        assert (
            await client.post(url, headers=HEADERS, json={**body, **updates})
        ).status_code == 409
    assert (await docker.read_volume_files(state.resource_names.workspace_volume))[
        "src/a.txt"
    ] == b"new"


async def test_stale_equal_source_is_not_a_tracked_receipt(prepared):
    wid, _oid, _manager, docker, _run, state, body = prepared
    await docker.write_volume_files(state.resource_names.workspace_volume, {"src/a.txt": b"new"})
    async with _client() as client:
        response = await client.post(
            f"/internal/workspaces/{wid}/agent/write-files", headers=HEADERS, json=body
        )
    assert response.status_code == 409


async def test_historical_status_after_release_never_reads_source(prepared, monkeypatch):
    wid, oid, manager, _docker, run, state, body = prepared
    async with _client() as client:
        response = await client.post(
            f"/internal/workspaces/{wid}/agent/write-files", headers=HEADERS, json=body
        )
        assert response.status_code == 200
        manager.state_store._persist_state(
            replace(state, active_generation_run_id=None, active_generation_fencing_epoch=None)
        )

        async def forbidden(*args):
            raise AssertionError("receipt lookup read source")

        monkeypatch.setattr(workspace, "_read_agent_workspace_files", forbidden)
        url = f"/internal/workspaces/{wid}/agent/write-operations/{oid}"
        status = await client.get(url, headers=HEADERS, params={"generation_run_id": str(run)})
        assert status.status_code == 200
        assert status.json()["receipt"] == response.json()["receipt"]
        manager.state_store._persist_state(replace(state, active_generation_run_id=uuid4()))
        assert (
            await client.get(url, headers=HEADERS, params={"generation_run_id": str(run)})
        ).status_code == 409


async def test_unknown_and_foreign_status_fail_closed(prepared):
    wid, oid, _manager, _docker, run, _state, _body = prepared
    async with _client() as client:
        url = f"/internal/workspaces/{wid}/agent/write-operations/{oid}"
        assert (
            await client.get(url, headers=HEADERS, params={"generation_run_id": str(run)})
        ).status_code == 409
        assert (await client.get(url, params={"generation_run_id": str(run)})).status_code == 401


async def test_concurrent_retry_waits_then_returns_exact_receipt(prepared, monkeypatch):
    wid, _oid, _manager, docker, _run, _state, body = prepared
    entered, resume = asyncio.Event(), asyncio.Event()
    original = docker.write_volume_files
    calls = 0

    async def write(name, files):
        nonlocal calls
        calls += 1
        entered.set()
        await resume.wait()
        await original(name, files)

    monkeypatch.setattr(docker, "write_volume_files", write)
    async with _client() as client:
        url = f"/internal/workspaces/{wid}/agent/write-files"
        first = asyncio.create_task(client.post(url, headers=HEADERS, json=body))
        await asyncio.wait_for(entered.wait(), 5)
        second = asyncio.create_task(client.post(url, headers=HEADERS, json=body))
        resume.set()
        responses = await asyncio.wait_for(asyncio.gather(first, second), 5)
    assert [response.status_code for response in responses] == [200, 200]
    assert responses[0].json() == responses[1].json()
    assert calls == 1


async def test_physical_identity_drift_after_effect_cannot_mint_receipt(prepared, monkeypatch):
    from yleum_orchestrator.services.source_write_journal import SourceWriteJournal

    wid, oid, manager, docker, _run, state, body = prepared
    original = docker.write_volume_files

    async def write(name, files):
        await original(name, files)
        manager.state_store._persist_state(replace(state, owner_id=uuid4()))

    monkeypatch.setattr(docker, "write_volume_files", write)
    async with _client() as client:
        response = await client.post(
            f"/internal/workspaces/{wid}/agent/write-files",
            headers=HEADERS,
            json=body,
        )
    assert response.status_code == 409
    assert SourceWriteJournal(manager.state_store.root, wid).load(oid).receipt is None


async def test_pending_blocks_new_lease_claim(prepared, monkeypatch):
    from yleum_orchestrator.core.cell_resources import (
        CellIndeterminateOperation,
        LifecycleMutation,
    )
    from yleum_orchestrator.core.workspace_provider import WorkspaceSpec

    wid, _oid, manager, _docker, _run, state, body = prepared

    async def fail(*args):
        raise RuntimeError("simulated controller loss")

    monkeypatch.setattr(workspace, "_prepare_portable_write", fail)
    async with _client() as client:
        assert (
            await client.post(
                f"/internal/workspaces/{wid}/agent/write-files",
                headers=HEADERS,
                json=body,
            )
        ).status_code == 500
    # The workspace lock guard precedes admission/capacity/lease transitions.
    with pytest.raises(CellIndeterminateOperation):
        await manager.ensure(
            WorkspaceSpec(
                workspace_id=wid,
                project_id=state.project_id,
                owner_id=state.owner_id,
                profile_version=state.profile_version,
                generation_run_id=uuid4(),
            ),
            LifecycleMutation(uuid4(), 5, "d" * 64),
        )


async def test_portable_status_binds_active_volume_without_credential_initialization(prepared):
    from types import SimpleNamespace

    from yleum_orchestrator.services.machine_identity import machine_identity_root
    from yleum_orchestrator.services.project_machine import write_controller_json
    from yleum_orchestrator.services.source_write_journal import (
        SourceWriteJournal,
        SourceWriteRecord,
    )

    wid, oid, manager, docker, run, state, body = prepared
    prefix = "omnia-machine-test" if manager.namespace == "test" else "omnia-machine"
    volume = f"{prefix}-{wid.hex}-code-{uuid4().hex}"
    labels = {
        "omnia.managed": "true",
        "omnia.project_machine": "true",
        "omnia.workspace_id": str(wid),
        "omnia.project_id": str(state.project_id),
        "omnia.owner_id": str(state.owner_id),
        "omnia.resource_kind": "project-volume",
        "omnia.namespace": manager.namespace,
    }
    await docker.create_volume(volume, labels)
    write_controller_json(
        machine_identity_root(manager.state_store.root, wid) / "docker.json",
        {"active_code_volume": volume},
    )

    def forbidden(*args):
        raise AssertionError("receipt lookup initialized machine credentials")

    manager.machine_runtime = SimpleNamespace(exists=lambda _: True, parts=forbidden)
    journal = SourceWriteJournal(manager.state_store.root, wid)
    value = SourceWriteRecord(
        workspace_id=wid,
        operation_id=oid,
        project_id=state.project_id,
        owner_id=state.owner_id,
        generation_run_id=run,
        fencing_epoch=4,
        physical_volume=volume,
        physical_volume_identity=await docker.source_volume_identity(volume),
        request_sha256="a" * 64,
        before_revision=body["expected_revision"],
    )
    journal.start(value)
    journal.finish(value, after="b" * 64, written=1, deleted=0)
    async with _client() as client:
        url = f"/internal/workspaces/{wid}/agent/write-operations/{oid}"
        response = await client.get(url, headers=HEADERS, params={"generation_run_id": str(run)})
        assert response.status_code == 200
        assert response.json()["receipt"]["physical_volume"] == volume
        write_controller_json(
            machine_identity_root(manager.state_store.root, wid) / "docker.json",
            {"active_code_volume": "foreign-volume"},
        )
        assert (
            await client.get(url, headers=HEADERS, params={"generation_run_id": str(run)})
        ).status_code == 409


async def test_same_name_recreated_volume_rejects_historical_receipt(prepared):
    wid, oid, _manager, docker, run, state, body = prepared
    name = state.resource_names.workspace_volume
    async with _client() as client:
        write_url = f"/internal/workspaces/{wid}/agent/write-files"
        first = await client.post(write_url, headers=HEADERS, json=body)
        assert first.status_code == 200
        docker.volumes[name] = replace(docker.volumes[name], resource_id="new-physical-volume")
        response = await client.get(
            f"/internal/workspaces/{wid}/agent/write-operations/{oid}",
            headers=HEADERS,
            params={"generation_run_id": str(run)},
        )
        assert response.status_code == 409
        assert (await client.post(write_url, headers=HEADERS, json=body)).status_code == 409


async def test_legacy_read_write_need_no_birth_probe_but_tracked_requires_it(prepared, monkeypatch):
    from yleum_orchestrator.core.cell_resources import CellResourceError

    wid, _oid, _manager, docker, _run, _state, body = prepared
    probes = 0

    async def unavailable(_name):
        nonlocal probes
        probes += 1
        raise CellResourceError("birth time is unavailable")

    monkeypatch.setattr(docker, "source_volume_identity", unavailable)
    async with _client() as client:
        url = f"/internal/workspaces/{wid}/agent/write-files"
        legacy = await client.post(
            url, headers=HEADERS, json={k: v for k, v in body.items() if k != "operation_id"}
        )
        assert legacy.status_code == 200
        assert "receipt" not in legacy.json()
        read = await client.post(
            f"/internal/workspaces/{wid}/agent/bootstrap",
            headers=HEADERS,
            json={"generation_run_id": body["generation_run_id"], "fencing_epoch": 4},
        )
        assert read.status_code == 200
        assert probes == 0
        assert (await client.post(url, headers=HEADERS, json=body)).status_code == 409
        assert probes == 1


async def test_terminal_status_and_replay_require_current_birth_probe(prepared, monkeypatch):
    from yleum_orchestrator.core.cell_resources import CellResourceError

    wid, oid, _manager, docker, run, _state, body = prepared
    async with _client() as client:
        url = f"/internal/workspaces/{wid}/agent/write-files"
        assert (await client.post(url, headers=HEADERS, json=body)).status_code == 200

        async def unavailable(_name):
            raise CellResourceError("birth time is unavailable")

        monkeypatch.setattr(docker, "source_volume_identity", unavailable)
        assert (await client.post(url, headers=HEADERS, json=body)).status_code == 409
        assert (
            await client.get(
                f"/internal/workspaces/{wid}/agent/write-operations/{oid}",
                headers=HEADERS,
                params={"generation_run_id": str(run)},
            )
        ).status_code == 409
