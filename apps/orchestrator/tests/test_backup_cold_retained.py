"""Retained PVC proof boundaries with inert Kubernetes I/O and real local tar bytes."""

from __future__ import annotations

import copy
import importlib.util
import io
import json
import os
import queue
import tarfile
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

import pytest
from scripts import backup_cells as cells

SOURCE = Path(__file__).resolve().parents[3] / "infra/backup/cold-retained.py"
SPEC = importlib.util.spec_from_file_location("cold_retained_contract", SOURCE)
cold = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(cold)
PROJECT = "11111111-1111-4111-8111-111111111111"
OWNER = "22222222-2222-4222-8222-222222222222"
NAMESPACE_UID = "33333333-3333-4333-8333-333333333333"
HELPER_UID = "44444444-4444-4444-8444-444444444444"
RELEASE = "55555555-5555-4555-8555-555555555555"
IMAGE = "postgres@sha256:" + "a" * 64
NAMESPACE = "app-" + PROJECT
AUTHORITY = {
    "project_id": PROJECT,
    "owner_id": OWNER,
    "workspace_id": HELPER_UID,
    "journal_sha256": "b" * 64,
    "lock_inode": 1,
    "lock_owner": 1000,
}


def uid(index):
    return str(UUID(int=10**30 + index))


def tar_bytes(members=None):
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w") as archive:
        for name, content, kind, link in members or [("payload.txt", b"retained", "file", "")]:
            row = tarfile.TarInfo(name)
            row.uid, row.gid, row.mode = 999, 999, 0o600
            if kind == "file":
                row.size = len(content)
            elif kind == "directory":
                row.type = tarfile.DIRTYPE
            elif kind == "symlink":
                row.type, row.linkname = tarfile.SYMTYPE, link
            elif kind == "hardlink":
                row.type, row.linkname = tarfile.LNKTYPE, link
            else:
                row.type = tarfile.FIFOTYPE
            archive.addfile(row, io.BytesIO(content) if kind == "file" else None)
    return stream.getvalue()


class FakeConnection:
    def __init__(self, events):
        self.events, self.sock = events, self

    def shutdown(self, _):
        self.events.put(None)

    def close(self):
        pass


class FakeStream:
    def __init__(self, events):
        self.events = events

    def readline(self, _):
        event = self.events.get(timeout=3)
        return json.dumps(event).encode() + b"\n" if event is not None else b""

    def close(self):
        pass


class Kube:
    def __init__(self, fault=None):
        self.calls, self.events, self.fault = [], queue.Queue(), fault
        self.pod, self.deleted, self.pv = None, [], {}
        self.claims = []
        for index, name in enumerate(
            (
                "data-core-postgres-0",
                "data-project-postgres-0",
                "code-55555555-abcdefab",
                "code-55555555-bcdefabc",
            )
        ):
            volume_uid, pv_name = uid(index), "pvc-" + uid(index)
            tags = {
                cold.PART: "max-app",
                cold.MANAGED: "omnia-orchestrator",
                cold.PROJECT: PROJECT,
                cold.OWNER: OWNER,
                cold.COMPONENT: (
                    "core-postgres" if index == 0 else "project-postgres" if index == 1 else "app"
                ),
            }
            if index >= 2:
                tags.update({"omnia.volume-kind": "code", "omnia.release-id": RELEASE})
            self.claims.append(
                {
                    "metadata": {
                        "name": name,
                        "namespace": NAMESPACE,
                        "uid": volume_uid,
                        "labels": tags,
                    },
                    "spec": {
                        "volumeName": pv_name,
                        "accessModes": ["ReadWriteOnce"],
                        "volumeMode": "Filesystem",
                        "storageClassName": "local-path",
                        "resources": {"requests": {"storage": "2Gi"}},
                    },
                    "status": {"phase": "Bound"},
                }
            )
            self.pv[pv_name] = {
                "metadata": {"name": pv_name, "uid": uid(100 + index)},
                "spec": {
                    "claimRef": {"namespace": NAMESPACE, "name": name, "uid": volume_uid},
                    "accessModes": ["ReadWriteOnce"],
                    "volumeMode": "Filesystem",
                    "storageClassName": "local-path",
                    "local": {"path": "/var/lib/rancher/k3s/storage/" + pv_name},
                    "nodeAffinity": {
                        "required": {
                            "nodeSelectorTerms": [
                                {
                                    "matchExpressions": [
                                        {
                                            "key": "kubernetes.io/hostname",
                                            "operator": "In",
                                            "values": ["runtime"],
                                        }
                                    ]
                                }
                            ]
                        }
                    },
                },
                "status": {"phase": "Bound"},
            }

    def emit(self, kind, row):
        self.events.put({"type": kind, "object": copy.deepcopy(row)})

    def watch(self, namespace, version):
        assert namespace == NAMESPACE and version == "100"
        self.calls.append(("WATCH", namespace, version))
        return FakeConnection(self.events), FakeStream(self.events)

    def request(self, method, path, body=None, *, missing=False, patch=False):
        self.calls.append((method, path, copy.deepcopy(body)))
        if method == "GET":
            if path == "/api/v1/namespaces/" + NAMESPACE:
                return {
                    "metadata": {
                        "name": NAMESPACE,
                        "uid": NAMESPACE_UID,
                        "labels": {"omnia.retired": "true"},
                    }
                }
            if path.endswith("/pods"):
                return {
                    "metadata": {"resourceVersion": "100"},
                    "items": [copy.deepcopy(self.pod)] if self.pod else [],
                }
            if path.endswith("/persistentvolumeclaims"):
                return {"items": copy.deepcopy(self.claims)}
            if "/persistentvolumes/" in path:
                return copy.deepcopy(self.pv[path.rsplit("/", 1)[1]])
            if "/pods/" in path:
                assert self.pod is not None or missing
                return copy.deepcopy(self.pod)
            return {"items": []}
        if method == "POST":
            self.pod = copy.deepcopy(body)
            self.pod["metadata"].update(uid=HELPER_UID, resourceVersion="101")
            self.pod["status"] = {
                "containerStatuses": [
                    {"name": "archive", "ready": True, "imageID": IMAGE, "state": {"running": {}}}
                ]
            }
            self.emit("ADDED", self.pod)
            if self.fault == "foreign_writer":
                foreign = copy.deepcopy(self.pod)
                foreign["metadata"]["name"] = "real-writer"
                self.emit("ADDED", foreign)
                self.emit("DELETED", foreign)
            elif self.fault == "watch_error":
                self.events.put({"type": "ERROR", "object": {"code": 410}})
            return copy.deepcopy(self.pod)
        if method == "PATCH":
            assert patch and body["metadata"]["uid"] == HELPER_UID
            assert body["metadata"]["resourceVersion"] == "101"
            self.pod["metadata"]["annotations"].update(body["metadata"]["annotations"])
            self.pod["metadata"]["resourceVersion"] = "102"
            if self.fault != "missing_barrier":
                self.emit("MODIFIED", self.pod)
            if self.fault == "replacement":
                self.pod["metadata"]["uid"] = uid(500)
            return copy.deepcopy(self.pod)
        assert method == "DELETE"
        assert body["preconditions"]["uid"] == HELPER_UID
        self.deleted.append(body["preconditions"]["uid"])
        if self.fault != "cleanup_stuck":
            self.pod = None
        return {}


class TarRunner:
    def __init__(self):
        self.calls = []
        self.nonces = []

    def run_to_file(self, args, *, path, timeout):
        raise AssertionError("cold collection must verify the source SHA before EOF")

    def run_acknowledged_to_file(self, args, *, path, timeout, nonce):
        import hashlib

        self.calls.append(args)
        assert len(nonce) == 32 and int(nonce, 16) >= 0
        assert nonce not in self.nonces
        self.nonces.append(nonce)
        assert "--stdin" in args and "--tty" not in args
        assert args[-2] == f"/source/{len(self.calls) - 1}" and args[-1] == nonce
        assert timeout == 1800
        payload = tar_bytes()
        path.write_bytes(payload)
        return cells.StreamResult(
            tuple(args), 0, len(payload), hashlib.sha256(payload).hexdigest(), b""
        )


def spec():
    return {
        "kubectl": "kubectl",
        "context": "default",
        "helper_image": IMAGE,
        "postgres_image": IMAGE,
    }


def test_full_cold_copy_uses_continuous_real_watch_and_uid_cleanup(tmp_path):
    api, runner = Kube(), TarRunner()
    result = cold.collect_one(cells, runner, spec(), AUTHORITY, tmp_path, api=api)
    assert len(result["volumes"]) == 4
    assert result["proof"]["cleanup_confirmed"] is True
    assert api.deleted == [HELPER_UID] and api.pod is None
    cold.validate(cells, tmp_path, [result])
    assert len(runner.calls) == 4
    first_mutation = next(index for index, row in enumerate(api.calls) if row[0] == "POST")
    assert any(row[0] == "WATCH" for row in api.calls[:first_mutation])
    mutations = [row for row in api.calls if row[0] in {"POST", "PATCH", "DELETE"}]
    assert all("/pods" in row[1] for row in mutations)
    assert len(mutations) == 3


@pytest.mark.parametrize(
    "fault", ["foreign_writer", "watch_error", "missing_barrier", "cleanup_stuck", "replacement"]
)
def test_unconfirmed_quiescence_or_cleanup_never_returns_complete(tmp_path, monkeypatch, fault):
    monkeypatch.setattr(cold, "_BARRIER_TIMEOUT", 0.05)
    monkeypatch.setattr(cold, "_CLEANUP_TIMEOUT", 0.05)
    api = Kube(fault)
    with pytest.raises(ValueError, match="proof is unconfirmed"):
        cold.collect_one(cells, TarRunner(), spec(), AUTHORITY, tmp_path, api=api)
    if fault == "replacement":
        assert api.deleted == [], "a changed UID must never be deleted"
    else:
        assert api.deleted == [HELPER_UID]


@pytest.mark.parametrize(
    "fault", ["owner", "binding", "missing_db", "unknown_pvc", "writable_plan"]
)
def test_invalid_cold_inventory_stops_before_any_helper_creation(tmp_path, fault):
    api = Kube()
    expected = None
    if fault == "owner":
        api.claims[0]["metadata"]["labels"][cold.OWNER] = uid(99)
    elif fault == "binding":
        next(iter(api.pv.values()))["spec"]["claimRef"]["uid"] = uid(99)
    elif fault == "missing_db":
        api.claims.pop(0)
    elif fault == "unknown_pvc":
        api.claims[-1]["metadata"]["name"] = "unrecognized-data"
    else:
        plan = cold.cold_census(api, AUTHORITY)
        expected = {"namespace_uid": uid(99), "volumes": plan["volumes"]}
    with pytest.raises(ValueError):
        cold.collect_one(
            cells, TarRunner(), spec(), AUTHORITY, tmp_path, api=api, expected_inventory=expected
        )
    assert not any(row[0] != "GET" for row in api.calls)


def test_helper_admission_cannot_add_writable_mount_or_service_token():
    plan = cold.cold_census(Kube(), AUTHORITY)
    expected = cold.helper_pod(plan, "omnia-backup-cold-abc", "intent", IMAGE)
    pod = copy.deepcopy(expected)
    pod["metadata"]["uid"] = HELPER_UID
    assert cold.own_pod(pod, expected) == HELPER_UID
    defaulted = copy.deepcopy(pod)
    for key in ("hostNetwork", "hostPID", "hostIPC"):
        del defaulted["spec"][key]
    assert cold.own_pod(defaulted, expected) == HELPER_UID
    for fault in ("token", "mount", "fsGroup", "sidecar", "pid", "env"):
        bad = copy.deepcopy(pod)
        if fault == "token":
            bad["spec"]["automountServiceAccountToken"] = True
        elif fault == "mount":
            bad["spec"]["containers"][0]["volumeMounts"][0]["readOnly"] = False
        elif fault == "fsGroup":
            bad["spec"]["securityContext"]["fsGroup"] = 999
        elif fault == "sidecar":
            bad["spec"]["containers"].append(copy.deepcopy(bad["spec"]["containers"][0]))
        elif fault == "pid":
            bad["spec"]["hostPID"] = True
        else:
            bad["spec"]["containers"][0]["env"] = [{"name": "TOKEN", "value": "not-public"}]
        with pytest.raises(ValueError):
            cold.own_pod(bad, expected)


def test_proxy_transport_connects_only_to_private_unix_socket(monkeypatch):
    calls = []
    handle = SimpleNamespace(
        settimeout=lambda value: calls.append(("timeout", value)),
        connect=lambda value: calls.append(("connect", value)),
    )

    def make_socket(family, kind):
        calls.append((family, kind))
        return handle

    monkeypatch.setattr(cold.socket, "socket", make_socket)
    monkeypatch.setattr(cold.socket, "AF_UNIX", 1, raising=False)
    connection = cold.UnixConnection("/tmp/yleum-cold-fixture/api.sock", 60)
    connection.connect()
    assert calls == [
        (cold.socket.AF_UNIX, cold.socket.SOCK_STREAM),
        ("timeout", 60),
        ("connect", "/tmp/yleum-cold-fixture/api.sock"),
    ]


def test_proxy_cleanup_kills_only_its_owned_group_before_wait_and_closes_resources(monkeypatch):
    calls = []
    proxy = cold.KubeProxy("kubectl", "default", "unused")
    proxy.process = SimpleNamespace(pid=1234, wait=lambda timeout: calls.append(("wait", timeout)))
    proxy.errors = SimpleNamespace(close=lambda: calls.append("fd_closed"))
    proxy.directory = SimpleNamespace(cleanup=lambda: calls.append("directory_removed"))
    monkeypatch.setattr(
        cold.os, "killpg", lambda pid, sig: calls.append(("kill", pid, sig)), raising=False
    )
    monkeypatch.setattr(cold.signal, "SIGKILL", 9, raising=False)
    proxy.__exit__(None, None, None)
    assert calls == [("kill", 1234, 9), ("wait", 5), "fd_closed", "directory_removed"]


def test_kubernetes_api_http_failure_closes_connection_without_replay(monkeypatch):
    calls = []

    class Connection:
        def __init__(self, path, timeout):
            assert str(path) == "private.sock" and timeout == 60

        def request(self, *args):
            calls.append(args)

        def getresponse(self):
            return SimpleNamespace(status=503)

        def close(self):
            calls.append("closed")

    monkeypatch.setattr(cold, "UnixConnection", Connection)
    proxy = cold.KubeProxy("kubectl", "default", "unused")
    proxy.path = "private.sock"
    with pytest.raises(ValueError, match="proof is unconfirmed"):
        proxy.request("POST", "/api/v1/namespaces/own/pods", {"own": True})
    assert len(calls) == 2 and calls[-1] == "closed"


@pytest.mark.parametrize(
    "members",
    [
        [("../outside", b"data", "file", "")],
        [("/outside", b"data", "file", "")],
        [("same", b"one", "file", ""), ("same", b"two", "file", "")],
        [("pipe", b"", "fifo", "")],
        [("link", b"", "symlink", "../outside")],
        [("link", b"", "symlink", "inside"), ("link/child", b"data", "file", "")],
        [("link/child", b"data", "file", ""), ("link", b"", "symlink", "inside")],
        [("link", b"", "hardlink", "missing")],
    ],
)
def test_unsafe_archives_fail_before_any_scratch_allocation(tmp_path, members):
    path = tmp_path / "unsafe.tar"
    path.write_bytes(tar_bytes(members))
    with pytest.raises(ValueError):
        cold.filesystem_manifest(path)


def test_file_tree_proof_covers_bytes_owner_mode_and_links(tmp_path):
    path = tmp_path / "valid.tar"
    members = [("data", b"retained", "file", ""), ("link", b"", "symlink", "data")]
    path.write_bytes(tar_bytes(members))
    original = cold.filesystem_manifest(path)
    path.write_bytes(tar_bytes([("data", b"changed", "file", ""), members[1]]))
    assert cold.filesystem_manifest(path) != original


def test_cold_coverage_requires_every_exact_pvc_and_retirement_authority(tmp_path):
    api = Kube()
    plan = cold.cold_census(api, AUTHORITY)
    summary = {key: plan[key] for key in ("namespace", "namespace_uid", "project_id", "owner_id")}
    summary["volumes"] = [
        {key: item[key] for key in ("name", "uid", "kind")} for item in plan["volumes"]
    ]
    result = cold.collect_one(
        cells, TarRunner(), spec(), AUTHORITY, tmp_path, api=api, expected_inventory=summary
    )
    manifest = {"runtime_census": {"cold_retained": [summary]}, "cold_retained": [result]}
    assert cold.coverage(manifest)["pvcs"] == 4
    for fault in ("missing_pvc", "wrong_kind", "missing_namespace", "foreign_owner"):
        bad = copy.deepcopy(manifest)
        if fault == "missing_pvc":
            bad["cold_retained"][0]["volumes"].pop()
        elif fault == "wrong_kind":
            next(row for row in bad["cold_retained"][0]["volumes"] if row["kind"] == "core")[
                "kind"
            ] = "files"
        elif fault == "missing_namespace":
            bad["cold_retained"] = []
        else:
            bad["cold_retained"][0]["authority"]["owner_id"] = uid(100)
        with pytest.raises(ValueError):
            cold.coverage(bad)


def test_cold_archive_identity_hash_and_cleanup_are_fail_closed(tmp_path):
    result = cold.collect_one(cells, TarRunner(), spec(), AUTHORITY, tmp_path, api=Kube())
    for fault in ("wrong_filename", "hash", "cleanup"):
        bad = copy.deepcopy(result)
        if fault == "wrong_filename":
            bad["volumes"][0]["file"] = bad["volumes"][1]["file"]
        elif fault == "hash":
            bad["volumes"][0]["sha256"] = "0" * 64
        else:
            bad["proof"]["cleanup_confirmed"] = False
        with pytest.raises(ValueError):
            cold.validate(cells, tmp_path, [bad])


def test_authority_is_bound_to_exact_archived_retirement_journal(tmp_path):
    payload = {
        "project_id": PROJECT,
        "owner_id": OWNER,
        "production_workspace_id": HELPER_UID,
        "disabled": True,
        "deletion_completed": True,
        "active_release": {"placement": {"backend": "kubernetes", "namespace": NAMESPACE}},
    }
    archive = tmp_path / "state.tar.gz"

    def write_archive(record):
        saved = json.dumps(record).encode()
        with tarfile.open(archive, "w:gz") as output:
            item = tarfile.TarInfo(f"state/cell-publications/{PROJECT}/publication.json")
            item.size = len(saved)
            output.addfile(item, io.BytesIO(saved))
        return cold.hashlib.sha256(saved).hexdigest()

    entry = {
        "project_id": PROJECT,
        "owner_id": OWNER,
        "namespace": NAMESPACE,
        "authority": {**AUTHORITY, "journal_sha256": write_archive(payload)},
    }
    cold.verify_authorities(archive, [entry])
    for field, invalid in (
        ("disabled", False),
        ("deletion_completed", False),
        ("project_id", uid(100)),
        ("owner_id", uid(100)),
        ("production_workspace_id", uid(100)),
        ("active_release", {"placement": {"backend": "kubernetes", "namespace": "other"}}),
    ):
        tampered = {**payload, field: invalid}
        # Even a self-consistent file hash must not hide an invalid authority.
        entry["authority"]["journal_sha256"] = write_archive(tampered)
        with pytest.raises(ValueError):
            cold.verify_authorities(archive, [entry])
    write_archive(payload)
    entry["authority"]["journal_sha256"] = "0" * 64
    with pytest.raises(ValueError):
        cold.verify_authorities(archive, [entry])


@pytest.mark.parametrize("fault", ["duplicate_active_retired", "duplicate_retired", "oversized"])
def test_retirement_archive_rejects_ambiguous_or_oversized_even_with_matching_hash(tmp_path, fault):
    retired = {
        "project_id": PROJECT,
        "owner_id": OWNER,
        "production_workspace_id": HELPER_UID,
        "disabled": True,
        "deletion_completed": True,
        "active_release": {"placement": {"backend": "kubernetes", "namespace": NAMESPACE}},
    }
    selected = json.dumps(retired).encode()
    if fault == "oversized":
        selected += b" " * (4 * 1024 * 1024)
        payloads = [selected]
    elif fault == "duplicate_active_retired":
        payloads = [json.dumps({**retired, "disabled": False}).encode(), selected]
    else:
        payloads = [selected, selected]
    archive = tmp_path / "state.tar.gz"
    with tarfile.open(archive, "w:gz") as output:
        for payload in payloads:
            member = tarfile.TarInfo(f"state/cell-publications/{PROJECT}/publication.json")
            member.size = len(payload)
            output.addfile(member, io.BytesIO(payload))
    entry = {
        "project_id": PROJECT,
        "owner_id": OWNER,
        "namespace": NAMESPACE,
        "authority": {**AUTHORITY, "journal_sha256": cold.hashlib.sha256(selected).hexdigest()},
    }
    with pytest.raises(ValueError):
        cold.verify_authorities(archive, [entry])


@pytest.mark.skipif(os.name == "nt", reason="uses canonical Linux flock")
def test_canonical_lock_is_existing_owned_inode_and_journal_is_archived(tmp_path):
    root = tmp_path / "state"
    project = root / "cell-publications" / PROJECT
    project.mkdir(parents=True)
    locks = project.parent / "locks"
    locks.mkdir(mode=0o700)
    lock = locks / f"workspace-{PROJECT}.lock"
    lock.write_bytes(b"")
    lock.chmod(0o600)
    payload = {
        **AUTHORITY,
        "production_workspace_id": HELPER_UID,
        "disabled": True,
        "deletion_completed": True,
        "active_release": {"placement": {"backend": "kubernetes", "namespace": NAMESPACE}},
    }
    journal = project / "publication.json"
    journal.write_text(json.dumps(payload))
    saved = journal.read_bytes()
    archive = tmp_path / "state.tar.gz"
    with tarfile.open(archive, "w:gz") as output:
        output.add(journal, arcname=f"state/cell-publications/{PROJECT}/publication.json")
    before = lock.stat()
    with cold.publication_locks(cells, root, archive) as records:
        assert len(records) == 1
        assert records[0]["lock_inode"] == before.st_ino
    after = lock.stat()
    assert (after.st_ino, after.st_uid, after.st_gid, after.st_mode) == (
        before.st_ino,
        before.st_uid,
        before.st_gid,
        before.st_mode,
    )
    assert journal.read_bytes() == saved
    lock.unlink()
    with pytest.raises(FileNotFoundError), cold.publication_locks(cells, root, archive):
        pass


def standalone_cells(monkeypatch):
    """Load the exact standalone script, without controller package initialization."""
    import sys

    source = SOURCE.parents[2] / "apps/orchestrator/scripts/backup_cells.py"
    name = "cold_standalone_cells_contract"
    spec = importlib.util.spec_from_file_location(name, source)
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, name, module)
    spec.loader.exec_module(module)
    return module


class ScratchVerifyRunner:
    """Docker I/O double; the real cold/cells copy, dump and restore paths run."""

    def __init__(self, module, archive, fault=None):
        self.module, self.archive, self.fault = module, archive, fault
        self.calls, self.containers, self.volumes = [], set(), set()

    def run(self, args, *, timeout, env=None, stdin_path=None):
        self.calls.append((list(args), dict(env or {})))
        assert timeout > 0
        failed = self.fault == "cleanup" and (args[0] == "rm" or args[:2] == ["volume", "rm"])
        if not failed:
            if args[:2] == ["volume", "create"]:
                self.volumes.add(args[-1])
            elif args[:2] == ["volume", "rm"]:
                self.volumes.discard(args[-1])
            elif args[0] == "run":
                self.containers.add(args[args.index("--name") + 1])
            elif args[0] == "rm":
                self.containers.discard(args[-1])
        if args[:2] == ["container", "ls"]:
            output = "\n".join(sorted(self.containers)).encode()
        elif args[:2] == ["volume", "ls"]:
            output = "\n".join(sorted(self.volumes)).encode()
        else:
            output = b"7\n" if "psql" in args else b""
        return self.module.CommandResult(
            tuple(args), int(failed), output, b"synthetic cleanup failure" if failed else b""
        )

    def run_to_file(self, args, *, path, timeout, env=None):
        self.calls.append((list(args), dict(env or {})))
        assert timeout > 0
        failed = self.fault == "dump" and "pg_dumpall" in args
        payload = (
            self.archive if "tar" in args else b"CREATE ROLE postgres;\n-- synthetic cluster dump\n"
        )
        if not failed:
            path.write_bytes(payload)
        return self.module.StreamResult(
            tuple(args),
            int(failed),
            0 if failed else len(payload),
            cells.hashlib.sha256(payload).hexdigest(),
            b"synthetic dump failure" if failed else b"",
        )

    def pipe(self, producer, consumer, *, timeout):
        self.calls.extend(((list(producer), {}), (list(consumer), {})))
        assert timeout > 0
        return (
            self.module.CommandResult(tuple(producer), 0, b"", b""),
            self.module.CommandResult(tuple(consumer), 0, b"", b""),
        )


def standalone_entry(module, tmp_path, kind):
    archive = tmp_path / cold.cold_file(NAMESPACE_UID, HELPER_UID)
    payload = tar_bytes()
    archive.write_bytes(payload)
    sha, size = module._file_digest(archive)
    entry = {
        "project_id": PROJECT,
        "owner_id": OWNER,
        "namespace_uid": NAMESPACE_UID,
        "authority": {"project_id": PROJECT, "owner_id": OWNER},
        "proof": {
            "cleanup_confirmed": True,
            "watch_start": "fixture",
            "barrier": "fixture",
            "helper_uid": HELPER_UID,
        },
        "volumes": [
            {
                "uid": HELPER_UID,
                "kind": kind,
                "file": archive.name,
                "sha256": sha,
                "bytes": size,
                **cold.filesystem_manifest(archive),
            }
        ],
    }
    return entry, payload


@pytest.mark.parametrize("kind", ["core", "project"])
def test_standalone_cold_verify_uses_only_own_scratch_transport(tmp_path, monkeypatch, kind):
    import builtins

    module = standalone_cells(monkeypatch)
    entry, archive = standalone_entry(module, tmp_path, kind)
    runner = ScratchVerifyRunner(module, archive)
    original_import = builtins.__import__
    imports = []

    def no_controller_import(name, *args, **kwargs):
        if name.startswith("yleum_orchestrator"):
            imports.append(name)
            raise ImportError("controller package absent in standalone verifier")
        return original_import(name, *args, **kwargs)

    with monkeypatch.context() as guard:
        guard.setattr(builtins, "__import__", no_controller_import)
        cold.verify(module, tmp_path, [entry], IMAGE, runner)
    assert imports == []
    assert runner.containers == runner.volumes == set()
    assert (tmp_path / entry["volumes"][0]["file"]).read_bytes() == archive
    dump = [(args, env) for args, env in runner.calls if "pg_dumpall" in args]
    assert len(dump) == 1
    command, environment = dump[0]
    assert command[:4] == ["exec", "--user", "postgres", "-e"]
    assert command[4] == "PGOPTIONS" and command[command.index("-h") + 1] == "/tmp"
    assert environment == {"PGOPTIONS": cold._SCRATCH_ADMIN_PGOPTIONS}
    assert not any("PGPASSWORD" in arg for args, _ in runner.calls for arg in args)
    assert any("local all all trust" in arg for args, _ in runner.calls for arg in args)


@pytest.mark.parametrize("fault", ["dump", "cleanup"])
def test_standalone_cold_failure_never_claims_verified(tmp_path, monkeypatch, fault):
    module = standalone_cells(monkeypatch)
    entry, archive = standalone_entry(module, tmp_path, "project")
    runner = ScratchVerifyRunner(module, archive, fault)
    with pytest.raises((ValueError, module.BackupError)):
        cold.verify(module, tmp_path, [entry], IMAGE, runner)
    if fault == "dump":
        assert runner.containers == runner.volumes == set()
    else:
        assert runner.containers or runner.volumes, "unconfirmed cleanup must not fabricate absence"


def test_scratch_admin_options_match_canonical_controller_without_imports():
    import ast

    path = (
        SOURCE.parents[2]
        / "apps/orchestrator/src/yleum_orchestrator/services/restoration_database.py"
    )
    tree = ast.parse(path.read_bytes())
    declaration = next(
        node
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "TRUSTED_ADMIN_PGOPTIONS"
            for target in node.targets
        )
    )
    expected = eval(
        compile(ast.Expression(declaration.value), "<trusted-options-expression>", "eval"),
        {"__builtins__": {}},
    )
    assert cold._SCRATCH_ADMIN_PGOPTIONS == expected


@pytest.mark.parametrize(
    "read_caps,write_caps", [(False, False), (True, False), (False, True), (True, True)]
)
def test_cold_copied_scratch_adds_FSETID_only_for_writes(
    tmp_path, monkeypatch, read_caps, write_caps
):
    module = standalone_cells(monkeypatch)
    backup = cold._scratch_backup(module, tmp_path, IMAGE, object())
    name = "omnia-backup-scratch-" + "a" * 32
    native = module.CellBackup.helper_flags(
        backup, name, read_caps=read_caps, write_caps=write_caps
    )
    actual = backup.helper_flags(name, read_caps=read_caps, write_caps=write_caps)
    assert actual == native + (["--cap-add", "FSETID"] if write_caps else [])
    assert "FSETID" not in native, "global/producer flags must stay unchanged"
    assert actual[actual.index("--cap-drop") + 1] == "ALL"
    assert actual[actual.index("--network") + 1] == "none"
    assert actual[actual.index("--security-opt") + 1] == "no-new-privileges:true"
    assert "--read-only" in actual
    assert actual[actual.index("--memory") + 1] == "256m"
    assert actual[actual.index("--pids-limit") + 1] == "64"
