"""Explicit adoption of the historical pre-material bootstrap journal only."""

import importlib
import importlib.util
import json
from contextlib import asynccontextmanager
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from omnia_orchestrator.core.cell_resources import CellResourceNames
from omnia_orchestrator.core.project_machine import MachineManifest
from omnia_orchestrator.services.cell_state import (
    CellOperationRecord,
    CellStateStore,
    CellWorkspaceState,
)
from omnia_orchestrator.services.fresh_database_protection import prepare_new_database
from omnia_orchestrator.services.project_machine import ProjectMachine, write_controller_json
from tests.test_project_machine_manifest import payload
from tests.test_restoration_machine_policy import backend


@pytest.fixture
def recovery(tmp_path):
    physical = backend(tmp_path / "project-machines")
    resources = []
    source = tmp_path / "volume"
    (source / ".omnia").mkdir(parents=True)
    (source / ".omnia/cell.json").write_text(
        MachineManifest.model_validate(payload()).model_dump_json(),
        encoding="utf-8",
    )
    source_volume = SimpleNamespace(
        attrs={
            "Mountpoint": str(source),
            "Labels": {
                "omnia.workspace_id": str(physical.workspace_id),
                "omnia.project_id": str(physical.project_id),
                "omnia.owner_id": str(physical.owner_id),
                "omnia.resource_kind": "workspace",
            },
        }
    )
    physical.client = SimpleNamespace(
        containers=SimpleNamespace(list=lambda **_: resources),
        volumes=SimpleNamespace(list=lambda **_: resources, get=lambda _: source_volume),
        networks=SimpleNamespace(list=lambda **_: resources),
    )
    physical._lookup = lambda *_: None
    physical._container = lambda: None
    physical._project_postgres = lambda: None
    prepare_new_database(physical, 1)
    root = physical.root / str(physical.workspace_id)
    secret = physical.root / "project-postgres-secrets" / f"{physical.workspace_id}.json"
    write_controller_json(secret, {"postgres_password": "synthetic-existing-secret"})
    run_id, operation_id = uuid4(), uuid4()
    operation = CellOperationRecord(
        operation_id=operation_id,
        kind="ensure",
        status="completed",
        phase="completed",
        request_digest="a" * 64,
        fencing_epoch=1,
        generation_run_id=run_id,
    )
    state = CellWorkspaceState(
        workspace_id=physical.workspace_id,
        project_id=physical.project_id,
        owner_id=physical.owner_id,
        profile_version=physical.resource_profile_version,
        phase="ready",
        bundle_state="resources_ready",
        fencing_epoch=2,
        active_generation_run_id=None,
        active_generation_fencing_epoch=None,
        last_operation_id=operation_id,
        provider_ref=None,
        resource_names=CellResourceNames.for_workspace(physical.workspace_id, namespace="test"),
        operations=(operation,),
    )
    physical.workspace_volume = state.resource_names.workspace_volume
    physical.internal_network = state.resource_names.internal_network
    store = CellStateStore(tmp_path / "states")
    store._persist_state(state)
    locked = []

    @asynccontextmanager
    async def hold(_):
        locked.append(True)
        try:
            yield
        finally:
            locked.pop()

    machine = ProjectMachine(
        physical.root, physical.workspace_id, physical, lease_epoch=lambda: None
    )
    adapter = SimpleNamespace(root=physical.root, parts=lambda _: (machine, physical))
    manager = SimpleNamespace(
        state_store=store, operation_lock=SimpleNamespace(hold=hold), machine_runtime=adapter
    )
    manager.docker = SimpleNamespace(
        helper_image="sha256:" + "f" * 64,
        read_workspace_source_files=AsyncMock(
            return_value={
                ".omnia/cell.json": (source / ".omnia/cell.json").read_bytes(),
            }
        ),
    )
    return SimpleNamespace(
        manager=manager,
        physical=physical,
        root=root,
        state=state,
        operation_id=operation_id,
        resources=resources,
        secret=secret,
    )


async def test_explicit_adoption_changes_only_journal_after_dry_run(recovery):
    module = importlib.import_module("omnia_orchestrator.services.initial_database_recovery")
    flow = recovery
    before = {p: p.read_bytes() for p in (flow.root / "data-policy.json", flow.secret)}
    original = (flow.root / "initial-database.json").read_bytes()
    kwargs = dict(
        manager=flow.manager,
        workspace_id=flow.state.workspace_id,
        expected_epoch=2,
        initial_operation_id=flow.operation_id,
    )
    receipt = await module.recover_initial_database(**kwargs)
    assert receipt["status"] == "eligible"
    assert (flow.root / "initial-database.json").read_bytes() == original
    receipt = await module.recover_initial_database(
        **kwargs,
        apply=True,
        expected_journal_digest=receipt["journal_digest"],
        expected_admission_digest=receipt["admission_digest"],
    )
    assert receipt["status"] == "adopted"
    journal = json.loads((flow.root / "initial-database.json").read_text())
    assert journal["epoch"] == 1 and journal["state"] == "pending"
    assert journal["generation_run_id"] == str(flow.state.operations[0].generation_run_id)
    assert "initial_runtime" in journal
    assert {p: p.read_bytes() for p in before} == before
    assert not (flow.root / "machine.json").exists()


@pytest.mark.parametrize(
    "damage",
    [
        "active",
        "epoch",
        "operation",
        "material",
        "secret",
        "ready",
        "policy",
        "digest",
    ],
)
async def test_recovery_rejects_ambiguous_state_without_writes(recovery, damage):
    module = importlib.import_module("omnia_orchestrator.services.initial_database_recovery")
    flow = recovery
    receipt = await module.recover_initial_database(
        manager=flow.manager,
        workspace_id=flow.state.workspace_id,
        expected_epoch=2,
        initial_operation_id=flow.operation_id,
    )
    if damage == "active":
        flow.manager.state_store._persist_state(
            replace(flow.state, active_generation_run_id=uuid4())
        )
    elif damage == "epoch":
        flow.manager.state_store._persist_state(replace(flow.state, fencing_epoch=3))
    elif damage == "operation":
        flow.operation_id = uuid4()
    elif damage == "material":
        flow.resources.append(object())
    elif damage == "secret":
        flow.secret.unlink()
    elif damage in {"ready", "policy"}:
        path = flow.root / ("initial-database.json" if damage == "ready" else "data-policy.json")
        value = json.loads(path.read_text())
        value["state" if damage == "ready" else "epoch"] = "ready" if damage == "ready" else 2
        write_controller_json(path, value)
    before = (flow.root / "initial-database.json").read_bytes()
    reason = {
        "active": "generation_active",
        "epoch": "canonical_epoch",
        "operation": "initial_operation",
        "material": "machine_material",
        "secret": "existing_secret_required",
        "ready": "initial_state",
        "policy": "initial_policy",
        "digest": "journal_changed",
    }[damage]
    with pytest.raises(module.InitialDatabaseRecoveryRejected, match=f"^{reason}$"):
        await module.recover_initial_database(
            manager=flow.manager,
            workspace_id=flow.state.workspace_id,
            expected_epoch=2,
            initial_operation_id=flow.operation_id,
            apply=True,
            expected_journal_digest="0" * 64 if damage == "digest" else receipt["journal_digest"],
            expected_admission_digest=receipt["admission_digest"],
        )
    assert (flow.root / "initial-database.json").read_bytes() == before


@pytest.mark.parametrize("damage", ["image", "manifest", "cleanup"])
async def test_source_helper_failure_never_adopts_journal(recovery, damage):
    module = importlib.import_module("omnia_orchestrator.services.initial_database_recovery")
    flow = recovery
    kwargs = dict(
        manager=flow.manager,
        workspace_id=flow.state.workspace_id,
        expected_epoch=2,
        initial_operation_id=flow.operation_id,
    )
    receipt = await module.recover_initial_database(**kwargs)
    original = (flow.root / "initial-database.json").read_bytes()
    if damage == "image":
        flow.manager.docker.helper_image = "mutable:latest"
    elif damage == "manifest":
        flow.manager.docker.read_workspace_source_files.return_value = {}
    else:
        files = flow.manager.docker.read_workspace_source_files.return_value

        async def unfinished_helper(_):
            flow.resources.append(object())
            return files

        flow.manager.docker.read_workspace_source_files.side_effect = unfinished_helper
    reason = {
        "image": "helper_image",
        "manifest": "source_manifest",
        "cleanup": "source_helper_cleanup",
    }[damage]
    with pytest.raises(module.InitialDatabaseRecoveryRejected, match=f"^{reason}$"):
        await module.recover_initial_database(
            **kwargs,
            apply=True,
            expected_journal_digest=receipt["journal_digest"],
            expected_admission_digest=receipt["admission_digest"],
        )
    assert (flow.root / "initial-database.json").read_bytes() == original


@pytest.mark.parametrize("changed_field", ["base_image", "cpu_cores"])
async def test_apply_rejects_admission_drift_after_reviewed_dry_run(recovery, changed_field):
    module = importlib.import_module("omnia_orchestrator.services.initial_database_recovery")
    flow = recovery
    kwargs = dict(
        manager=flow.manager,
        workspace_id=flow.state.workspace_id,
        expected_epoch=2,
        initial_operation_id=flow.operation_id,
    )
    receipt = await module.recover_initial_database(**kwargs)
    original = (flow.root / "initial-database.json").read_bytes()
    setattr(
        flow.physical, changed_field, "sha256:" + "e" * 64 if changed_field == "base_image" else 2.5
    )
    with pytest.raises(module.InitialDatabaseRecoveryRejected, match=r"^admission_changed$"):
        await module.recover_initial_database(
            **kwargs,
            apply=True,
            expected_journal_digest=receipt["journal_digest"],
            expected_admission_digest=receipt["admission_digest"],
        )
    assert (flow.root / "initial-database.json").read_bytes() == original


async def test_cli_selects_stored_workspace_profile_before_building_provider(monkeypatch):
    config = importlib.import_module("omnia_orchestrator.core.config")
    factory = importlib.import_module("omnia_orchestrator.services.workspace_provider_factory")
    recovery_module = importlib.import_module(
        "omnia_orchestrator.services.initial_database_recovery"
    )
    spec = importlib.util.spec_from_file_location(
        "initial_database_recovery_cli",
        Path(__file__).parents[1] / "scripts/recover-initial-database.py",
    )
    assert spec is not None and spec.loader is not None
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    workspace = uuid4()
    defaults, stored_profile, manager = object(), object(), object()
    calls = []

    def select(settings, workspace_id):
        assert settings is defaults and workspace_id == workspace
        calls.append("select")
        return stored_profile

    def build(settings):
        assert settings is stored_profile
        calls.append("build")
        return SimpleNamespace(resource_manager=manager)

    monkeypatch.setattr(config, "get_settings", lambda: defaults)
    monkeypatch.setattr(factory, "settings_for_workspace", select)
    monkeypatch.setattr(factory, "build_workspace_provider", build)
    recover = AsyncMock(return_value={"status": "eligible"})
    monkeypatch.setattr(recovery_module, "recover_initial_database", recover)
    args = SimpleNamespace(
        workspace=workspace,
        expected_epoch=2,
        initial_operation=uuid4(),
        apply=False,
        expected_journal_digest=None,
        expected_admission_digest=None,
    )
    assert await cli.run(args) == {"status": "eligible"}
    assert calls == ["select", "build"]
    recover.assert_awaited_once_with(
        manager=manager,
        workspace_id=workspace,
        expected_epoch=2,
        initial_operation_id=args.initial_operation,
        apply=False,
        expected_journal_digest=None,
        expected_admission_digest=None,
    )
