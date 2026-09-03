"""Integrity and ownership must be checked before importing any Docker artifact."""

import importlib
import importlib.util
from uuid import uuid4

import pytest


def module():
    name = "omnia_orchestrator.services.machine_environment"
    assert importlib.util.find_spec(name) is not None, "environment recovery is missing"
    return importlib.import_module(name)


class ArchiveBackend:
    def __init__(self):
        self.running = True
        self.image = b"rootfs with /usr/bin/jq and python environment"
        self.volumes = {
            "repo": b"source and node_modules symlink archive",
            "home": b"home and pip cache archive",
        }
        self.imported = False
        self.restore_pending = False
        self.fail_volume = None
        self.previous = None
        self.events = []

    def prepare_capture(self):
        self.events.append("quiesce")

    def validate_restore(self, reference):
        self.events.append("restore_check")
        if getattr(self, "fail_check", False):
            raise RuntimeError("restore check failed")

    def stop(self):
        self.running = False
        self.events.append("stop")

    def export_image(self):
        assert not self.running
        return "sha256:" + "a" * 64, iter([self.image])

    def export_volume(self, name):
        return iter([self.volumes[name]])

    def import_image(self, path, image_id):
        self.image = path.read_bytes()
        self.imported = True

    def import_volume(self, name, path):
        if name == self.fail_volume:
            self.volumes[name] = b"partially imported"
            raise RuntimeError("volume import failed")
        self.volumes[name] = path.read_bytes()

    def begin_restore(self, reference):
        self.restore_pending = True
        self.previous = (self.image, dict(self.volumes))

    def finish_restore(self):
        self.events.append("activate")
        self.restore_pending = False


def test_environment_and_volumes_survive_full_recreation(tmp_path):
    api = module()
    owner = uuid4()
    backend = ArchiveBackend()
    store = api.MachineEnvironmentStore(tmp_path, owner, backend, max_bytes=4096)
    ref = store.capture(
        manifest_digest="b" * 64, base_image="sha256:" + "c" * 64, volumes=("repo", "home")
    )
    backend.image = b""
    backend.volumes.clear()
    store.restore(ref, manifest_digest="b" * 64)
    assert backend.image == b"rootfs with /usr/bin/jq and python environment"
    assert backend.volumes["repo"] == b"source and node_modules symlink archive"
    assert not backend.running


def test_streaming_capture_stops_at_parent_deadline_without_publishing_artifact(
    tmp_path, monkeypatch
):
    from types import SimpleNamespace

    from omnia_orchestrator.services import project_machine

    clock = [0]
    monkeypatch.setattr(project_machine, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    store = module().MachineEnvironmentStore(tmp_path, uuid4(), ArchiveBackend(), max_bytes=4096)

    def chunks():
        yield b"first chunk"
        clock[0] = 2
        yield b"too late"

    with project_machine.machine_budget(1), pytest.raises(TimeoutError, match="budget"):
        store._save(chunks(), 4096)
    assert list(store.root.iterdir()) == []


def test_corrupt_volume_prevents_even_image_import(tmp_path):
    api = module()
    backend = ArchiveBackend()
    store = api.MachineEnvironmentStore(tmp_path, uuid4(), backend, max_bytes=4096)
    ref = store.capture(
        manifest_digest="b" * 64, base_image="sha256:" + "c" * 64, volumes=("repo", "home")
    )
    artifact = store.artifact_path(ref.volumes[0].artifact_ref)
    artifact.write_bytes(b"corrupt")
    with pytest.raises(api.EnvironmentIntegrityError, match="digest"):
        store.restore(ref, manifest_digest="b" * 64)
    assert not backend.imported


def test_cross_project_and_manifest_mismatch_never_restore(tmp_path):
    api = module()
    backend = ArchiveBackend()
    owner = uuid4()
    store = api.MachineEnvironmentStore(tmp_path, owner, backend, max_bytes=4096)
    ref = store.capture(
        manifest_digest="b" * 64, base_image="sha256:" + "c" * 64, volumes=("repo",)
    )
    other = api.MachineEnvironmentStore(tmp_path, uuid4(), backend, max_bytes=4096)
    with pytest.raises(api.EnvironmentIntegrityError, match="identity"):
        other.restore(ref, manifest_digest="b" * 64)
    with pytest.raises(api.EnvironmentIntegrityError, match="manifest"):
        store.restore(ref, manifest_digest="d" * 64)
    assert not backend.imported


def test_artifact_budget_and_reference_traversal_fail_closed(tmp_path):
    api = module()
    store = api.MachineEnvironmentStore(tmp_path, uuid4(), ArchiveBackend(), max_bytes=2)
    with pytest.raises(api.EnvironmentIntegrityError, match="budget"):
        store.capture(manifest_digest="b" * 64, base_image="sha256:" + "c" * 64, volumes=("repo",))
    with pytest.raises(api.EnvironmentIntegrityError, match="reference"):
        store.artifact_path("../../other.tar")


def test_failed_second_volume_import_keeps_restore_barrier_and_previous_pair(tmp_path):
    api = module()
    backend = ArchiveBackend()
    store = api.MachineEnvironmentStore(tmp_path, uuid4(), backend, max_bytes=4096)
    ref = store.capture(
        manifest_digest="b" * 64, base_image="sha256:" + "c" * 64, volumes=("repo", "home")
    )
    backend.image = b"newer rootfs"
    backend.volumes = {"repo": b"newer source", "home": b"newer home"}
    backend.fail_volume = "home"
    with pytest.raises(RuntimeError, match="volume import failed"):
        store.restore(ref, manifest_digest="b" * 64)
    assert backend.restore_pending
    assert backend.previous == (b"newer rootfs", {"repo": b"newer source", "home": b"newer home"})
    backend.fail_volume = None
    store.restore(ref, manifest_digest="b" * 64)
    assert not backend.restore_pending
    assert backend.volumes["home"] == b"home and pip cache archive"


def test_quiesce_precedes_capture_and_failed_restore_checks_never_activate(tmp_path):
    api = module()
    backend = ArchiveBackend()
    store = api.MachineEnvironmentStore(tmp_path, uuid4(), backend, max_bytes=4096)
    ref = store.capture(
        manifest_digest="b" * 64, base_image="sha256:" + "c" * 64, volumes=("repo",)
    )
    assert backend.events[:2] == ["quiesce", "stop"]
    backend.fail_check = True
    with pytest.raises(RuntimeError, match="restore check failed"):
        store.restore(ref, manifest_digest="b" * 64)
    assert backend.restore_pending and "activate" not in backend.events
