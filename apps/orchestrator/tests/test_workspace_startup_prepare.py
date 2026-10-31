import json
from uuid import uuid4

import pytest

from tests.test_machine_workspace_dispatch import PortableRuntime
from tests.test_project_machine_manifest import payload
from tests.test_workspace_router import _client, _ready_provider
from tests.test_workspace_router import _internal_settings as _internal_settings
from yleum_orchestrator.core.project_machine import MachineManifest
from yleum_orchestrator.routers import workspace
from yleum_orchestrator.services.machine_startup import MachineInventoryUnavailable


class Runtime(PortableRuntime):
    def __init__(self):
        super().__init__()
        self.prepared = []

    async def prepare_environment(self, state, manifest, binding, verify_current, *, files):
        await verify_current()
        assert manifest.digest() == binding.manifest_digest
        self.prepared.append(binding)
        return "f" * 64, "e" * 64

    async def protect_migration_sources(self, state, files, *, resume=True):
        return {}


async def setup(tmp_path, monkeypatch):
    wid = uuid4()
    provider, manager, docker, run = await _ready_provider(tmp_path, wid)
    runtime = Runtime()
    runtime.manager = manager
    manager.machine_runtime = runtime
    state = manager.state_store.load(wid)
    files = {".omnia/cell.json": json.dumps(payload()), "server.py": "print('owned')"}
    await docker.write_volume_files(
        state.resource_names.workspace_volume, {p: value.encode() for p, value in files.items()}
    )
    monkeypatch.setattr(workspace, "build_workspace_provider", lambda _: provider)
    request = {
        "project_id": str(state.project_id),
        "owner_id": str(state.owner_id),
        "generation_run_id": str(run),
        "fencing_epoch": 4,
        "expected_revision": workspace._workspace_revision(files),
        "manifest_digest": MachineManifest.model_validate(payload()).digest(),
    }
    return wid, runtime, request


@pytest.mark.usefixtures("_internal_settings")
async def test_prepare_has_explicit_owned_current_source_boundary(tmp_path, monkeypatch):
    wid, runtime, request = await setup(tmp_path, monkeypatch)
    async with _client() as client:
        response = await client.post(
            f"/internal/workspaces/{wid}/agent/environment/prepare",
            json=request,
            headers={"X-Internal-Token": "test-internal-token-not-a-real-secret"},
        )
    assert response.status_code == 200
    assert len(runtime.prepared) == 1
    assert response.json()["identity"]["environment_digest"] == "e" * 64


@pytest.mark.usefixtures("_internal_settings")
@pytest.mark.parametrize(
    "field",
    [
        "project_id",
        "owner_id",
        "generation_run_id",
        "fencing_epoch",
        "expected_revision",
        "manifest_digest",
    ],
)
async def test_prepare_foreign_or_stale_context_has_zero_effect(tmp_path, monkeypatch, field):
    wid, runtime, request = await setup(tmp_path, monkeypatch)
    request[field] = (
        str(uuid4()) if field.endswith("_id") else 3 if field == "fencing_epoch" else "a" * 64
    )
    async with _client() as client:
        response = await client.post(
            f"/internal/workspaces/{wid}/agent/environment/prepare",
            json=request,
            headers={"X-Internal-Token": "test-internal-token-not-a-real-secret"},
        )
    assert response.status_code == 409
    assert runtime.prepared == []


@pytest.mark.usefixtures("_internal_settings")
async def test_stopped_identity_is_typed_refusal_and_never_prepares(tmp_path, monkeypatch):
    wid, runtime, request = await setup(tmp_path, monkeypatch)

    def stopped():
        raise MachineInventoryUnavailable("stopped")

    runtime.backend.environment_digest = stopped
    async with _client() as client:
        response = await client.get(
            f"/internal/workspaces/{wid}/agent/identity",
            params={key: request[key] for key in ("generation_run_id", "fencing_epoch")},
            headers={"X-Internal-Token": "test-internal-token-not-a-real-secret"},
        )
    assert response.status_code == 409
    error = response.json()["error"]
    assert error["code"] == "container_not_running"
    assert error["details"] == {
        "reason": "stopped",
        "workspace_revision": request["expected_revision"],
        "manifest_digest": request["manifest_digest"],
    }
    assert runtime.prepared == []


@pytest.mark.usefixtures("_internal_settings")
@pytest.mark.parametrize("changed_source", [False, True])
async def test_running_identity_requires_exact_pending_prepare_reconciliation(
    tmp_path,
    monkeypatch,
    changed_source,
):
    from yleum_orchestrator.services.machine_identity import machine_identity_root
    from yleum_orchestrator.services.machine_startup import PrepareBinding, StartupJournal

    wid, runtime, request = await setup(tmp_path, monkeypatch)
    manager = runtime.manager
    state = manager.state_store.load(wid)
    first = state.operations[0]
    binding = PrepareBinding(
        workspace_id=wid,
        project_id=state.project_id,
        owner_id=state.owner_id,
        generation_run_id=state.active_generation_run_id,
        fencing_epoch=4,
        admission_operation_id=first.operation_id,
        admission_request_sha256=first.request_digest,
        physical_volume=state.resource_names.workspace_volume,
        physical_volume_identity=await manager.docker.source_volume_identity(
            state.resource_names.workspace_volume,
        ),
        workspace_revision="a" * 64 if changed_source else request["expected_revision"],
        manifest_digest=request["manifest_digest"],
    )
    journal = StartupJournal(machine_identity_root(manager.state_store.root, wid).parent, wid)
    journal.begin(binding)
    journal.dispatch(binding)
    async with _client() as client:
        response = await client.get(
            f"/internal/workspaces/{wid}/agent/identity",
            params={key: request[key] for key in ("generation_run_id", "fencing_epoch")},
            headers={"X-Internal-Token": "test-internal-token-not-a-real-secret"},
        )
    assert response.status_code == 409
    assert response.json()["error"]["details"]["reason"] == (
        "unavailable" if changed_source else "prepare_pending"
    )
    assert runtime.prepared == []


@pytest.mark.usefixtures("_internal_settings")
async def test_real_adapter_stopped_prepare_fresh_identity_then_normal_roles(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from tests.test_docker_machine_backend import _InventoryContainer
    from tests.test_docker_machine_backend import backend as make_backend
    from yleum_orchestrator.services import applied_migration_sources
    from yleum_orchestrator.services.machine_adapter import MachineAdapter
    from yleum_orchestrator.services.project_machine import ProjectMachine

    wid, old_runtime, request = await setup(tmp_path, monkeypatch)
    manager = old_runtime.manager
    state = manager.state_store.load(wid)
    admission = state.operations[0]
    manifest = payload()
    manifest["tasks"].append(
        {"name": "check", "role": "fast_check", "argv": ["python", "-m", "unittest"]}
    )
    files = {".omnia/cell.json": json.dumps(manifest), "server.py": "print('owned')"}
    await manager.docker.write_volume_files(
        state.resource_names.workspace_volume, {p: value.encode() for p, value in files.items()}
    )
    request["expected_revision"] = workspace._workspace_revision(files)
    request["manifest_digest"] = MachineManifest.model_validate(manifest).digest()
    runtime = MachineAdapter(manager, SimpleNamespace())
    manager.machine_runtime = runtime
    product = _InventoryContainer()
    product.attrs["State"].update(Status="exited", Running=False)
    native = make_backend(
        runtime.root,
        workspace_id=wid,
        project_id=state.project_id,
        owner_id=state.owner_id,
        workspace_volume=state.resource_names.workspace_volume,
    )
    native._container = lambda: product
    native._project_postgres = lambda: SimpleNamespace(status="running", reload=lambda: None)
    starts = []

    def ensure(actual_manifest, epoch):
        assert actual_manifest.digest() == request["manifest_digest"] and epoch == 4
        assert "next_cache_key" in native._metadata(), (
            "prepare must use the canonical current cache identity"
        )
        if product.attrs["State"]["Status"] != "running":
            starts.append(True)
            product.attrs["State"].update(
                Status="running",
                Running=True,
                StartedAt="2026-10-31T09:00:00.123456789Z",
            )

    native.ensure = ensure
    native.exec_start = lambda _argv, _cwd, operation_id: "local-exec-" + operation_id
    native.exec_status = lambda _exec: {
        "running": False,
        "exit_code": 0,
        "output": "synthetic task green",
    }
    machine = ProjectMachine(
        runtime.root,
        wid,
        native,
        lease_epoch=lambda: manager.state_store.load(wid).active_generation_fencing_epoch,
    )
    runtime.parts = lambda _state: (machine, native)
    monkeypatch.setattr(applied_migration_sources, "protected_sources", lambda *_a, **_kw: {})

    async def no_migration_gap(_state):
        return None

    runtime._migration_dependency_gap = no_migration_gap
    headers = {"X-Internal-Token": "test-internal-token-not-a-real-secret"}
    params = {key: request[key] for key in ("generation_run_id", "fencing_epoch")}
    async with _client() as client:
        stopped = await client.get(
            f"/internal/workspaces/{wid}/agent/identity", params=params, headers=headers
        )
        assert stopped.status_code == 409 and starts == []
        prepared = await client.post(
            f"/internal/workspaces/{wid}/agent/environment/prepare", json=request, headers=headers
        )
        assert prepared.status_code == 200, prepared.text
        fresh = await client.get(
            f"/internal/workspaces/{wid}/agent/identity", params=params, headers=headers
        )
        assert fresh.status_code == 200
        assert (
            fresh.json()["environment_digest"] == prepared.json()["identity"]["environment_digest"]
        )
        for role in ("bootstrap", "fast_check"):
            response = await client.post(
                f"/internal/workspaces/{wid}/agent/exec",
                headers=headers,
                json={
                    **params,
                    "operation_id": str(uuid4()),
                    "cmd": "omnia:" + role,
                    "task_role": role,
                    "expected_revision": request["expected_revision"],
                },
            )
            assert response.status_code == 200 and response.json()["ok"] is True, response.text
        # A real normal edit changes CURRENT source, not the original admission.
        written = await client.post(
            f"/internal/workspaces/{wid}/agent/write-files",
            headers=headers,
            json={
                **params,
                "operation_id": str(uuid4()),
                "expected_revision": request["expected_revision"],
                "files": {"server.py": "print('updated')"},
            },
        )
        assert written.status_code == 200, written.text
        revision_b = written.json()["workspace_revision"]
        assert revision_b != request["expected_revision"]
        checked_b = await client.post(
            f"/internal/workspaces/{wid}/agent/exec",
            headers=headers,
            json={
                **params,
                "operation_id": str(uuid4()),
                "cmd": "omnia:fast_check",
                "task_role": "fast_check",
                "expected_revision": revision_b,
            },
        )
        assert checked_b.status_code == 200 and checked_b.json()["ok"] is True, checked_b.text
        assert manager.state_store.load(wid).operations[0] == admission
    assert starts == [True]
    files["server.py"] = "print('updated')"
    assert (
        await workspace._read_agent_workspace_files(manager, state.resource_names.workspace_volume)
        == files
    )
