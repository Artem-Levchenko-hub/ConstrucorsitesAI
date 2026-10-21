"""Full node/namespace coverage uses real validators and inert I/O doubles."""

from __future__ import annotations

import base64
import copy
import importlib.util
import io
import json
import os
import signal
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
        self.boundary_source = b"print('boundary fixture')\n"
        self.configmap = {"metadata": {"name": "boundary-server", "uid": "cm-uid",
                          "resourceVersion": "1",
                          "labels": {**tags, runtime.COMPONENT: "boundary"}},
                          "data": {"server.py": self.boundary_source.decode()}}
        self.cri = {}
        for component in ("app", "core", "boundary", "redis"):
            config_id = "sha256:" + runtime.digest(component)
            resolved = f"registry/{component}@sha256:" + runtime.digest("manifest-" + component)
            self.cri[resolved] = {
                "status": {"id": config_id, "repoDigests": [resolved]},
                "info": {"imageSpec": {"os": "linux", "architecture": "amd64",
                                       "config": {"Env": ["PRIVATE=never-persist"]}}},
            }
            self.resources.append({
                "kind": "Pod",
                "metadata": {"name": component + "-pod", "uid": component + "-pod-uid",
                             "labels": {**tags, runtime.COMPONENT: component}},
                "spec": {"containers": [
                    {"name": component, "image": f"registry/{component}:current"},
                ]},
                "status": {"phase": "Running", "containerStatuses": [
                    {"name": component, "ready": True, "containerID": "containerd://" + component,
                     "imageID": resolved, "state": {"running": {}}},
                ]},
            })

        boundary = next(pod for pod in self.resources
                        if pod.get("kind") == "Pod" and pod["metadata"]["name"] == "boundary-pod")
        boundary["spec"]["containers"][0].update(
            command=["python3", "/run/omnia-boundary/server.py"],
            volumeMounts=[{"name": "boundary", "mountPath": "/run/omnia-boundary",
                           "readOnly": True}])
        boundary["spec"]["volumes"] = [{"name": "boundary", "projected": {"sources": [
            {"configMap": {"name": "boundary-server",
                           "items": [{"key": "server.py", "path": "server.py"}]}}
        ]}}]

    def run(self, args, **_kwargs):
        self.calls.append(tuple(args))
        if args[:2] == ["crictl", "inspecti"]:
            return cells.CommandResult(tuple(args), 0, json.dumps(self.cri[args[-1]]).encode(), b"")
        if "get" in args:
            items = ([self.configmap] if "configmaps" in args else
                     self.namespaces if "namespaces" in args else self.resources)
            items = copy.deepcopy(items)
            if self.dumps and self.change_after_dump and "namespaces" in args:
                items[0]["metadata"]["uid"] = "replacement-uid"
            return cells.CommandResult(tuple(args), 0, json.dumps({"items": items}).encode(), b"")
        return cells.CommandResult(tuple(args), 0, b"7\n", b"")

    def run_to_file(self, args, *, path, **_kwargs):
        import hashlib

        self.calls.append(tuple(args))
        is_source = args[-2:] == ["cat", "/run/omnia-boundary/server.py"]
        if not is_source:
            self.dumps += 1
        payload = self.boundary_source if is_source else b"-- fixture cluster dump\n"
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
        if "exec" in call and ("pg_dumpall" in call or "psql" in call)
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


def test_runtime_image_facts_bind_cri_config_and_manifest_without_env():
    kube = Kube()
    facts = runtime.kube_runtime_images(kube, kube, "default")
    assert {row["component"] for row in facts} == {"app", "core", "guard", "redis"}
    assert all(row["pod_uid"] and row["config_id"] != row["runtime_image_id"] for row in facts)
    assert "PRIVATE" not in json.dumps(facts)
    assert sum(call[:2] == ("crictl", "inspecti") for call in kube.calls) == 4
    assert not any(
        verb in call for call in kube.calls for verb in ("pull", "create", "run", "apply")
    )


def test_cri_redis_alias_is_bound_and_distinct_image_lookup_is_deduplicated():
    kube = Kube()
    redis = next(row for row in kube.resources
                 if row.get("metadata", {}).get("name") == "redis-pod")
    state = redis["status"]["containerStatuses"][0]
    suffix = "@sha256:" + "e" * 64
    proof = kube.cri.pop(state["imageID"])
    state["imageID"] = "docker.io/library/redis" + suffix
    redis["spec"]["containers"][0]["image"] = "redis" + suffix
    proof["status"]["repoDigests"] = ["redis" + suffix]
    proof["info"]["imageSpec"]["variant"] = None
    kube.cri[state["imageID"]] = proof
    replica = copy.deepcopy(next(row for row in kube.resources
                                 if row.get("metadata", {}).get("name") == "app-pod"))
    replica["metadata"].update(name="app-replica", uid="app-replica-uid")
    kube.resources.append(replica)
    facts = runtime.kube_runtime_images(kube, kube, "default")
    assert len(facts) == 5
    assert sum(call[:2] == ("crictl", "inspecti") for call in kube.calls) == 4
    redis_fact = next(row for row in facts if row["component"] == "redis")
    assert redis_fact["repo_digests"] == ["docker.io/library/redis" + suffix]
    assert redis_fact["platform"]["variant"] == ""


def test_live_image_drift_during_pg_dump_refuses_completion(tmp_path, monkeypatch):
    kube = Kube()
    dump = kube.run_to_file

    def changed(*args, **kwargs):
        result = dump(*args, **kwargs)
        app = next(row for row in kube.resources
                   if row.get("metadata", {}).get("name") == "app-pod")
        app["spec"]["containers"][0]["image"] = "registry/app:changed"
        return result

    monkeypatch.setattr(kube, "run_to_file", changed)
    monkeypatch.setattr(cells, "DockerRunner", lambda *args: kube)
    with pytest.raises(ValueError):
        runtime.kube_backup(cells, {"kubectl": "kubectl", "context": "default",
                                    "postgres_image": IMAGE}, tmp_path)
    assert not (tmp_path / "MANIFEST.json").exists()


@pytest.mark.parametrize("fault", ["cri_digest", "missing_role", "not_ready", "foreign_owner"])
def test_runtime_image_facts_fail_closed(fault):
    kube = Kube()
    pod = next(row for row in kube.resources if row.get("metadata", {}).get("name") == "app-pod")
    if fault == "cri_digest":
        kube.cri[pod["status"]["containerStatuses"][0]["imageID"]]["status"]["repoDigests"] = []
    elif fault == "missing_role":
        kube.resources.remove(pod)
    elif fault == "not_ready":
        pod["status"]["containerStatuses"][0]["ready"] = False
    else:
        pod["metadata"]["labels"][runtime.PROJECT] = "foreign"
    with pytest.raises(ValueError):
        runtime.kube_runtime_images(kube, kube, "default")


def test_new_coverage_refuses_missing_published_sidecar_but_legacy_is_honest():
    manifests = complete_manifests()
    assert runtime.close_coverage(manifests)["full_dr_verified"] is False
    with pytest.raises(ValueError):
        runtime.close_coverage(manifests, require_published=True)


def complete_v2_manifests():
    manifests = complete_manifests()
    facts = runtime.kube_runtime_images(Kube(), Kube(), "default")
    images = runtime.validate_runtime_images(facts)
    workspace = manifests["runtime"]["workspaces"][0]["workspace_id"]
    for value in manifests.values():
        value["recovery_coverage_version"] = 2
    for node in ("core", "commerce"):
        manifests[node]["published_artifacts"] = {
            "file": "PUBLISHED.json", "bytes": 100, "sha256": "a" * 64,
            "blob_count": 0, "blob_bytes": 0, "publications": [],
        }
    manifests["runtime"]["runtime_images"] = facts
    manifests["runtime"]["boundary_sources"] = runtime.boundary_snapshot(Kube(), "default", facts)
    manifests["commerce"]["published_artifacts"]["publications"] = [{
        "project_id": PROJECT, "release_id": PROJECT, "images": images[PROJECT],
        "durable_pvc_gaps": ["/mutable-business"],
        "databases": [{"project_id": PROJECT, "workspace_id": workspace, "kind": kind,
                       "source": "existing-runtime-backup"} for kind in ("project", "core")],
    }]
    return manifests


@pytest.mark.parametrize("fault", [None, "missing_closure", "wrong_config"])
def test_v2_coverage_requires_exact_artifact_runtime_image_and_db_closure(fault):
    manifests = complete_v2_manifests()
    if fault == "missing_closure":
        manifests["commerce"]["published_artifacts"]["publications"] = []
    elif fault == "wrong_config":
        record = manifests["commerce"]["published_artifacts"]["publications"][0]
        record["images"]["app"]["config_id"] = "sha256:" + "f" * 64
    if fault:
        with pytest.raises(ValueError):
            runtime.close_coverage(manifests, require_published=True)
    else:
        result = runtime.close_coverage(manifests, require_published=True)
        assert result["version"] == 2 and result["full_dr_verified"] is False
        assert result["durable_pvc_gaps"] == {PROJECT: ["/mutable-business"]}


@pytest.mark.parametrize("drift", [False, True])
def test_collect_runtime_first_then_sidecars_final_images_only_probe(tmp_path, monkeypatch, drift):
    complete = complete_v2_manifests()
    manifests = {"core": copy.deepcopy(complete["core"])}
    manifests["core"].pop("published_artifacts")
    manifests["core"].pop("recovery_coverage_version")
    core = tmp_path / "core"
    core.mkdir()
    (core / "MANIFEST.json").write_text(json.dumps(manifests["core"]))
    nodes = json.loads(SOURCE.with_name("production-topology.json").read_text())["nodes"]
    order = []

    def fetch(cells_arg, out, sources, spec):
        assert set(sources) == {"cells", "cold", "runtime", "published"}
        order.append(spec["name"])
        if spec["name"] == "commerce":
            assert spec["runtime_images"] == complete["runtime"]["runtime_images"]
            assert "published_artifacts" in json.loads((core / "MANIFEST.json").read_text())
        return copy.deepcopy(complete[spec["name"]])

    def supplement(cells_arg, manifest, state, directory, images):
        order.append("core")
        assert images == complete["runtime"]["runtime_images"]
        manifest.update(copy.deepcopy(complete["core"]))

    def final_probe(cells_arg, sources, spec):
        order.append("images-only")
        result = copy.deepcopy(complete["runtime"]["runtime_images"])
        if drift:
            result[0]["pod_uid"] = "replaced-pod"
        return result

    monkeypatch.setattr(runtime, "_fetch_peer", fetch)
    monkeypatch.setattr(runtime, "add_published", supplement)
    monkeypatch.setattr(runtime, "_fetch_runtime_images", final_probe)
    monkeypatch.setattr(runtime.cold_module(), "verify_authorities", lambda *args: None)
    args = SimpleNamespace(
        cells_script=SOURCE.parents[2] / "apps/orchestrator/scripts/backup_cells.py",
        out=tmp_path, core_cells=core, topology=SOURCE.with_name("production-topology.json"),
        peer_temp_root="/private/stage", postgres_image=IMAGE, helper_image=IMAGE,
    )
    if drift:
        with pytest.raises(ValueError):
            runtime._collect_peers(cells, args, nodes, manifests, [], platform_census())
        assert not (tmp_path / "COVERAGE.json").exists()
    else:
        runtime._collect_peers(cells, args, nodes, manifests, [], platform_census())
        assert json.loads((tmp_path / "COVERAGE.json").read_text())["version"] == 2
    assert order == ["runtime", "core", "commerce", "images-only"]


def test_images_only_peer_never_creates_backup_or_dumps(tmp_path, monkeypatch, capsys):
    kube = Kube()
    monkeypatch.setattr(cells, "DockerRunner", lambda *args: kube)
    monkeypatch.setattr(runtime, "export_peer", lambda *args: pytest.fail("unexpected PG export"))
    result = runtime.dispatch_peer(cells, {"spec": {
        "mode": "images-only", "provider": "kubernetes", "kubectl": "kubectl",
        "context": "default", "producer_sha256": {"published": "a" * 64},
        "temp_root": str(tmp_path / "not-created"),
    }})
    response = json.loads(capsys.readouterr().out)
    assert result == 0 and len(response["runtime_images"]) == 4
    assert kube.dumps == 0 and not (tmp_path / "not-created").exists()


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
    assert manifest["recovery_coverage_version"] == 2
    peer_tar = tmp_path / "saved.tgz"
    peer_tar.write_bytes(output.buffer.getvalue())
    extracted = tmp_path / "verified-peer"
    runtime.unpack(peer_tar, extracted)
    assert runtime.validate_local(cells, extracted) == manifest
    (extracted / "PUBLISHED.json").unlink()
    with pytest.raises(runtime.published_module().PublishedError):
        runtime.validate_local(cells, extracted)


def test_peer_payload_binds_all_four_sources_and_only_shared_platform_census(
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
    assert set(payload["sources"]) == {"cells", "runtime", "cold", "published"}
    assert payload["spec"]["name"] == "runtime", "runtime image facts must precede Docker peers"
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


@pytest.mark.parametrize("project_exists", [False, True])
def test_owner_peer_export_passes_census_into_real_backup_and_archive(
    tmp_path, monkeypatch, project_exists
):
    from tests.test_backup_cells import (
        GENERATING,
        RETIRED_PROJECT,
        FakeRunner,
        _terminal_destroy,
    )

    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    journal = _terminal_destroy(state, GENERATING)
    original = journal.read_bytes()
    census = platform_census()
    if project_exists:
        census["projects"] = [RETIRED_PROJECT]
    ambient = {**platform_census(), "projects": [PROJECT]}
    monkeypatch.setenv("BACKUP_PLATFORM_CENSUS_JSON", json.dumps(ambient))
    monkeypatch.setattr(cells, "DockerRunner", FakeRunner)
    # The peer process normally exits after export. Keep only its POSIX process
    # deadline inert for this in-process, portable test; actual backup is real.
    monkeypatch.setattr(signal, "SIGALRM", 14, raising=False)
    monkeypatch.setattr(signal, "signal", lambda *_: None)
    monkeypatch.setattr(signal, "alarm", lambda *_: None, raising=False)
    wire = io.BytesIO()
    stdout = io.TextIOWrapper(wire, encoding="utf-8")
    monkeypatch.setattr(sys, "stdout", stdout)
    peer_root = tmp_path / "peer"
    result = runtime.dispatch_peer(
        cells,
        {
            "spec": {
                "provider": "docker",
                "as_owner": True,
                "name": "commerce",
                "temp_root": str(peer_root),
                "state_root": str(state),
                "postgres_image": IMAGE,
                "helper_image": IMAGE,
                "platform_census": census,
                "producer_sha256": {"cells": "a" * 64, "runtime": "b" * 64, "cold": "c" * 64},
            }
        },
    )
    assert result == (2 if project_exists else 0)
    archive_path = tmp_path / "peer.tar.gz"
    archive_path.write_bytes(wire.getvalue())
    extracted = tmp_path / "extracted"
    runtime.unpack(archive_path, extracted)
    manifest = runtime.read(extracted / "MANIFEST.json")
    assert manifest["coverage"]["platform_census"] == census
    assert manifest["node"] == "commerce"
    assert journal.read_bytes() == original
    if project_exists:
        assert manifest["coverage"]["retired_no_local_storage"] == []
        assert any(
            row["reason"] == "declared_core_volume_missing"
            for row in manifest["coverage"]["issues"]
        )
        assert len(list(peer_root.iterdir())) == 1, "failed peer evidence must remain private"
    else:
        assert manifest["coverage"]["issues"] == []
        assert [
            row["workspace_id"] for row in manifest["coverage"]["retired_no_local_storage"]
        ] == [GENERATING]
        assert list(peer_root.iterdir()) == [], "only the successful own stage is cleaned"
        _, verified = cells.run_verify(
            backup_dir=extracted, postgres_image=IMAGE, runner=FakeRunner()
        )
        assert verified == 0
    assert os.environ["BACKUP_PLATFORM_CENSUS_JSON"] == json.dumps(ambient)


@pytest.mark.parametrize("fault", ["missing", "wrong-mount", "mounted-mismatch", "cm-drift"])
def test_boundary_source_collection_fails_closed(tmp_path, monkeypatch, fault):
    kube = Kube()
    if fault == "missing":
        kube.configmap["data"] = {}
    elif fault == "wrong-mount":
        pod = next(item for item in kube.resources if item.get("kind") == "Pod"
                   and item["metadata"]["name"] == "boundary-pod")
        pod["spec"]["containers"][0]["volumeMounts"][0]["readOnly"] = False
    elif fault == "mounted-mismatch":
        kube.boundary_source += b"changed"
    else:
        original = kube.run_to_file
        def drift(*args, **kwargs):
            result = original(*args, **kwargs)
            kube.configmap["metadata"]["resourceVersion"] = "2"
            return result
        kube.run_to_file = drift
    monkeypatch.setattr(cells, "DockerRunner", lambda *_: kube)
    with pytest.raises(ValueError):
        runtime.kube_backup(cells, {"kubectl": "kubectl", "context": "default",
                                   "postgres_image": IMAGE}, tmp_path)


def test_boundary_source_offline_corruption_and_missing_fail(tmp_path):
    kube = Kube()
    facts = runtime.kube_runtime_images(kube, kube, "default")
    rows = runtime.boundary_snapshot(kube, "default", facts)
    path = tmp_path / rows[0]["file"]
    path.write_bytes(kube.boundary_source)
    runtime.validate_boundary_sources(rows, facts, tmp_path)
    path.write_bytes(b"corrupt")
    with pytest.raises(ValueError):
        runtime.validate_boundary_sources(rows, facts, tmp_path)
    with pytest.raises(ValueError):
        runtime.validate_boundary_sources([], facts)


def _add_service_account_mount(kube):
    pod = next(item for item in kube.resources if item.get("kind") == "Pod"
               and item["metadata"]["name"] == "boundary-pod")
    mount = {"name": "kube-api-access-t6b6n", "readOnly": True,
             "mountPath": "/var/run/secrets/kubernetes.io/serviceaccount"}
    pod["spec"]["containers"][0]["volumeMounts"].append(mount)
    volume = {"name": mount["name"], "projected": {"defaultMode": 420, "sources": [
        {"serviceAccountToken": {"expirationSeconds": 3607, "path": "token"}},
        {"configMap": {"name": "kube-root-ca.crt",
                       "items": [{"key": "ca.crt", "path": "ca.crt"}]}},
        {"downwardAPI": {"items": [{"fieldRef": {"apiVersion": "v1",
            "fieldPath": "metadata.namespace"}, "path": "namespace"}]}},
    ]}}
    pod["spec"]["volumes"].append(volume)
    return pod, mount, volume


def test_boundary_source_accepts_standard_kubernetes_service_account_mount(tmp_path, monkeypatch):
    kube = Kube()
    facts = runtime.kube_runtime_images(kube, kube, "default")
    expected = runtime.boundary_snapshot(kube, "default", facts)
    _add_service_account_mount(kube)
    assert runtime.boundary_snapshot(kube, "default", facts) == expected
    monkeypatch.setattr(cells, "DockerRunner", lambda *_: kube)
    manifest, code = runtime.kube_backup(
        cells, {"kubectl": "kubectl", "context": "default", "postgres_image": IMAGE}, tmp_path
    )
    assert code == 0
    assert manifest["boundary_sources"] == expected
    assert "serviceAccountToken" not in json.dumps(manifest)
    runtime.validate_boundary_sources(expected, facts, tmp_path)


@pytest.mark.parametrize("fault", [
    "unknown-mount", "overlap", "writable", "subpath", "duplicate-source",
    "foreign-volume", "unexpected-projection", "boundary-writable",
])
def test_boundary_source_keeps_mount_guards_with_service_account(fault):
    kube = Kube()
    pod, mount, volume = _add_service_account_mount(kube)
    if fault == "unknown-mount":
        mount["name"] = "arbitrary-extra-volume"
    elif fault == "overlap":
        mount["mountPath"] = "/run/omnia-boundary/server.py"
    elif fault == "writable":
        mount["readOnly"] = False
    elif fault == "subpath":
        mount["subPath"] = "token"
    elif fault == "duplicate-source":
        pod["spec"]["containers"][0]["volumeMounts"].append(
            copy.deepcopy(pod["spec"]["containers"][0]["volumeMounts"][0]))
    elif fault == "foreign-volume":
        volume.pop("projected")
        volume["hostPath"] = {"path": "/run/omnia-boundary"}
    elif fault == "unexpected-projection":
        volume["projected"]["sources"].append({"secret": {"name": "foreign"}})
    else:
        pod["spec"]["containers"][0]["volumeMounts"][0]["readOnly"] = False
    facts = runtime.kube_runtime_images(kube, kube, "default")
    with pytest.raises(ValueError):
        runtime.boundary_snapshot(kube, "default", facts)


@pytest.mark.parametrize("fault", ["cm", "pod"])
def test_final_images_probe_rejects_boundary_drift(monkeypatch, fault):
    kube = Kube()
    facts = runtime.kube_runtime_images(kube, kube, "default")
    expected = runtime.boundary_snapshot(kube, "default", facts)
    if fault == "cm":
        kube.configmap["metadata"]["resourceVersion"] = "2"
    else:
        pod = next(item for item in kube.resources if item.get("kind") == "Pod"
                   and item["metadata"]["name"] == "boundary-pod")
        pod["metadata"]["uid"] = "replacement"
    monkeypatch.setattr(cells, "DockerRunner", lambda *_: kube)
    with pytest.raises(ValueError):
        runtime.dispatch_peer(cells, {"spec": {
            "mode": "images-only", "provider": "kubernetes", "kubectl": "kubectl",
            "context": "default", "producer_sha256": {},
            "expected_boundary_sources": expected}})
