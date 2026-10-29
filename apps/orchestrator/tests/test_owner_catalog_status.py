"""Status is reconstructed from controller state and read-only runtime evidence."""

import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest

from tests.test_cell_publication import request
from tests.test_project_machine_manifest import payload
from yleum_orchestrator.core.cell_resources import CellIdentityConflict, WorkspaceLockTimeout
from yleum_orchestrator.schemas.workspace import WorkspaceOwnerBusinessConfigRequest
from yleum_orchestrator.services.app_business_config import config_digest
from yleum_orchestrator.services.cell_publication import CellPublicationService
from yleum_orchestrator.services.machine_adapter import MachineAdapter
from yleum_orchestrator.services.project_machine import write_controller_json


def public_runtime(tmp_path, monkeypatch, *, protocol=1):
    config = {"app_name": "Shop", "content": []}
    value = request(business_config=config, business_config_version=4)
    manifest = payload()
    if protocol is not None:
        manifest["app_config_protocol"] = protocol
    release = {"release_id": "release-one", "manifest": manifest,
               "placement": {"backend": "kubernetes"}}
    service = CellPublicationService(SimpleNamespace(), root=tmp_path)
    service._write(value.project_id, {
        "project_id": str(value.project_id), "owner_id": str(value.owner_id),
        "active_release": release, "history": [],
    })
    write_controller_json(
        tmp_path / str(value.project_id) / "requests/release-one.json",
        value.model_dump(mode="json"),
    )
    observe = Mock(return_value={"core_matches": True, "app_matches": True})
    monkeypatch.setattr(service, "_kubernetes", lambda: SimpleNamespace(observe_config=observe))
    return service, value, observe


@pytest.mark.parametrize("core,app", [(True, True), (False, True), (True, False)])
async def test_applied_requires_both_live_readbacks(tmp_path, monkeypatch, core, app):
    service, value, observe = public_runtime(tmp_path, monkeypatch)
    observe.return_value = {"core_matches": core, "app_matches": app}
    before = service._project_path(value.project_id).read_bytes()
    result = await service.business_config_status(
        value.project_id, value.owner_id, 4, config_digest(value.business_config),
    )
    assert result["state"] == ("applied" if core and app else "pending")
    assert result["confirmed_config_version"] == (4 if core and app else None)
    assert service._project_path(value.project_id).read_bytes() == before
    assert "config" not in result and "owner_id" not in result


async def test_old_source_remains_blocked_without_runtime_effect(tmp_path, monkeypatch):
    service, value, observe = public_runtime(tmp_path, monkeypatch, protocol=None)
    result = await service.business_config_status(
        value.project_id, value.owner_id, 4, config_digest(value.business_config),
    )
    assert result["state"] == "blocked" and result["reason"] == "catalog_integration_required"
    observe.assert_not_called()


async def test_default_zero_config_survives_auth_only_overlay_and_first_configure(tmp_path):
    service = CellPublicationService(SimpleNamespace(), root=tmp_path)
    value = request(business_config_version=0, business_config={"content": []})
    service._write(value.project_id, {
        "project_id": str(value.project_id), "owner_id": str(value.owner_id),
        "active_release": None, "history": [{}],
    })
    await service.configure(value.project_id, value.owner_id, runtime_env={})
    assert service._effective_request(value).business_config_version == 0
    await service.configure(
        value.project_id, value.owner_id,
        business_config={"content": []}, business_config_version=0,
    )
    assert service._effective_request(value).business_config == {"content": []}


async def test_stale_controller_and_changed_release_cannot_confirm(tmp_path, monkeypatch):
    service, value, observe = public_runtime(tmp_path, monkeypatch)
    result = await service.business_config_status(
        value.project_id, value.owner_id, 5, config_digest(value.business_config),
    )
    assert result["state"] == "pending"
    observe.assert_not_called()
    def changed(*_):
        saved = service._read(value.project_id)
        saved["active_release"]["release_id"] = "replacement"
        service._write(value.project_id, saved)
        return {"core_matches": True, "app_matches": True}
    observe.side_effect = changed
    result = await service.business_config_status(
        value.project_id, value.owner_id, 4, config_digest(value.business_config),
    )
    assert result["state"] == "unknown" and result["reason"] == "busy"


async def test_foreign_owner_and_busy_read_are_not_applied(tmp_path, monkeypatch):
    service, value, observe = public_runtime(tmp_path, monkeypatch)
    with pytest.raises(CellIdentityConflict):
        await service.business_config_status(value.project_id, uuid4(), 4, "0" * 64)
    @asynccontextmanager
    async def busy(_):
        raise WorkspaceLockTimeout("workspace_lock_timeout")
        yield
    monkeypatch.setattr(service._submission_lock, "hold", busy)
    result = await service.business_config_status(value.project_id, value.owner_id, 4, "0" * 64)
    assert result["state"] == "unknown" and result["reason"] == "busy"
    observe.assert_not_called()


@pytest.mark.parametrize("running", [False, True])
def test_preview_status_never_wakes_sleeping_machine(tmp_path, monkeypatch, running):
    state = SimpleNamespace(workspace_id=uuid4(), project_id=uuid4(), owner_id=uuid4())
    adapter = MachineAdapter(
        SimpleNamespace(state_store=SimpleNamespace(root=tmp_path / "controller-state")), object(),
    )
    manifest = {**payload(), "app_config_protocol": 1}
    machine = SimpleNamespace(path=tmp_path / "machine.json", state=lambda: {"manifest": manifest})
    config = {"content": []}
    (tmp_path / "business-config.json").write_text(json.dumps({
        "project_id": str(state.project_id), "owner_id": str(state.owner_id),
        "version": 4, "config": config,
    }))
    app = SimpleNamespace(status="running" if running else "exited", reload=lambda: None)
    backend = SimpleNamespace(_container=lambda: app)
    monkeypatch.setattr(adapter, "exists", lambda _: True)
    monkeypatch.setattr(adapter, "parts", lambda _state, **_: (machine, backend))
    observe = Mock(return_value={"core_matches": True, "app_matches": True})
    monkeypatch.setattr(
        "yleum_orchestrator.services.app_business_config.observe_docker_config", observe,
    )
    result = adapter.owner_business_config_status(state, 4, config_digest(config))
    assert result["state"] == ("applied" if running else "deferred")
    assert observe.call_count == int(running)


def test_generation_seed_is_durable_before_a_manifest_and_never_wakes(tmp_path, monkeypatch):
    state = SimpleNamespace(workspace_id=uuid4(), project_id=uuid4(), owner_id=uuid4())
    adapter = MachineAdapter(
        SimpleNamespace(state_store=SimpleNamespace(root=tmp_path / "controller-state")), object(),
    )
    monkeypatch.setattr(
        adapter, "parts", Mock(side_effect=AssertionError("must not create runtime")),
    )
    adapter.seed_owner_business_config(state, version=0, config={"app_name": "Shop", "content": []})
    path = adapter.root / str(state.workspace_id) / "business-config.json"
    assert json.loads(path.read_text())["version"] == 0
    adapter.seed_owner_business_config(state, version=2, config={"app_name": "New"})
    with pytest.raises(RuntimeError, match="stale"):
        adapter.seed_owner_business_config(state, version=1, config={"app_name": "Old"})


def test_generation_seed_dto_requires_paired_authority_and_preserves_owner_guard():
    from pydantic import ValidationError
    base = {"project_id": uuid4(), "owner_id": uuid4(), "version": 0, "config": {"content": []}}
    with pytest.raises(ValidationError):
        WorkspaceOwnerBusinessConfigRequest(**base)
    with pytest.raises(ValidationError):
        WorkspaceOwnerBusinessConfigRequest(**base, generation_run_id=uuid4())
    valid = WorkspaceOwnerBusinessConfigRequest(**base, generation_run_id=uuid4(), fencing_epoch=4)
    assert valid.version == 0
