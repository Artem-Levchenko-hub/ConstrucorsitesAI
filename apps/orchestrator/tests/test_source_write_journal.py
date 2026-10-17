import json
import os
import stat
from uuid import UUID, uuid4

import pytest
from docker.errors import APIError

from tests._generation_reader_settings import legacy_reader_process_settings  # noqa: F401
from tests.test_docker_py_cell_backend import (
    _backend,
    _docker_api_error,
    _FakeClient,
    _FakeVolume,
    _labels,
)
from yleum_orchestrator.core.cell_resources import CellIndeterminateOperation
from yleum_orchestrator.services.source_write_journal import (
    SourceWriteJournal,
    SourceWriteRecord,
    plan_source_helper,
    request_digest,
    source_write_scope,
)


def record(tmp_path, volume="workspace-vol"):
    labels = _labels("workspace")
    value = SourceWriteRecord(
        operation_id=uuid4(),
        workspace_id=UUID(labels["omnia.workspace_id"]),
        project_id=UUID(labels["omnia.project_id"]),
        owner_id=UUID(labels["omnia.owner_id"]),
        generation_run_id=uuid4(),
        fencing_epoch=4,
        physical_volume=volume,
        physical_volume_identity="d" * 64,
        request_sha256="a" * 64,
        before_revision="b" * 64,
    )
    journal = SourceWriteJournal(tmp_path / "state", value.workspace_id)
    journal.start(value)
    return journal, value, labels


async def test_helper_identity_is_durable_before_create_and_exact_collision_never_retries(
    tmp_path,
    monkeypatch,
):
    journal, value, labels = record(tmp_path)
    client = _FakeClient()
    client.volumes.items[value.physical_volume] = _FakeVolume(value.physical_volume, labels)
    calls = []

    def create(*args, **kwargs):
        saved = SourceWriteJournal(tmp_path / "state", value.workspace_id).load(value.operation_id)
        assert kwargs["name"] in saved.helpers
        assert saved.helpers[kwargs["name"]] == kwargs["labels"]
        assert kwargs["labels"]["omnia.source_operation_id"] == str(value.operation_id)
        calls.append(kwargs["name"])
        raise _docker_api_error(409, "The container name is already in use")

    monkeypatch.setattr(client.containers, "create", create)
    backend = _backend(client)

    async def identity(_self, _name, _expected, **_kwargs):
        return (
            None  # This test isolates durable planning; identity has separate real-probe coverage.
        )

    monkeypatch.setattr(type(backend), "_require_source_incarnation", identity)
    with source_write_scope(journal, value.operation_id):
        with pytest.raises(APIError):
            await backend.write_volume_files(value.physical_volume, {"x": b"never"})
    assert len(calls) == 1
    assert client.volumes.items[value.physical_volume].files == {}
    with pytest.raises(CellIndeterminateOperation):
        journal.start(value)


async def test_tracked_backend_write_and_delete_keep_exact_plans_after_cleanup(
    tmp_path, monkeypatch
):
    journal, value, labels = record(tmp_path)
    client = _FakeClient()
    client.volumes.items[value.physical_volume] = _FakeVolume(
        value.physical_volume,
        labels,
        files={"old": b"old"},
    )
    backend = _backend(client)

    async def identity(_self, _name, _expected, **_kwargs):
        return None

    monkeypatch.setattr(type(backend), "_require_source_incarnation", identity)
    with source_write_scope(journal, value.operation_id):
        await backend.delete_volume_paths(value.physical_volume, ("old",))
        await backend.write_volume_files(value.physical_volume, {"new": b"new"})
    saved = journal.load(value.operation_id)
    assert len(saved.helpers) == 2 and saved.receipt is None
    assert not client.containers.items
    assert client.volumes.items[value.physical_volume].files == {"new": b"new"}
    terminal = journal.finish(value, after="c" * 64, written=1, deleted=1)
    assert journal.start(value) == terminal
    journal.require_clear()


@pytest.mark.parametrize("field", ["workspace_id", "project_id", "owner_id", "physical_volume"])
def test_helper_binding_mismatch_never_records_plan(tmp_path, field):
    journal, value, labels = record(tmp_path)
    volume = value.physical_volume
    if field == "physical_volume":
        volume = "foreign"
    else:
        labels["omnia." + field] = str(uuid4())
    with source_write_scope(journal, value.operation_id), pytest.raises(CellIndeterminateOperation):
        plan_source_helper("helper", labels, volume)
    assert journal.load(value.operation_id).helpers == {}


def test_unknown_corrupt_or_forged_receipt_preserves_original(tmp_path):
    journal, value, _ = record(tmp_path)
    path = journal.root / f"{value.operation_id}.json"
    payload = json.loads(path.read_text())
    payload["version"] = 99
    path.write_text(json.dumps(payload))
    with pytest.raises(ValueError):
        journal.start(value)
    assert json.loads(path.read_text())["version"] == 99


def test_canonical_request_digest_is_order_independent_but_scope_sensitive():
    args = {
        "workspace_id": uuid4(),
        "generation_run_id": uuid4(),
        "fencing_epoch": 3,
        "expected_revision": "b" * 64,
        "files": {"b": "two", "a": "one"},
        "deletes": ("d", "c"),
    }
    first = request_digest(**args)
    assert (
        request_digest(**{**args, "files": {"a": "one", "b": "two"}, "deletes": ("c", "d")})
        == first
    )
    assert request_digest(**{**args, "fencing_epoch": 4}) != first
    assert request_digest(**{**args, "files": {"a": "ONE", "b": "two"}}) != first


def test_directory_durability_failure_never_exports_terminal(tmp_path, monkeypatch):
    journal, value, _ = record(tmp_path)
    original = os.fsync
    root_inode = journal.root.stat().st_ino

    def fail_directory(fd):
        info = os.fstat(fd)
        if stat.S_ISDIR(info.st_mode) and info.st_ino == root_inode:
            raise OSError("simulated directory durability failure")
        original(fd)

    monkeypatch.setattr(os, "fsync", fail_directory)
    with pytest.raises(OSError):
        journal.finish(value, after="c" * 64, written=1, deleted=0)
    restarted = SourceWriteJournal(tmp_path / "state", value.workspace_id)
    with pytest.raises(OSError):
        restarted.load(value.operation_id)
    with pytest.raises(OSError):
        restarted.start(value)
    monkeypatch.setattr(os, "fsync", original)
    terminal = restarted.start(value)
    assert terminal.receipt.after_revision == "c" * 64
    assert terminal.receipt.written == 1


def test_intent_directory_durability_failure_never_dispatches_helper(tmp_path, monkeypatch):
    journal, value, labels = record(tmp_path)
    original = os.fsync

    def fail_directory(fd):
        if stat.S_ISDIR(os.fstat(fd).st_mode):
            raise OSError("simulated intent durability failure")
        original(fd)

    monkeypatch.setattr(os, "fsync", fail_directory)
    with source_write_scope(journal, value.operation_id), pytest.raises(OSError):
        plan_source_helper("not-created", labels, value.physical_volume)
    assert journal.load(value.operation_id).helpers == {}
    with pytest.raises(CellIndeterminateOperation):
        journal.start(value)


@pytest.mark.parametrize("operation", ["write", "delete"])
async def test_replacement_between_preinspect_and_helper_mount_never_executes(
    tmp_path,
    monkeypatch,
    operation,
):
    from yleum_orchestrator.core.cell_resources import CellResourceError

    journal, value, labels = record(tmp_path)
    client = _FakeClient()
    client.volumes.items[value.physical_volume] = _FakeVolume(
        value.physical_volume,
        labels,
        files={"old": b"preserved"},
    )
    backend = _backend(client)

    async def before(_self, _name):
        return value.physical_volume_identity

    def after(_self, _helper, _name, _labels):
        return "e" * 64  # Docker mounted an incarnation different from preinspect.

    monkeypatch.setattr(type(backend), "source_volume_identity", before)
    monkeypatch.setattr(type(backend), "_mounted_volume_identity", after)
    with (
        source_write_scope(journal, value.operation_id),
        pytest.raises(
            CellResourceError,
            match="incarnation changed",
        ),
    ):
        if operation == "write":
            await backend.write_volume_files(value.physical_volume, {"new": b"forbidden"})
        else:
            await backend.delete_volume_paths(value.physical_volume, ("old",))
    assert client.volumes.items[value.physical_volume].files == {"old": b"preserved"}
    assert not client.containers.items
    assert journal.load(value.operation_id).receipt is None
