"""Private backups must authenticate their operation and never overwrite evidence."""

import importlib
import os
from uuid import uuid4

import pytest


def implementation():
    name = "yleum_orchestrator.services.publication_migration_backup"
    assert importlib.util.find_spec(name), "encrypted publication backup implementation missing"
    return importlib.import_module(name)


@pytest.fixture(autouse=True)
def directory_fsync_on_windows(monkeypatch):
    # Production Linux fsync remains mandatory; Windows has no O_DIRECTORY.
    if os.name == "nt":
        monkeypatch.setattr(implementation(), "_sync_directory", lambda path: None)


def test_backup_roundtrip_is_private_bound_and_exclusive(tmp_path):
    m = implementation()
    store = m.PrivateBackup(tmp_path, b"k" * 32, max_bytes=4096)
    project, operation = uuid4(), uuid4()
    receipt = store.save(project, operation, b"private order rows", {"binding": "one"})
    assert store.load(project, operation, receipt, {"binding": "one"}) == b"private order rows"
    assert b"private order rows" not in store.path(project, operation).read_bytes()
    with pytest.raises(m.MigrationIOError):
        store.load(project, operation, receipt, {"binding": "other"})
    with pytest.raises(m.MigrationIOError):
        store.save(project, operation, b"replacement", {"binding": "one"})


def test_backup_corruption_and_oversize_fail_without_plaintext(tmp_path):
    m = implementation()
    store = m.PrivateBackup(tmp_path, b"k" * 32, max_bytes=32)
    project, operation = uuid4(), uuid4()
    with pytest.raises(m.MigrationIOError):
        store.save(project, operation, b"x" * 33, {})
    assert not store.path(project, operation).exists()
    receipt = store.save(project, operation, b"a", {})
    path = store.path(project, operation)
    payload = bytearray(path.read_bytes())
    payload[-1] ^= 1
    path.write_bytes(payload)
    with pytest.raises(m.MigrationIOError):
        store.load(project, operation, receipt, {})


def test_globals_only_removes_exact_bootstrap_postgres_create():
    m = implementation()
    assert m.restore_globals("CREATE ROLE postgres;\nALTER ROLE postgres WITH SUPERUSER;") == (
        "ALTER ROLE postgres WITH SUPERUSER;"
    )
    with pytest.raises(m.MigrationIOError):
        m.restore_globals("CREATE ROLE postgres WITH LOGIN; ALTER ROLE postgres WITH SUPERUSER;")
    with pytest.raises(m.MigrationIOError):
        m.restore_globals("CREATE ROLE postgres; CREATE ROLE postgres;")


def test_backup_bundle_preserves_bytes_and_rejects_trailing_data():
    m = implementation()
    data = m.pack_backup(b"globals", b"\x00\xffPGDMP")
    assert m.unpack_backup(data) == (b"globals", b"\x00\xffPGDMP")
    with pytest.raises(m.MigrationIOError):
        m.unpack_backup(data + b"unexpected")


def test_sandbox_schema_mismatch_blocks_proof():
    m = implementation()
    with pytest.raises(m.MigrationIOError):
        m.require_schema("a" * 64, "b" * 64)


def test_project_backup_budget_rejects_unbounded_retention(tmp_path):
    m = implementation()
    store = m.PrivateBackup(tmp_path, b"k" * 32, max_bytes=32)
    project = uuid4()
    for _ in range(4):
        store.save(project, uuid4(), b"x" * 32, {})
    with pytest.raises(m.MigrationIOError):
        store.save(project, uuid4(), b"x", {})
