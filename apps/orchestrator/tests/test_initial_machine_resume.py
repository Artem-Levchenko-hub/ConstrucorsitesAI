"""Initial protected admission must compose with the actual machine command path."""

import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from omnia_orchestrator.core.cell_resources import (
    CellFenceRejected,
    CellProtectedEnvironmentRecoveryRequired,
    CellResourceError,
)
from omnia_orchestrator.core.errors import OrchestratorError
from omnia_orchestrator.core.project_machine import MachineManifest
from omnia_orchestrator.routers import workspace
from omnia_orchestrator.schemas.workspace import (
    WorkspaceAgentBootstrapRequest,
    WorkspaceAgentExecRequest,
    WorkspaceAgentWriteRequest,
)
from omnia_orchestrator.services import fresh_database_protection as protection
from omnia_orchestrator.services.machine_adapter import MachineAdapter
from omnia_orchestrator.services.project_machine import ProjectMachine, write_controller_json
from omnia_orchestrator.services.restoration_database import load_policy
from tests.test_project_machine_manifest import payload
from tests.test_restoration_machine_policy import backend


@pytest.fixture
def initial_machine(tmp_path, monkeypatch):
    physical = backend(tmp_path)
    physical.client = SimpleNamespace(volumes=object())
    physical._lookup = lambda *_: None
    physical._container = lambda: None
    physical._project_postgres = lambda: None
    lease = {"epoch": 7}
    machine = ProjectMachine(
        tmp_path, physical.workspace_id, physical, lease_epoch=lambda: lease["epoch"]
    )
    manifest = MachineManifest.model_validate(payload())
    run_id = uuid4()
    state = SimpleNamespace(
        workspace_id=physical.workspace_id,
        project_id=physical.project_id,
        owner_id=physical.owner_id,
    )
    locked = []

    @asynccontextmanager
    async def hold(_):
        locked.append(True)
        try:
            yield
        finally:
            locked.pop()

    manager = SimpleNamespace(operation_lock=SimpleNamespace(hold=hold))
    adapter = MachineAdapter(manager, SimpleNamespace())
    adapter.parts = lambda _: (machine, physical)
    adapter.exists = lambda _: machine.path.exists()
    manager.machine_runtime = adapter
    monkeypatch.setattr(workspace, "verify_internal_token", lambda _: None)
    monkeypatch.setattr(workspace, "_workspace_provider", lambda _: None)
    monkeypatch.setattr(workspace, "_require_docker_resource_manager", lambda _: manager)
    monkeypatch.setattr(
        workspace, "_workspace_volume_identity", AsyncMock(return_value=(state, "source"))
    )
    monkeypatch.setattr(
        workspace, "_require_active_generation_lease", lambda _: (run_id, lease["epoch"])
    )
    monkeypatch.setattr(
        workspace,
        "_ensure_seed_workspace_files",
        AsyncMock(
            return_value=(
                {".omnia/cell.json": manifest.model_dump_json()},
                False,
            )
        ),
    )
    effects = []

    def ensure(_manifest, epoch):
        # Docker/SQL boundary only. Controller admission/ensure/request storage are real.
        effects.append("ensure")
        assert physical.project_database_env()["PGUSER"] == "omnia_runtime"
        protection.finish_new_database(physical, epoch)

    monkeypatch.setattr(protection, "install_policy", lambda _: effects.append("install-policy"))
    physical.ensure = ensure
    physical.exec_start = lambda *_: effects.append("command") or "docker-exec"
    physical.exec_status = lambda _: {"running": False, "exit_code": 0, "output": "ok"}
    return SimpleNamespace(
        physical=physical,
        machine=machine,
        manifest=manifest,
        run_id=run_id,
        state=state,
        adapter=adapter,
        lease=lease,
        effects=effects,
    )


async def admit(flow):
    return await workspace.bootstrap_workspace_agent(
        flow.physical.workspace_id,
        WorkspaceAgentBootstrapRequest(
            generation_run_id=flow.run_id, fencing_epoch=flow.lease["epoch"]
        ),
    )


async def command(flow, **kwargs):
    return await flow.adapter.execute(
        flow.state,
        flow.manifest,
        WorkspaceAgentExecRequest(
            generation_run_id=flow.run_id,
            fencing_epoch=flow.lease["epoch"],
            expected_revision="a" * 64,
            cmd="omnia:bootstrap",
            task_role="bootstrap",
            **kwargs,
        ),
    )


async def test_normal_bootstrap_then_first_command_retains_policy(initial_machine):
    flow = initial_machine
    response = await admit(flow)
    policy = load_policy(flow.physical)
    assert response.capabilities["database_admin"] == "protected"
    assert not flow.machine.path.exists()
    result = await command(flow)
    assert result.exit_code == 0
    assert flow.effects == ["ensure", "install-policy", "command"]
    assert load_policy(flow.physical) == policy
    saved = json.loads(flow.machine.path.read_text())
    assert saved["ready_epoch"] == 7
    assert saved["manifest"] == flow.manifest.model_dump(mode="json")


@pytest.mark.parametrize("new_epoch", [7, 8])
async def test_interrupted_ensure_before_material_resumes_without_rotating_policy(
    initial_machine,
    new_epoch,
):
    flow = initial_machine
    await admit(flow)
    policy = load_policy(flow.physical)
    ensure = flow.physical.ensure

    def interrupted(*_):
        raise CellResourceError("synthetic Docker failure before material creation")

    flow.physical.ensure = interrupted
    with pytest.raises(CellResourceError, match="synthetic Docker failure"):
        await command(flow)
    assert flow.machine.state()["ready_epoch"] is None
    flow.physical.ensure = ensure
    flow.lease["epoch"] = new_epoch
    result = await command(flow)
    assert result.exit_code == 0
    assert load_policy(flow.physical) == policy
    assert flow.machine.state()["ready_epoch"] == new_epoch


@pytest.mark.parametrize(
    "damage",
    [
        "unbound",
        "ready",
        "mount",
        "image",
        "quota",
        "volume",
        "guest",
        "metadata",
        "lost_volume",
    ],
)
async def test_ambiguous_or_retained_state_never_reaches_docker(initial_machine, damage):
    flow = initial_machine
    await admit(flow)
    journal_path = flow.machine.path.parent / "initial-database.json"
    journal = json.loads(journal_path.read_text())
    if damage == "unbound":
        journal.pop("initial_runtime")
    elif damage == "ready":
        journal["state"] = "ready"
    elif damage == "mount":
        value = flow.manifest.model_dump()
        value["services"][0]["mounts"] = [{"volume": "new-data", "target": "/new-data"}]
        flow.manifest = MachineManifest.model_validate(value)
    elif damage == "image":
        flow.physical.base_image = "sha256:" + "d" * 64
    elif damage == "quota":
        flow.physical.memory_bytes *= 2
    elif damage == "volume":
        flow.physical._lookup = lambda *_: object()
    elif damage == "guest":
        flow.physical._container = lambda: object()
    elif damage == "metadata":
        write_controller_json(flow.physical.metadata_path, {"epoch": 7})
    elif damage == "lost_volume":
        journal["volume_intent"] = True
    write_controller_json(journal_path, journal)
    policy = load_policy(flow.physical)
    with pytest.raises(CellProtectedEnvironmentRecoveryRequired):
        await command(flow)
    assert flow.effects == []
    assert load_policy(flow.physical) == policy
    assert json.loads(journal_path.read_text()) == journal


@pytest.mark.parametrize("method", ["validate_restore_payload", "restore_payload"])
async def test_pending_restore_to_legacy_seed_preserves_all_controller_evidence(
    initial_machine,
    method,
):
    flow = initial_machine
    await admit(flow)
    write_controller_json(flow.machine.path, {"epoch": 7, "ready_epoch": None})
    marker = flow.machine.path.parent / "portable.json"
    write_controller_json(marker, {"workspace_id": str(flow.physical.workspace_id)})
    paths = [
        flow.machine.path,
        marker,
        flow.machine.path.parent / "initial-database.json",
        flow.machine.path.parent / "data-policy.json",
    ]
    before = [p.read_bytes() for p in paths]
    flow.adapter.halt = AsyncMock()
    with pytest.raises(CellProtectedEnvironmentRecoveryRequired):
        await getattr(flow.adapter, method)(flow.state, None)
    assert [p.read_bytes() for p in paths] == before
    assert flow.effects == []
    flow.adapter.halt.assert_not_awaited()


async def test_exec_route_returns_structured_recovery_code(initial_machine, monkeypatch):
    flow = initial_machine
    await admit(flow)
    path = flow.machine.path.parent / "initial-database.json"
    journal = json.loads(path.read_text())
    journal.pop("initial_runtime")
    write_controller_json(path, journal)
    files = {".omnia/cell.json": flow.manifest.model_dump_json()}
    monkeypatch.setattr(workspace, "_read_agent_workspace_files", AsyncMock(return_value=files))
    monkeypatch.setattr(workspace, "_prepare_portable_write", AsyncMock(return_value=flow.manifest))
    request = WorkspaceAgentExecRequest(
        generation_run_id=flow.run_id,
        fencing_epoch=7,
        expected_revision=workspace._workspace_revision(files),
        cmd="omnia:bootstrap",
        task_role="bootstrap",
    )
    with pytest.raises(OrchestratorError) as caught:
        await workspace.exec_workspace_agent_command(flow.physical.workspace_id, request)
    assert caught.value.status_code == 409
    assert caught.value.code == "protected_environment_recovery_required"
    assert flow.effects == []


@pytest.mark.parametrize("fence", ["stale_request", "cancelled", "older_lease"])
async def test_initial_resume_cannot_bypass_canonical_fence(initial_machine, fence):
    flow = initial_machine
    await admit(flow)
    if fence == "stale_request":
        flow.lease["epoch"] = 8
    elif fence == "cancelled":
        write_controller_json(
            flow.machine.path,
            {
                **flow.machine.state(),
                "epoch": 7,
                "cancelled_epoch": 7,
            },
        )
    else:
        flow.lease["epoch"] = 6
    expected = (
        "machine lease expired, cancelled, or changed"
        if fence != "older_lease"
        else "protected retained manifest is missing"
    )
    with pytest.raises(
        (CellFenceRejected, CellProtectedEnvironmentRecoveryRequired), match=expected
    ):
        await flow.adapter.execute(
            flow.state,
            flow.manifest,
            WorkspaceAgentExecRequest(
                generation_run_id=flow.run_id,
                fencing_epoch=7,
                expected_revision="a" * 64,
                cmd="omnia:bootstrap",
                task_role="bootstrap",
            ),
        )
    assert flow.effects == []


async def test_partial_postgres_volume_reuses_identity_without_recreation(initial_machine):
    flow = initial_machine
    await admit(flow)
    resource = {"volume": None}
    calls = []
    flow.physical._lookup = lambda *_: resource["volume"]

    def create_volume(_):
        if resource["volume"] is None:
            calls.append("volume-created")
            resource["volume"] = object()

    def permissions():
        calls.append("permissions")
        if calls.count("permissions") == 1:
            raise CellResourceError("synthetic helper interruption")

    flow.physical._volume = create_volume
    flow.physical._ensure_project_postgres_permissions = permissions
    flow.physical._initialize_project_postgres_volume = lambda: calls.append("initialize-helper")
    with pytest.raises(CellResourceError, match="synthetic helper interruption"):
        flow.physical._prepare_project_postgres_volume()
    original_volume = resource["volume"]
    policy = load_policy(flow.physical)
    flow.physical._prepare_project_postgres_volume()
    assert resource["volume"] is original_volume
    assert calls == ["volume-created", "permissions", "permissions", "initialize-helper"]
    assert protection.validate_initial_runtime_resume(flow.physical, flow.manifest, 8)
    assert load_policy(flow.physical) == policy


async def test_volume_creation_uncertainty_does_not_reinitialize_database(initial_machine):
    flow = initial_machine
    await admit(flow)
    protection.record_initial_volume_intent(flow.physical)
    with pytest.raises(CellProtectedEnvironmentRecoveryRequired, match="material is missing"):
        flow.physical._prepare_project_postgres_volume()
    assert flow.effects == []


async def test_newer_physical_postgres_is_not_rebound(initial_machine):
    flow = initial_machine
    await admit(flow)
    protection.record_initial_volume_intent(flow.physical)
    flow.physical._lookup = lambda *_: object()
    flow.physical._project_postgres = lambda: SimpleNamespace(
        labels={"omnia.fencing_epoch": "9"},
    )
    with pytest.raises(CellProtectedEnvironmentRecoveryRequired):
        await command(flow)
    assert flow.effects == []


@pytest.mark.parametrize("after_cancel", [False, True])
async def test_bootstrap_write_product_manifest_then_real_exec_route(
    initial_machine,
    monkeypatch,
    after_cancel,
):
    from omnia_orchestrator.services import protected_machine_lifecycle

    flow = initial_machine
    await admit(flow)
    policy = load_policy(flow.physical)
    if after_cancel:
        write_controller_json(
            flow.machine.path,
            {
                **flow.machine.state(),
                "epoch": 7,
                "cancelled_epoch": 7,
            },
        )
        flow.run_id = uuid4()
        flow.lease["epoch"] = 8
        monkeypatch.setattr(
            workspace, "_require_active_generation_lease", lambda _: (flow.run_id, 8)
        )
        await admit(flow)
    epoch = flow.lease["epoch"]
    files = {".omnia/cell.json": flow.manifest.model_dump_json()}
    manager = flow.adapter.manager
    manager.state_store = SimpleNamespace(root=flow.physical.root / "states")
    manager.inspect_draft_runtime = AsyncMock(return_value=None)

    async def read_files(*_):
        return dict(files)

    async def write_files(_, writes):
        files.update({path: value.decode() for path, value in writes.items()})

    manager.docker = SimpleNamespace(write_volume_files=write_files)
    monkeypatch.setattr(workspace, "_read_agent_workspace_files", read_files)
    monkeypatch.setattr(protected_machine_lifecycle, "validate_retained_runtime", lambda *_: None)
    flow.physical.environment_digest = lambda: "e" * 64
    value = flow.manifest.model_dump()
    value["tasks"][0]["argv"] = ["sh", "product-install.sh"]
    value["services"][0]["argv"] = ["python", "product.py"]
    value["routes"].append({**value["routes"][0], "path": "/product"})
    manifest = MachineManifest.model_validate(value)
    written = await workspace.write_workspace_agent_files(
        flow.physical.workspace_id,
        WorkspaceAgentWriteRequest(
            generation_run_id=flow.run_id,
            fencing_epoch=epoch,
            expected_revision=workspace._workspace_revision(files),
            files={".omnia/cell.json": manifest.model_dump_json()},
        ),
    )
    assert written.written == 1
    cache_before = flow.physical.next_cache_volume
    result = await workspace.exec_workspace_agent_command(
        flow.physical.workspace_id,
        WorkspaceAgentExecRequest(
            generation_run_id=flow.run_id,
            fencing_epoch=epoch,
            expected_revision=written.workspace_revision,
            cmd="omnia:bootstrap",
            task_role="bootstrap",
        ),
    )
    assert result.ok and result.exit_code == 0
    assert flow.physical.next_cache_volume != cache_before
    assert flow.effects == ["ensure", "install-policy", "ensure", "command"]
    assert load_policy(flow.physical) == policy
    assert flow.machine.state()["manifest"] == manifest.model_dump(mode="json")
    assert flow.machine.state()["ready_epoch"] == epoch
