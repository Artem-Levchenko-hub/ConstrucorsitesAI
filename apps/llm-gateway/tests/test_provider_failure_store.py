"""Real isolated POSIX filesystem protocol; never production paths or network."""

from __future__ import annotations

import json
import multiprocessing
import os
import stat
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError
from uuid import UUID, uuid4

import pytest

from yleum_gateway.services import provider_failure_store as store


def packet():
    return {"schema_version": 1, "diagnostic_visibility": "operator_only",
            "provider_scope": "llmgw", "upstream_http_status": 422,
            "response_body": {"sha256": "a" * 64, "bytes": 20,
                              "content_state": "redacted", "utf8": "safe private reason"}}


def _hold_private_lock(path, ready, release):
    import fcntl

    descriptor = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        ready.set()
        release.wait(10)
    finally:
        os.close(descriptor)


def _store_once(results):
    results.put(store.store_provider_failure(packet()))


@pytest.fixture
def private_root(tmp_path, monkeypatch):
    if os.name != "posix":
        pytest.skip("Requires actual POSIX dirfd/O_NOFOLLOW/flock; no Windows fallback")
    root = tmp_path / "private-failures"
    root.mkdir(mode=0o700)
    root.chmod(0o700)
    monkeypatch.setattr(store, "_DIRECTORY", root)
    return root


def test_result_is_immutable_and_invalid_packet_writes_nothing(tmp_path, monkeypatch):
    result = store.StoreResult("withheld", None, "invalid_packet")
    with pytest.raises(FrozenInstanceError):
        result.state = "stored"
    root = tmp_path / "must-not-create"
    monkeypatch.setattr(store, "_DIRECTORY", root)
    for bad in ({}, {**packet(), "schema_version": True},
                {**packet(), "response_body": {"content_state": "withheld", "utf8": "bad"}}):
        assert store.store_provider_failure(bad).state == "withheld"
    assert not root.exists()


def test_packet_limit_is_checked_before_store_access(tmp_path, monkeypatch):
    root = tmp_path / "must-not-create"
    monkeypatch.setattr(store, "_DIRECTORY", root)
    value = {**packet(), "padding": "x" * (128 * 1024)}
    assert store.store_provider_failure(value) == store.StoreResult("withheld", None, "packet_budget_exceeded")
    assert not root.exists()


def test_actual_private_publish_modes_and_restart_read(private_root):
    value = packet()
    result = store.store_provider_failure(value)
    assert result.state == "stored" and str(UUID(result.artifact_id)) == result.artifact_id
    path = private_root / (result.artifact_id + ".json")
    raw = path.read_bytes()
    assert json.loads(raw)["packet"] == value
    assert json.loads(raw)["artifact_id"] == result.artifact_id
    assert stat.S_IMODE(private_root.stat().st_mode) == 0o700
    assert stat.S_IMODE(path.stat().st_mode) == 0o600 and path.stat().st_nlink == 1
    assert sorted(p.name for p in private_root.iterdir()) == [".lock", path.name]
    # Restart-style reading needs no module memory, configuration discovery or
    # provider call: only the opaque saved identifier and deployment-owned root.
    assert json.loads((private_root / f"{UUID(result.artifact_id)}.json").read_bytes())["packet"] == value
    assert store.store_provider_failure(value).state == "stored"


def test_missing_root_and_unsafe_permissions_are_never_created_or_repaired(private_root, monkeypatch):
    missing = private_root / "missing"
    monkeypatch.setattr(store, "_DIRECTORY", missing)
    assert store.store_provider_failure(packet()).state == "withheld"
    assert not missing.exists()
    monkeypatch.setattr(store, "_DIRECTORY", private_root)
    private_root.chmod(0o755)
    assert store.store_provider_failure(packet()).reason == "unsafe_store"
    assert stat.S_IMODE(private_root.stat().st_mode) == 0o755 and not list(private_root.iterdir())


@pytest.mark.parametrize("attack", ["directory-link", "lock-link", "packet-link", "hardlink", "unknown-name", "bad-mode"])
def test_symlink_hardlink_unknown_names_and_unsafe_files_fail_closed(private_root, tmp_path, monkeypatch, attack):
    outside = tmp_path / "outside"
    outside.write_bytes(b"private outside sentinel")
    outside.chmod(0o600)
    name = str(uuid4()) + ".json"
    if attack == "directory-link":
        link = tmp_path / "root-link"
        link.symlink_to(private_root, target_is_directory=True)
        monkeypatch.setattr(store, "_DIRECTORY", link)
    elif attack in {"lock-link", "packet-link"}:
        (private_root / (".lock" if attack == "lock-link" else name)).symlink_to(outside)
    elif attack == "hardlink":
        os.link(outside, private_root / name)
    else:
        entry = private_root / ("unknown-private-file" if attack == "unknown-name" else name)
        entry.write_bytes(b"existing private packet")
        entry.chmod(0o600 if attack == "unknown-name" else 0o644)
    assert store.store_provider_failure(packet()).state == "withheld"
    assert outside.read_bytes() == b"private outside sentinel"
    assert not any(p.name.startswith("pending-") for p in private_root.iterdir())


def test_wrong_owner_refuses_before_any_packet_write(private_root, monkeypatch):
    uid = os.geteuid()
    monkeypatch.setattr(store.os, "geteuid", lambda: uid + 1)
    assert store.store_provider_failure(packet()).reason == "unsafe_store"
    assert not list(private_root.iterdir())


def test_collision_never_overwrites_or_deletes_existing_artifact(private_root, monkeypatch):
    identity = uuid4()
    monkeypatch.setattr(store, "uuid4", lambda: identity)
    first = store.store_provider_failure(packet())
    assert first.state == "stored"
    path = private_root / f"{identity}.json"
    original = path.read_bytes()
    second = store.store_provider_failure(packet())
    assert second == store.StoreResult("withheld", None, "artifact_collision")
    assert path.read_bytes() == original
    assert (private_root / f"pending-{identity}.json").exists()


def test_file_and_total_byte_capacity_refuse_without_retention_cleanup(private_root, monkeypatch):
    monkeypatch.setattr(store, "_MAX_FILES", 3)
    first = store.store_provider_failure(packet())
    assert first.state == "stored"
    path = private_root / (first.artifact_id + ".json")
    original = path.read_bytes()
    assert store.store_provider_failure(packet()).reason == "store_capacity_exceeded"
    assert path.read_bytes() == original
    monkeypatch.setattr(store, "_MAX_FILES", 1024)
    monkeypatch.setattr(store, "_MAX_TOTAL_BYTES", 1)
    assert store.store_provider_failure(packet()).reason == "store_capacity_exceeded"
    assert path.read_bytes() == original


def test_atomic_publish_has_no_partial_final_and_retains_failed_pending(private_root, monkeypatch):
    real_link = os.link

    def inspect_then_link(src, dst, **kwargs):
        assert not (private_root / dst).exists()
        assert json.loads((private_root / src).read_bytes())["packet"] == packet()
        real_link(src, dst, **kwargs)

    monkeypatch.setattr(store.os, "link", inspect_then_link)
    assert store.store_provider_failure(packet()).state == "stored"

    def unavailable(*args, **kwargs):
        raise OSError("never export exception or existing packet content")

    monkeypatch.setattr(store.os, "link", unavailable)
    assert store.store_provider_failure(packet()).state == "withheld"
    pending = list(private_root.glob("pending-*.json"))
    assert len(pending) == 1 and json.loads(pending[0].read_bytes())["packet"] == packet()
    assert stat.S_IMODE(pending[0].stat().st_mode) == 0o600


def test_cross_process_lock_wait_is_bounded_and_release_restores_writes(private_root):
    context = multiprocessing.get_context("fork")
    ready, release = context.Event(), context.Event()
    process = context.Process(target=_hold_private_lock, args=(private_root / ".lock", ready, release))
    process.start()
    try:
        assert ready.wait(5)
        started = time.monotonic()
        assert store.store_provider_failure(packet()).reason == "store_busy"
        assert 0.15 <= time.monotonic() - started < 1
    finally:
        release.set()
        process.join(5)
        if process.is_alive():
            process.terminate()
            process.join(5)
    assert not process.is_alive() and process.exitcode == 0
    assert store.store_provider_failure(packet()).state == "stored"


def test_short_cross_process_contention_preserves_both_packets(private_root, monkeypatch):
    context = multiprocessing.get_context("fork")
    ready, results = context.Event(), context.Queue()
    publish = store._publish

    def brief_publish(*args):
        ready.set()
        time.sleep(0.06)
        publish(*args)

    monkeypatch.setattr(store, "_publish", brief_publish)
    process = context.Process(target=_store_once, args=(results,))
    process.start()
    monkeypatch.setattr(store, "_publish", publish)
    try:
        assert ready.wait(5)
        second = store.store_provider_failure(packet())
        first = results.get(timeout=5)
        assert first.state == second.state == "stored"
        assert first.artifact_id != second.artifact_id
    finally:
        process.join(5)
        if process.is_alive():
            process.terminate()
            process.join(5)
        results.close()
    assert not process.is_alive() and process.exitcode == 0
    assert len(list(private_root.glob("*.json"))) == 2


def test_concurrent_writers_keep_atomic_unique_bounded_packets(private_root, monkeypatch):
    monkeypatch.setattr(store, "_MAX_FILES", 10)
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: store.store_provider_failure(packet()), range(24)))
    saved = [value for value in results if value.state == "stored"]
    assert saved and len({value.artifact_id for value in saved}) == len(saved)
    assert all(value.state == "stored" or value.reason in {"store_busy", "store_capacity_exceeded"}
               for value in results)
    assert len(list(private_root.iterdir())) <= 10
    for value in saved:
        info = private_root / (value.artifact_id + ".json")
        assert json.loads(info.read_bytes())["packet"] == packet()
        assert stat.S_IMODE(info.stat().st_mode) == 0o600 and info.stat().st_nlink == 1
