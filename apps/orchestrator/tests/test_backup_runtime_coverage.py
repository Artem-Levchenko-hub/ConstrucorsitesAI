"""Full node/namespace coverage uses real validators and inert I/O doubles."""

from __future__ import annotations

import base64
import copy
import importlib.util
import io
import json
import subprocess
import sys
import tarfile
import zlib
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from uuid import NAMESPACE_URL, uuid5

import pytest
from scripts import backup_cells as cells

SOURCE = Path(__file__).resolve().parents[3] / "infra/backup/runtime-backup.py"
spec = importlib.util.spec_from_file_location("runtime_backup_contract", SOURCE)
runtime = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runtime)
PROJECT = "03fe3e35-6a48-494c-bfd6-9a94b7525dbb"
NAMESPACE = "app-" + PROJECT
IMAGE = "postgres@sha256:" + "a" * 64


class Kube:
    def __init__(self):
        tags = {
            runtime.MANAGED: "omnia-orchestrator",
            runtime.PART: "max-app",
            runtime.PROJECT: PROJECT,
        }
        self.namespaces = [
            {
                "metadata": {
                    "name": NAMESPACE,
                    "uid": "namespace-uid",
                    "labels": {**tags, "omnia.retired": "false"},
                }
            }
        ]
        self.resources = []
        self.calls = []
        self.change_after_dump = False
        self.dumps = 0
        for component in runtime.COMPONENTS:
            claim = "data-" + component + "-0"
            self.resources += [
                {
                    "kind": "PersistentVolumeClaim",
                    "metadata": {"name": claim, "uid": claim + "-uid"},
                    "status": {"phase": "Bound"},
                },
                {
                    "kind": "Pod",
                    "metadata": {
                        "name": component + "-0",
                        "uid": component + "-uid",
                        "labels": {**tags, runtime.COMPONENT: component},
                    },
                    "spec": {
                        "containers": [
                            {
                                "name": "postgres",
                                "image": IMAGE,
                                "volumeMounts": [
                                    {"name": "data", "mountPath": "/var/lib/postgresql/data"}
                                ],
                            }
                        ],
                        "volumes": [
                            {"name": "data", "persistentVolumeClaim": {"claimName": claim}}
                        ],
                    },
                    "status": {
                        "phase": "Running",
                        "containerStatuses": [
                            {
                                "name": "postgres",
                                "ready": True,
                                "containerID": "containerd://" + component,
                                "imageID": IMAGE,
                                "state": {"running": {"startedAt": "2026-10-20T00:00:00Z"}},
                            }
                        ],
                    },
                },
            ]

    def run(self, args, **_kwargs):
        self.calls.append(tuple(args))
        if "get" in args:
            items = self.namespaces if "namespaces" in args else self.resources
            items = copy.deepcopy(items)
            if self.dumps and self.change_after_dump and "namespaces" in args:
                items[0]["metadata"]["uid"] = "replacement-uid"
            return cells.CommandResult(tuple(args), 0, json.dumps({"items": items}).encode(), b"")
        return cells.CommandResult(tuple(args), 0, b"7\n", b"")

    def run_to_file(self, args, *, path, **_kwargs):
        import hashlib

        self.calls.append(tuple(args))
        self.dumps += 1
        payload = b"-- fixture cluster dump\n"
        path.write_bytes(payload)
        return cells.StreamResult(
            tuple(args), 0, len(payload), hashlib.sha256(payload).hexdigest(), b""
        )


def test_census_covers_both_published_database_clusters():
    value = runtime.kube_census(Kube(), "default", IMAGE)
    assert {row["component"] for row in value["targets"]} == set(runtime.COMPONENTS)
    expected = str(uuid5(NAMESPACE_URL, f"omnia:public-cell:{PROJECT}"))
    assert {row["workspace_id"] for row in value["targets"]} == {expected}


@pytest.mark.parametrize(
    "fault",
    [
        "missing_project",
        "wrong_image",
        "wrong_owner",
        "not_ready",
        "missing_pvc",
        "retired_with_data",
        "namespace_unknown",
        "foreign_postgres",
    ],
)
def test_incomplete_or_foreign_census_fails_closed(fault):
    kube = Kube()
    pod = next(row for row in kube.resources if row["kind"] == "Pod")
    if fault == "missing_project":
        kube.resources = [
            row for row in kube.resources if row["metadata"]["name"] != "project-postgres-0"
        ]
    elif fault == "wrong_image":
        pod["spec"]["containers"][0]["image"] = "postgres:latest"
    elif fault == "wrong_owner":
        pod["metadata"]["labels"][runtime.PROJECT] = "foreign"
    elif fault == "not_ready":
        pod["status"]["containerStatuses"][0]["ready"] = False
    elif fault == "missing_pvc":
        kube.resources = [row for row in kube.resources if row["kind"] != "PersistentVolumeClaim"]
    elif fault == "retired_with_data":
        kube.namespaces[0]["metadata"]["labels"]["omnia.retired"] = "true"
    elif fault == "namespace_unknown":
        kube.namespaces[0]["metadata"]["labels"][runtime.MANAGED] = "unknown"
    else:
        other = copy.deepcopy(pod)
        other["metadata"]["labels"][runtime.COMPONENT] = "unregistered-postgres"
        kube.resources.append(other)
    with pytest.raises(ValueError, match=r"(coverage|proof) is unconfirmed"):
        runtime.kube_census(kube, "default", IMAGE)


def test_retired_empty_namespace_is_excluded_with_evidence():
    kube = Kube()
    kube.resources = []
    kube.namespaces[0]["metadata"]["labels"]["omnia.retired"] = "true"
    value = runtime.kube_census(kube, "default", IMAGE)
    assert value["targets"] == []
    assert value["retired"] == [
        {"namespace": NAMESPACE, "uid": "namespace-uid", "reason": "retired_empty"}
    ]


def test_legacy_retired_empty_namespace_without_ownership_tags_is_proven_empty():
    kube = Kube()
    kube.resources = []
    kube.namespaces[0]["metadata"]["labels"] = {
        "kubernetes.io/metadata.name": NAMESPACE,
        "omnia.retired": "true",
    }
    assert runtime.kube_census(kube, "default", IMAGE)["retired"] == [
        {"namespace": NAMESPACE, "uid": "namespace-uid", "reason": "retired_empty"},
    ]


@pytest.mark.parametrize("fault", ["partial", "contradictory", "active", "pod", "pvc"])
def test_legacy_retirement_never_hides_partial_identity_or_data(fault):
    kube = Kube()
    old_resources = kube.resources
    kube.resources = []
    tags = {"omnia.retired": "true"}
    kube.namespaces[0]["metadata"]["labels"] = tags
    if fault == "partial":
        tags[runtime.MANAGED] = "omnia-orchestrator"
    elif fault == "contradictory":
        tags.update({runtime.MANAGED: "other", runtime.PART: "max-app", runtime.PROJECT: PROJECT})
    elif fault == "active":
        tags["omnia.retired"] = "false"
    else:
        kind = "Pod" if fault == "pod" else "PersistentVolumeClaim"
        kube.resources = [row for row in old_resources if row["kind"] == kind][:1]
    with pytest.raises(ValueError, match=r"(coverage|proof) is unconfirmed"):
        runtime.kube_census(kube, "default", IMAGE)


def test_dump_is_read_only_complete_and_rechecks_all_runtime_bindings(tmp_path, monkeypatch):
    kube = Kube()
    monkeypatch.setattr(cells, "DockerRunner", lambda _: kube)
    manifest, code = runtime.kube_backup(
        cells, {"kubectl": "kubectl", "context": "default", "postgres_image": IMAGE}, tmp_path
    )
    assert code == 0 and len(manifest["workspaces"][0]["databases"]) == 2
    cells._validate_manifest(manifest)
    assert len([call for call in kube.calls if "pg_dumpall" in call]) == 2
    assert all(
        "default_transaction_read_only=on" in " ".join(call)
        for call in kube.calls
        if "exec" in call
    )
    assert all("--lock-wait-timeout=30s" in call for call in kube.calls if "pg_dumpall" in call)
    assert not any(
        verb in call for call in kube.calls for verb in ("apply", "delete", "scale", "patch")
    )


def test_replacement_after_dump_does_not_publish_a_manifest(tmp_path, monkeypatch):
    kube = Kube()
    kube.change_after_dump = True
    monkeypatch.setattr(cells, "DockerRunner", lambda _: kube)
    with pytest.raises(ValueError):
        runtime.kube_backup(
            cells, {"kubectl": "kubectl", "context": "default", "postgres_image": IMAGE}, tmp_path
        )
    assert not (tmp_path / "MANIFEST.json").exists()


def test_peer_preflight_rejects_swapped_artifact_before_restore(tmp_path, monkeypatch):
    monkeypatch.setattr(cells, "DockerRunner", lambda _: Kube())
    manifest, code = runtime.kube_backup(
        cells,
        {"kubectl": "kubectl", "context": "default", "postgres_image": IMAGE},
        tmp_path,
    )
    assert code == 0
    cells._write_private_json(tmp_path / "MANIFEST.json", manifest)
    assert runtime.validate_local(cells, tmp_path) == manifest
    entry = manifest["workspaces"][0]["databases"][0]
    original = tmp_path / entry["file"]
    wrong = tmp_path / "11111111-1111-4111-8111-111111111111-core.dump"
    original.rename(wrong)
    entry["file"] = wrong.name  # Bytes/checksum/table count still match exactly.
    (tmp_path / "MANIFEST.json").write_text(json.dumps(manifest))
    with pytest.raises(cells.BackupError, match=r"filename.*manifest identity"):
        runtime.validate_local(cells, tmp_path)


def complete_manifests():
    workspace = str(uuid5(NAMESPACE_URL, f"omnia:public-cell:{PROJECT}"))
    external = {"workspace_id": workspace, "project_id": PROJECT, "namespace": NAMESPACE}
    return {
        "core": {"coverage": {"external_publications": []}},
        "commerce": {"coverage": {"external_publications": [external]}},
        "runtime": {
            "workspaces": [
                {
                    "workspace_id": workspace,
                    "project_id": PROJECT,
                    "databases": [{"kind": "core"}, {"kind": "project"}],
                }
            ],
            "runtime_census": {"non_database_pvcs": []},
        },
    }


def test_global_closure_requires_exact_nodes_and_journal_to_runtime_matches():
    value = runtime.close_coverage(complete_manifests())
    assert value["coverage_complete"] is True and value["published_databases"] == 2
    for fault in ("missing_peer", "missing_database", "foreign_workspace", "unpublished_namespace"):
        value = complete_manifests()
        if fault == "missing_peer":
            del value["commerce"]
        elif fault == "missing_database":
            value["runtime"]["workspaces"][0]["databases"].pop()
        elif fault == "foreign_workspace":
            value["runtime"]["workspaces"][0]["workspace_id"] = "foreign"
        else:
            value["commerce"]["coverage"]["external_publications"] = []
        with pytest.raises(ValueError):
            runtime.close_coverage(value)


@pytest.mark.parametrize("kind", ["traversal", "symlink", "duplicate"])
def test_peer_archive_extraction_cannot_escape_or_overwrite(tmp_path, kind):
    path = tmp_path / "peer.tgz"
    with tarfile.open(path, "w:gz") as archive:
        info = tarfile.TarInfo("../outside" if kind == "traversal" else "MANIFEST.json")
        if kind == "symlink":
            info.type = tarfile.SYMTYPE
            info.linkname = "../outside"
        else:
            info.size = 2
        archive.addfile(info, io.BytesIO(b"{}") if info.isfile() else None)
        if kind == "duplicate":
            archive.addfile(info, io.BytesIO(b"{}"))
    with pytest.raises(ValueError):
        runtime.unpack(path, tmp_path / "out")
    assert not (tmp_path / "outside").exists()


def test_topology_cannot_omit_a_peer_or_inject_transport_arguments(tmp_path):
    source = SOURCE.with_name("production-topology.json")
    original = json.loads(source.read_text())
    for fault in ("missing", "duplicate", "alias", "version_bool"):
        value = copy.deepcopy(original)
        if fault == "missing":
            value["nodes"].pop()
        elif fault == "duplicate":
            value["nodes"][-1] = value["nodes"][0]
        elif fault == "alias":
            value["nodes"][-1]["ssh_alias"] = "runtime; unsafe"
        else:
            value["version"] = True
        path = tmp_path / "topology.json"
        path.write_text(json.dumps(value))
        with pytest.raises(ValueError):
            runtime.topology(path)


def test_peer_source_wire_is_exact_compressed_and_preserves_child_exit():
    payload = {
        "sources": {
            "cells": "from dataclasses import dataclass\n@dataclass\nclass Item:\n    x: int\n",
            "runtime": (
                "def dispatch_peer(c,p):\n    assert c.Item(7).x == 7\n"
                "    assert p['spec']['quoted'] == \"spaces 'quotes' Ядро\"\n    return 75\n"
            ),
        },
        "spec": {"quoted": "spaces 'quotes' Ядро"},
    }
    wire = runtime.encode_peer(payload)
    assert json.loads(zlib.decompress(base64.b64decode(wire))) == payload
    result = subprocess.run(
        [sys.executable, "-c", runtime.peer_program(), wire], capture_output=True, timeout=10
    )
    assert result.returncode == 75 and result.stdout == b"" and result.stderr == b""


def platform_census():
    return {
        "version": 1,
        "scope": "complete_platform_project_workspace_census",
        "observed_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "projects": [],
        "workspaces": [],
    }


def test_docker_peer_exports_retired_workspace_without_local_storage(tmp_path, monkeypatch):
    import signal

    workspace_id = "11111111-1111-4111-8111-111111111111"
    operation_id = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    stem = "omnia-cell-" + workspace_id.replace("-", "")
    state = tmp_path / "state"
    journal = state / "project-cells" / f"{workspace_id}.json"
    journal.parent.mkdir(parents=True)
    journal.write_text(json.dumps({"version": 1, "workspace": {
        "workspace_id": workspace_id, "project_id": PROJECT, "owner_id": PROJECT,
        "phase": "completed", "bundle_state": "retained", "fencing_epoch": 4,
        "last_operation_id": operation_id,
        "resource_names": {
            "workspace_id": workspace_id, "namespace": "prod",
            **{field: f"{stem}-{suffix}" for field, suffix in {
                "internal_network": "internal", "egress_network": "egress",
                "workspace_volume": "workspace", "agent_home_volume": "agent-home",
                "postgres_volume": "postgres", "redis_volume": "redis",
                "checkpoint_volume": "checkpoints", "postgres_container": "postgres",
                "redis_container": "redis",
            }.items()},
        },
        "operations": [{"operation_id": operation_id, "kind": "destroy",
                        "status": "completed", "phase": "completed",
                        "fencing_epoch": 4, "generation_run_id": None}],
    }}))
    original = journal.read_bytes()

    class EmptyDocker:
        def run(self, args, **_kwargs):
            assert args in (["volume", "ls", "--format", "{{.Name}}"],
                            ["ps", "--all", "--quiet", "--no-trunc"])
            return cells.CommandResult(tuple(args), 0, b"", b"")

    output = SimpleNamespace(buffer=io.BytesIO())
    monkeypatch.setattr(signal, "SIGALRM", 14, raising=False)
    monkeypatch.setattr(signal, "signal", lambda *_: None)
    monkeypatch.setattr(signal, "alarm", lambda *_: None, raising=False)
    monkeypatch.setattr(cells, "DockerRunner", EmptyDocker)
    monkeypatch.setattr(runtime.sys, "stdout", output)
    monkeypatch.setenv("BACKUP_PLATFORM_CENSUS_JSON", "invalid ambient census")
    census = platform_census()
    code = runtime.export_peer(cells, {
        "provider": "docker", "name": "commerce", "temp_root": str(tmp_path / "stage"),
        "state_root": str(state), "postgres_image": IMAGE, "helper_image": IMAGE,
        "platform_census": census, "producer_sha256": {},
    })
    with tarfile.open(fileobj=io.BytesIO(output.buffer.getvalue()), mode="r:gz") as archive:
        manifest = json.load(archive.extractfile("MANIFEST.json"))
    assert code == 0
    assert manifest["coverage"]["issues"] == []
    assert manifest["coverage"]["platform_census"] == census
    receipts = manifest["coverage"]["retired_no_local_storage"]
    assert [(row["workspace_id"], row["reason"]) for row in receipts] == [
        (workspace_id, "retired_no_local_storage")
    ]
    assert manifest["workspaces"] == []
    assert journal.read_bytes() == original
    assert list((tmp_path / "stage").iterdir()) == []
    assert runtime.os.environ["BACKUP_PLATFORM_CENSUS_JSON"] == "invalid ambient census"


def test_peer_payload_binds_all_three_sources_and_only_shared_platform_census(
    tmp_path, monkeypatch
):
    import shlex

    captured = []

    class Transport:
        def run_to_file(self, argv, **_):
            captured.append(argv)
            return cells.StreamResult(tuple(argv), 75, 0, "", b"denied")

    monkeypatch.setattr(cells, "DockerRunner", lambda _: Transport())
    args = SimpleNamespace(
        cells_script=SOURCE.parents[2] / "apps/orchestrator/scripts/backup_cells.py",
        out=tmp_path,
        peer_temp_root="/private/new-stage",
        postgres_image=IMAGE,
        helper_image=IMAGE,
    )
    nodes = json.loads(SOURCE.with_name("production-topology.json").read_text())["nodes"]
    census = cells.parse_platform_census(json.dumps(platform_census()))
    with pytest.raises(ValueError):
        runtime._collect_peers(cells, args, nodes, {"core": {}}, [], census)
    assert len(captured) == 1
    wire = shlex.split(captured[0][-1])[-1]
    payload = json.loads(zlib.decompress(base64.b64decode(wire)))
    assert payload["spec"]["platform_census"] == census
    assert "docker_observation" not in payload["spec"]
    assert set(payload["sources"]) == {"cells", "runtime", "cold"}
    assert payload["spec"]["producer_sha256"] == {
        name: runtime.hashlib.sha256(value.encode()).hexdigest()
        for name, value in payload["sources"].items()
    }
    assert len(wire) < 100000


def test_controller_child_gets_exact_census_env_and_no_automatic_replay(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setitem(
        sys.modules,
        "pwd",
        SimpleNamespace(getpwnam=lambda name: SimpleNamespace(pw_uid=1000, pw_gid=1000)),
    )
    monkeypatch.setattr(runtime.os, "chown", lambda *args: None, raising=False)

    def launch(argv, **kwargs):
        calls.append((argv, kwargs))
        return SimpleNamespace(returncode=75)

    monkeypatch.setattr(runtime.subprocess, "run", launch)
    census = cells.parse_platform_census(json.dumps(platform_census()))
    before = dict(runtime.os.environ)
    result = runtime.dispatch_peer(
        cells,
        {
            "sources": {"cells": "", "runtime": "", "cold": ""},
            "spec": {"provider": "docker", "temp_root": str(tmp_path), "platform_census": census},
        },
    )
    assert result == 75 and len(calls) == 1
    environment = calls[0][1]["env"]
    assert environment == {**before, "BACKUP_PLATFORM_CENSUS_JSON": json.dumps(census)}
    assert dict(runtime.os.environ) == before
    assert list(tmp_path.iterdir()) == []
