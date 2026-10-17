"""Restored-volume compatibility requires protected controller authority, never labels alone."""

from dataclasses import replace
from types import SimpleNamespace
from uuid import uuid4

import pytest

from tests._generation_reader_settings import legacy_reader_process_settings  # noqa: F401
from tests.test_docker_py_cell_backend import _backend, _FakeClient, _FakeVolume
from tests.test_workspace_router import _ready_provider
from yleum_orchestrator.core.cell_resources import CellIdentityConflict, CellResourceError
from yleum_orchestrator.services.docker_machine_backend import DockerMachineBackend
from yleum_orchestrator.services.machine_identity import machine_identity_root
from yleum_orchestrator.services.project_machine import write_controller_json


@pytest.fixture
async def active_volume(tmp_path, monkeypatch):
    wid = uuid4()
    _, manager, _, _ = await _ready_provider(tmp_path, wid)
    state = manager.state_store.load(wid)
    identity = SimpleNamespace(
        workspace_id=wid,
        project_id=state.project_id,
        owner_id=state.owner_id,
        namespace=manager.namespace,
    )
    name = f"omnia-machine-test-{wid.hex}-code-{'a' * 32}"
    labels = DockerMachineBackend.labels(identity, "project-volume")
    path = machine_identity_root(manager.state_store.root, wid) / "docker.json"
    write_controller_json(path, {"active_code_volume": name})
    client = _FakeClient()
    client.volumes.items[name] = _FakeVolume(name, labels, files={"old": b"old"})
    backend = _backend(client)
    manager.docker = backend

    async def incarnation(self, selected):
        assert selected == name
        return "a" * 64

    monkeypatch.setattr(type(backend), "source_volume_identity", incarnation)
    monkeypatch.setattr(type(backend), "_mounted_volume_identity", lambda *a: "a" * 64)
    return manager, backend, client, name, state, labels, path


@pytest.mark.parametrize(
    "action", ["read_volume_files", "write_volume_files", "delete_volume_paths"]
)
async def test_exact_active_source_context_supports_original_volume_labels(
    active_volume, monkeypatch, action
):
    from yleum_orchestrator.services.active_source_volume import source_volume_call
    manager, backend, client, name, state, labels, _path = active_volume
    args = {
        "read_volume_files": (),
        "write_volume_files": ({"new": b"new"},),
        "delete_volume_paths": (("old",),),
    }[action]
    with pytest.raises(CellResourceError):
        await getattr(backend, action)(name, *args)
    result = await source_volume_call(manager, name, action, *args)
    if action == "read_volume_files":
        assert result == {"old": b"old"}
    elif action == "write_volume_files":
        assert client.volumes.items[name].files["new"] == b"new"
    else:
        assert client.volumes.items[name].files == {}
    assert client.volumes.items[name].attrs["Labels"] == labels
    # A controller owner mismatch cannot be repaired by matching source bytes/labels.
    monkeypatch.setattr(
        type(manager.state_store), "load", lambda self, wid: replace(state, owner_id=uuid4())
    )
    with pytest.raises(CellIdentityConflict):
        await source_volume_call(manager, name, action, *args)


@pytest.mark.parametrize("change", ["namespace", "selection", "name", "incarnation"])
async def test_active_source_authority_rejects_changed_binding_before_write(
    active_volume, monkeypatch, change
):
    from yleum_orchestrator.services.active_source_volume import source_volume_call
    manager, backend, client, name, _state, _labels, path = active_volume
    selected = name
    if change == "namespace":
        manager.namespace = "prod"
    elif change == "selection":
        write_controller_json(path, {"active_code_volume": name[:-1] + "b"})
    elif change == "name":
        selected = name[:-1] + "b"
    else:
        monkeypatch.setattr(type(backend), "_mounted_volume_identity", lambda *a: "b" * 64)
    with pytest.raises(CellResourceError):
        await source_volume_call(manager, selected, "write_volume_files", {"new": b"unsafe"})
    assert client.volumes.items[name].files == {"old": b"old"}
