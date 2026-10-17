"""Stable retained candidate identity never falls back to names or mutable ctime."""

import pytest

from tests.test_docker_machine_backend import retained_preview_fixture
from yleum_orchestrator.core.cell_resources import CellResourceError


def test_recovery_identity_requires_stable_birthtime_for_every_retained_volume(
    tmp_path, monkeypatch
):
    runtime, reference, volumes, _, _ = retained_preview_fixture(tmp_path, restored_code=True)
    births = [[1, index + 1, 1700000000, 10] for index in range(len(volumes))]
    monkeypatch.setattr(runtime, "_retained_volume_stats", lambda names, **kwargs: births)
    first = runtime.recovery_volume_identity(reference)
    births[-1][-1] += 1
    assert runtime.recovery_volume_identity(reference) != first
    monkeypatch.setattr(runtime, "_retained_volume_stats", lambda names, **kwargs: None)
    with pytest.raises(CellResourceError):
        runtime.recovery_volume_identity(reference)


@pytest.mark.parametrize("key", ["omnia.owner_id", "omnia.project_id", "omnia.namespace"])
def test_recovery_identity_rejects_foreign_volume_binding(tmp_path, monkeypatch, key):
    runtime, reference, volumes, _, _ = retained_preview_fixture(tmp_path, restored_code=True)
    monkeypatch.setattr(
        runtime,
        "_retained_volume_stats",
        lambda names, **kwargs: [[1, index + 1, 1700000000, 10] for index in range(len(names))],
    )
    volumes[runtime.workspace_volume].attrs["Labels"][key] = "foreign"
    with pytest.raises(CellResourceError):
        runtime.recovery_volume_identity(reference)


def runtime_fixture(tmp_path, monkeypatch):
    from types import SimpleNamespace

    runtime, reference, _, _, _ = retained_preview_fixture(tmp_path, restored_code=True)
    metadata = runtime._metadata()
    metadata["epoch"] = 9
    monkeypatch.setattr(runtime, "_metadata", lambda: metadata)
    containers = {}
    for kind in ("development", "project-postgres"):
        containers[kind] = SimpleNamespace(
            id="owned-" + kind,
            status="running",
            reload=lambda: None,
            attrs={
                "Image": reference.image_id if kind == "development" else "pg-pinned",
                "Config": {"Labels": {**runtime.labels(kind), "omnia.fencing_epoch": "9"}},
            },
        )
    monkeypatch.setattr(runtime, "_container", lambda: containers["development"])
    monkeypatch.setattr(runtime, "_project_postgres", lambda: containers["project-postgres"])
    monkeypatch.setattr(runtime, "_project_postgres_matches", lambda *a: True)
    runtime.client.images.get = lambda image: SimpleNamespace(id="pg-pinned")
    containers["development"].attrs["Mounts"] = [
        {"Type": "volume", "Name": name, "Destination": mount["bind"], "RW": True}
        for name, mount in runtime.volume_mapping(reference.manifest).items()
    ] + [
        {
            "Type": "volume",
            "Name": runtime.stem + "-logs",
            "Destination": "/run/omnia-logs",
            "RW": True,
        }
    ]
    containers["development"].attrs["HostConfig"] = {"NetworkMode": "container:own-guard"}
    runtime.client.containers.list = lambda **kwargs: []
    return runtime, reference, containers


def test_recovery_runtime_proof_requires_exact_running_owned_containers(tmp_path, monkeypatch):
    runtime, reference, containers = runtime_fixture(tmp_path, monkeypatch)
    assert runtime.recovery_runtime_proof(reference, epoch=9)["epoch"] == 9
    containers["development"].attrs["Config"]["Labels"]["omnia.fencing_epoch"] = "8"
    with pytest.raises(CellResourceError):
        runtime.recovery_runtime_proof(reference, epoch=9)
    containers["development"].attrs["Config"]["Labels"]["omnia.fencing_epoch"] = "9"
    containers["project-postgres"].status = "exited"
    with pytest.raises(CellResourceError):
        runtime.recovery_runtime_proof(reference, epoch=9)


@pytest.mark.parametrize(
    "drift", ["postgres_image", "postgres_config", "source_mount", "extra_writer"]
)
def test_resumed_runtime_proof_rejects_unbound_physical_runtime(tmp_path, monkeypatch, drift):
    from types import SimpleNamespace

    runtime, reference, containers = runtime_fixture(tmp_path, monkeypatch)
    if drift == "postgres_image":
        containers["project-postgres"].attrs["Image"] = "foreign-image"
    elif drift == "postgres_config":
        monkeypatch.setattr(runtime, "_project_postgres_matches", lambda *a: False)
    elif drift == "source_mount":
        containers["development"].attrs["Mounts"][0]["Name"] = "wrong-instance"
    else:
        runtime.client.containers.list = lambda **kwargs: [SimpleNamespace(id="unknown-writer")]
    with pytest.raises(CellResourceError):
        runtime.recovery_runtime_proof(reference, epoch=9)
