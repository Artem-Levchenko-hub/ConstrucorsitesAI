from __future__ import annotations

import json
import multiprocessing
import os
import re
import stat
import traceback
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from pathlib import Path
from uuid import UUID, uuid4

import pytest

import yleum_orchestrator.services.project_database_credentials as credential_module
from yleum_orchestrator.services.project_database_credentials import (
    ProjectDatabaseCredentials,
    ProjectDatabaseCredentialStore,
)


def _create(root: str, workspace_id: str) -> tuple[str, str, str]:
    result = ProjectDatabaseCredentialStore(root).load_or_create(UUID(workspace_id))
    return result.runtime_password, result.migrator_password, result.admin_password


def _payload(path: Path) -> dict[str, object]:
    return json.loads(path.read_text())


def _rewrite(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload))
    path.chmod(0o600)


def test_credentials_are_independent_stable_private_and_uuid_bound(tmp_path: Path) -> None:
    root = tmp_path / "private" / "project-database"
    store = ProjectDatabaseCredentialStore(root)
    workspace_id = uuid4()
    first = store.load_or_create(workspace_id)
    assert first == store.load_or_create(workspace_id)
    assert first == ProjectDatabaseCredentialStore(root).load(workspace_id)
    passwords = (first.runtime_password, first.migrator_password, first.admin_password)
    assert len(set(passwords)) == 3
    assert all(re.fullmatch(r"[A-Za-z0-9_-]{43}", value) for value in passwords)
    path = root / f"{workspace_id}.json"
    assert _payload(path) == {
        "version": 1,
        "workspace_id": str(workspace_id),
        "runtime_password": first.runtime_password,
        "migrator_password": first.migrator_password,
        "admin_password": first.admin_password,
    }
    assert stat.S_IMODE(root.stat().st_mode) == 0o700
    assert stat.S_IMODE(root.parent.stat().st_mode) == 0o700
    assert all(stat.S_IMODE(item.stat().st_mode) == 0o600 for item in root.iterdir())
    other = store.load_or_create(uuid4())
    assert set(passwords).isdisjoint(
        {other.runtime_password, other.migrator_password, other.admin_password}
    )
    assert not any(password in repr(first) for password in passwords)
    assert "password" not in repr(first)


@pytest.mark.parametrize("processes", [False, True])
def test_concurrent_creators_observe_one_complete_bundle(tmp_path: Path, processes: bool) -> None:
    root = tmp_path / "authority"
    workspace_id = uuid4()
    if processes:
        with ProcessPoolExecutor(
            max_workers=6, mp_context=multiprocessing.get_context("fork")
        ) as executor:
            results = list(executor.map(_create, [str(root)] * 24, [str(workspace_id)] * 24))
    else:
        with ThreadPoolExecutor(max_workers=12) as executor:
            results = list(executor.map(_create, [str(root)] * 48, [str(workspace_id)] * 48))
    assert len(set(results)) == 1
    assert results[0] == _create(str(root), str(workspace_id))
    assert set(item.suffix for item in root.iterdir()) == {".json", ".lock"}


def test_missing_load_fails_without_allocating_authority(tmp_path: Path) -> None:
    root = tmp_path / "authority"
    with pytest.raises(RuntimeError, match="credential"):
        ProjectDatabaseCredentialStore(root).load(uuid4())
    assert not root.exists()


@pytest.mark.parametrize("mode", [0o644, 0o660, 0o400, 0o1600])
def test_unsafe_credential_modes_fail_closed(tmp_path: Path, mode: int) -> None:
    store = ProjectDatabaseCredentialStore(tmp_path / "authority")
    workspace_id = uuid4()
    store.load_or_create(workspace_id)
    path = store.root / f"{workspace_id}.json"
    path.chmod(mode)
    before = path.read_bytes()
    for action in (store.load, store.load_or_create):
        with pytest.raises(RuntimeError, match="credential"):
            action(workspace_id)
    assert path.read_bytes() == before
    assert stat.S_IMODE(path.stat().st_mode) == mode


@pytest.mark.parametrize("mode", [0o755, 0o770, 0o600, 0o1700])
def test_unsafe_authority_directory_fails_closed(tmp_path: Path, mode: int) -> None:
    root = tmp_path / "authority"
    root.mkdir(mode=mode)
    root.chmod(mode)
    with pytest.raises(RuntimeError, match="credential"):
        ProjectDatabaseCredentialStore(root).load_or_create(uuid4())
    assert stat.S_IMODE(root.stat().st_mode) == mode
    assert not list(root.iterdir())


@pytest.mark.parametrize("location", ["root", "ancestor", "credentials", "lock"])
def test_symlinks_are_rejected_without_touching_target(tmp_path: Path, location: str) -> None:
    target = tmp_path / "target"
    target.mkdir(mode=0o700)
    root = tmp_path / "authority"
    workspace_id = uuid4()
    if location == "root":
        root.symlink_to(target, target_is_directory=True)
    elif location == "ancestor":
        root.symlink_to(target, target_is_directory=True)
        root = root / "nested"
    else:
        root.mkdir(mode=0o700)
        suffix = ".json" if location == "credentials" else ".lock"
        (root / f"{workspace_id}{suffix}").symlink_to(target / "secret")
    before = list(target.iterdir())
    with pytest.raises(RuntimeError, match="credential"):
        ProjectDatabaseCredentialStore(root).load_or_create(workspace_id)
    assert list(target.iterdir()) == before


@pytest.mark.parametrize("suffix", [".json", ".lock"])
@pytest.mark.parametrize("kind", ["hardlink", "fifo", "directory"])
def test_nonprivate_or_nonregular_files_fail_closed(tmp_path: Path, suffix: str, kind: str) -> None:
    root = tmp_path / "authority"
    root.mkdir(mode=0o700)
    workspace_id = uuid4()
    path = root / f"{workspace_id}{suffix}"
    if kind == "hardlink":
        target = tmp_path / "secret"
        target.write_text("unrelated secret")
        target.chmod(0o600)
        path.hardlink_to(target)
    elif kind == "fifo":
        os.mkfifo(path, 0o600)
    else:
        path.mkdir(mode=0o700)
    with pytest.raises(RuntimeError, match="credential"):
        ProjectDatabaseCredentialStore(root).load_or_create(workspace_id)
    if kind == "hardlink":
        assert target.read_text() == "unrelated secret"


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("version", 2),
        ("version", True),
        ("version", "1"),
        ("workspace_id", str(uuid4())),
        ("workspace_id", None),
        ("runtime_password", "short"),
        ("runtime_password", "x" * 129),
        ("runtime_password", "é" * 43),
        ("runtime_password", " " * 43),
        ("runtime_password", "postgresql://password@localhost/db"),
        ("migrator_password", None),
        ("admin_password", 123),
        ("unknown", "private-value"),
    ],
)
def test_tampered_protocol_fails_closed(tmp_path: Path, key: str, value: object) -> None:
    store = ProjectDatabaseCredentialStore(tmp_path / "authority")
    workspace_id = uuid4()
    credentials = store.load_or_create(workspace_id)
    path = store.root / f"{workspace_id}.json"
    payload = _payload(path)
    payload[key] = value
    _rewrite(path, payload)
    before = path.read_bytes()
    for action in (store.load, store.load_or_create):
        with pytest.raises(RuntimeError, match="credential") as error:
            action(workspace_id)
        rendered = "".join(traceback.format_exception(error.value))
        assert credentials.runtime_password not in rendered
        assert credentials.migrator_password not in rendered
        assert credentials.admin_password not in rendered
        assert "postgresql://" not in rendered
    assert path.read_bytes() == before


@pytest.mark.parametrize(
    "tamper", ["duplicate_password", "missing", "json", "list", "duplicate_key"]
)
def test_invalid_bundle_is_never_replaced(tmp_path: Path, tamper: str) -> None:
    store = ProjectDatabaseCredentialStore(tmp_path / "authority")
    workspace_id = uuid4()
    store.load_or_create(workspace_id)
    path = store.root / f"{workspace_id}.json"
    payload = _payload(path)
    if tamper == "duplicate_password":
        payload["admin_password"] = payload["runtime_password"]
        _rewrite(path, payload)
    elif tamper == "missing":
        del payload["admin_password"]
        _rewrite(path, payload)
    elif tamper == "list":
        _rewrite(path, [payload])
    elif tamper == "duplicate_key":
        path.write_text(json.dumps(payload)[:-1] + ',"version":1}')
    else:
        path.write_text('{"admin_password":"private-password",')
    before = path.read_bytes()
    with pytest.raises(RuntimeError, match="credential") as error:
        store.load_or_create(workspace_id)
    assert "private-password" not in "".join(traceback.format_exception(error.value))
    assert path.read_bytes() == before


@pytest.mark.parametrize("workspace_id", ["../escape", "not-a-uuid", None, 123])
def test_only_uuid_input_can_address_authority(tmp_path: Path, workspace_id: object) -> None:
    root = tmp_path / "authority"
    with pytest.raises(RuntimeError, match="credential"):
        ProjectDatabaseCredentialStore(root).load_or_create(workspace_id)  # type: ignore[arg-type]
    assert not root.exists()


def test_direct_bundle_repr_does_not_expose_passwords() -> None:
    credentials = ProjectDatabaseCredentials("runtime-secret", "migrator-secret", "admin-secret")
    assert "secret" not in repr(credentials)


@pytest.mark.parametrize("operation", ["load", "load_or_create"])
def test_missing_workspace_is_not_recovered_from_another_bundle(
    tmp_path: Path, operation: str
) -> None:
    store = ProjectDatabaseCredentialStore(tmp_path / "authority")
    first_id, second_id = uuid4(), uuid4()
    first = store.load_or_create(first_id)
    first_path = store.root / f"{first_id}.json"
    second_path = store.root / f"{second_id}.json"
    second_path.write_bytes(first_path.read_bytes())
    second_path.chmod(0o600)
    with pytest.raises(RuntimeError, match="credential"):
        getattr(store, operation)(second_id)
    assert store.load(first_id) == first


def test_untrusted_writable_ancestor_is_rejected_before_creation(tmp_path: Path) -> None:
    ancestor = tmp_path / "untrusted"
    ancestor.mkdir(mode=0o777)
    ancestor.chmod(0o777)
    root = ancestor / "authority"
    with pytest.raises(RuntimeError, match="credential"):
        ProjectDatabaseCredentialStore(root).load_or_create(uuid4())
    assert not root.exists()


@pytest.mark.skipif(os.geteuid() != 0, reason="physical ownership test requires chown capability")
@pytest.mark.parametrize("entry", ["directory", "credentials", "lock"])
def test_foreign_owned_authority_entries_are_rejected(tmp_path: Path, entry: str) -> None:
    store = ProjectDatabaseCredentialStore(tmp_path / "authority")
    workspace_id = uuid4()
    store.load_or_create(workspace_id)
    if entry == "directory":
        path = store.root
    else:
        suffix = ".json" if entry == "credentials" else ".lock"
        path = store.root / f"{workspace_id}{suffix}"
    os.chown(path, 65534, 65534)
    with pytest.raises(RuntimeError, match="credential"):
        store.load_or_create(workspace_id)
    assert path.stat().st_uid == 65534


def test_failed_staging_sync_leaves_no_credential_or_partial_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "authority"
    root.mkdir(mode=0o700)
    store = ProjectDatabaseCredentialStore(root)
    workspace_id = uuid4()
    original_fsync = credential_module.os.fsync

    def reject_file_sync(fd: int) -> None:
        if stat.S_ISREG(os.fstat(fd).st_mode):
            raise OSError("injected-private-secret")
        original_fsync(fd)

    with monkeypatch.context() as faults:
        faults.setattr(credential_module.os, "fsync", reject_file_sync)
        with pytest.raises(RuntimeError, match="credential") as error:
            store.load_or_create(workspace_id)
        assert "injected-private-secret" not in "".join(traceback.format_exception(error.value))
    assert list(root.glob("*.json")) == []
    assert list(root.glob("*.tmp")) == []
    result = store.load_or_create(workspace_id)
    assert store.load(workspace_id) == result


def test_directory_sync_failure_reports_error_and_keeps_one_complete_bundle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "authority"
    root.mkdir(mode=0o700)
    store = ProjectDatabaseCredentialStore(root)
    workspace_id = uuid4()
    original_fsync = credential_module.os.fsync

    def reject_directory_sync(fd: int) -> None:
        if stat.S_ISDIR(os.fstat(fd).st_mode):
            raise OSError("injected failure")
        original_fsync(fd)

    with monkeypatch.context() as faults:
        faults.setattr(credential_module.os, "fsync", reject_directory_sync)
        with pytest.raises(RuntimeError, match="credential"):
            store.load_or_create(workspace_id)
    path = root / f"{workspace_id}.json"
    before = path.read_bytes()
    result = store.load(workspace_id)
    assert store.load_or_create(workspace_id) == result
    assert path.read_bytes() == before
    assert list(root.glob("*.tmp")) == []


def test_unconfirmed_publication_blocks_reads_until_directory_sync_recovers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "authority"
    root.mkdir(mode=0o700)
    store = ProjectDatabaseCredentialStore(root)
    workspace_id = uuid4()
    original_fsync = credential_module.os.fsync
    directory_sync_attempts = 0

    def reject_directory_sync(fd: int) -> None:
        nonlocal directory_sync_attempts
        if stat.S_ISDIR(os.fstat(fd).st_mode):
            directory_sync_attempts += 1
            raise OSError("private-durability-failure")
        original_fsync(fd)

    with monkeypatch.context() as faults:
        faults.setattr(credential_module.os, "fsync", reject_directory_sync)
        with pytest.raises(RuntimeError, match="credential"):
            store.load_or_create(workspace_id)
        path = root / f"{workspace_id}.json"
        before = path.read_bytes()
        payload = _payload(path)
        passwords = [
            payload[key] for key in ("runtime_password", "migrator_password", "admin_password")
        ]
        for action in (store.load, store.load_or_create):
            with pytest.raises(RuntimeError, match="credential") as error:
                action(workspace_id)
            rendered = "".join(traceback.format_exception(error.value))
            assert "private-durability-failure" not in rendered
            assert all(password not in rendered for password in passwords)
            assert path.read_bytes() == before
        assert directory_sync_attempts == 3
    result = store.load(workspace_id)
    assert (result.runtime_password, result.migrator_password, result.admin_password) == tuple(
        passwords
    )
    assert store.load_or_create(workspace_id) == result
    assert path.read_bytes() == before
    assert list(root.glob("*.tmp")) == []


def test_oversized_and_non_utf8_records_fail_closed(tmp_path: Path) -> None:
    store = ProjectDatabaseCredentialStore(tmp_path / "authority")
    workspace_id = uuid4()
    store.load_or_create(workspace_id)
    path = store.root / f"{workspace_id}.json"
    for record in (b" " * 4097, b'"\xff"'):
        path.write_bytes(record)
        with pytest.raises(RuntimeError, match="credential"):
            store.load_or_create(workspace_id)
        assert path.read_bytes() == record


def test_uuid_subclass_cannot_override_authority_path(tmp_path: Path) -> None:
    class UnsafeUUID(UUID):
        def __str__(self) -> str:
            return "../escaped"

    root = tmp_path / "authority"
    with pytest.raises(RuntimeError, match="credential"):
        ProjectDatabaseCredentialStore(root).load_or_create(UnsafeUUID(str(uuid4())))
    assert not root.exists()
    assert not (tmp_path / "escaped.json").exists()
    assert not (tmp_path / "escaped.lock").exists()
