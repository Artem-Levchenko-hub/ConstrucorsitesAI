#!/usr/bin/env python3
"""Root-only multi-host backup; live databases are never restore targets.

Peer programs are the exact installed source bytes, sent through SSH. A peer
only reads live data and writes a fresh private temporary backup directory.
Cold retained PVCs use strictly owned transient read-only helper Pods.
Restore checks always run in the existing own Docker scratch pool on Core.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import importlib.util
import json
import os
import re
import shlex
import subprocess
import sys
import tarfile
import tempfile
import zlib
from contextlib import redirect_stdout
from pathlib import Path
from uuid import NAMESPACE_URL, UUID, uuid5

NODES = {"core": "docker", "commerce": "docker", "runtime": "kubernetes"}
COMPONENTS = ("core-postgres", "project-postgres")
PART = "app.kubernetes.io/part-of"
MANAGED = "app.kubernetes.io/managed-by"
COMPONENT = "app.kubernetes.io/component"
PROJECT = "omnia.project-id"
SOURCE_LAYOUTS = {
    "guard": {"component": "boundary", "configmap": "boundary-server", "key": "server.py",
              "path": "/run/omnia-boundary/server.py", "suffix": "-boundary-server.py"},
    "analytics-relay": {"component": "analytics-relay", "configmap": "analytics-relay", "key": "relay.py",
                        "path": "/run/relay/relay.py", "suffix": "-analytics-relay.py"},
}
PGOPTIONS = " ".join(
    "-c " + setting
    for setting in (
        "search_path=public",
        "log_statement=none",
        "log_duration=off",
        "log_min_duration_statement=-1",
        "log_min_duration_sample=-1",
        "log_transaction_sample_rate=0",
        "log_min_error_statement=panic",
        "session_preload_libraries=",
        "local_preload_libraries=",
        "default_transaction_read_only=on",
    )
)
_PEER_ERROR_MAX_BYTES = 1024
_PEER_ERROR_FIELDS = {"version", "node", "phase", "error_class", "guard_code", "exit_code"}
_PEER_PHASES = {
    "peer_dispatch", "peer_prepare", "database_backup", "published_artifacts",
    "manifest_write", "archive_stream",
}
_PEER_EXCEPTION_CLASSES = {
    "Exception", "ValueError", "TypeError", "KeyError", "OSError", "PermissionError",
    "FileNotFoundError", "TimeoutError", "BackupError", "PublishedError", "TarError",
    "ReadError", "WriteError", "StreamError", "CalledProcessError", "TimeoutExpired",
    "JSONDecodeError",
}
_PEER_GUARD_CLASSES = {
    "peer_exception": _PEER_EXCEPTION_CLASSES,
    "coverage_unconfirmed": {"ValueError"},
    "backup_exit_nonzero": {"NonzeroExit"},
}


class PeerCommandError(ValueError):
    """Only fixed, validated diagnostic fields cross the peer command boundary."""

    def __init__(self, diagnostic: dict) -> None:
        super().__init__("peer command failed")
        self.diagnostic = diagnostic


def _peer_error_record(node, phase, error=None, *, exit_code=1):
    error_class, guard_code = "NonzeroExit", "backup_exit_nonzero"
    if error is not None:
        name = type(error).__name__
        error_class = name if name in _PEER_EXCEPTION_CLASSES else "Exception"
        guard_code = (
            "coverage_unconfirmed"
            if (
                type(error) is ValueError
                and error.args == ("backup runtime coverage is unconfirmed",)
            )
            else "peer_exception"
        )
    return {
        "version": 1, "node": node if type(node) is str and node in NODES else "unknown",
        "phase": phase, "error_class": error_class, "guard_code": guard_code,
        "exit_code": exit_code,
    }


def _emit_peer_error(record):
    # A preceding command may have left stderr without a final newline.
    # No argv, exception text, stderr, environment or payload is serialized.
    print("\n" + json.dumps(record, separators=(",", ":")), file=sys.stderr, flush=True)


def _unique_peer_fields(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate peer diagnostic field")
        value[key] = item
    return value


def _peer_failure(result, node):
    fallback = {"node": node, "guard_code": "peer_command_failed", "exit_code": result.exit_code}
    # Read only the final bounded line. Earlier stderr is untrusted and can
    # include SQL data, credentials or arbitrary SSH/sudo diagnostics.
    line = result.stderr.removesuffix(b"\n").rsplit(b"\n", 1)[-1]
    if not 0 < len(line) <= _PEER_ERROR_MAX_BYTES:
        return PeerCommandError(fallback)
    try:
        value = json.loads(line.decode("utf-8"), object_pairs_hook=_unique_peer_fields)
        if (
            type(value) is dict and set(value) == _PEER_ERROR_FIELDS
            and type(value["version"]) is int and value["version"] == 1
            and type(value["node"]) is str and value["node"] == node
            and type(value["phase"]) is str and value["phase"] in _PEER_PHASES
            and type(value["guard_code"]) is str and value["guard_code"] in _PEER_GUARD_CLASSES
            and type(value["error_class"]) is str
            and value["error_class"] in _PEER_GUARD_CLASSES[value["guard_code"]]
            and type(value["exit_code"]) is int and 1 <= value["exit_code"] <= 255
            and value["exit_code"] == result.exit_code
        ):
            return PeerCommandError(value)
    except (ValueError, UnicodeError):
        pass
    return PeerCommandError(fallback)


def need(value: object) -> None:
    if not value:
        raise ValueError("backup runtime coverage is unconfirmed")


def digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def read(path: Path) -> dict:
    need(path.is_file() and not path.is_symlink())
    value = json.loads(path.read_text())
    need(type(value) is dict)
    return value


def topology(path: Path) -> list[dict]:
    value = read(path)
    need(
        type(value.get("version")) is int
        and value["version"] == 1
        and type(value.get("nodes")) is list
    )
    nodes = value["nodes"]
    need(len(nodes) == 3 and {item.get("name") for item in nodes} == set(NODES))
    for node in nodes:
        name = node["name"]
        need(node.get("provider") == NODES[name])
        if name != "core":
            need(
                type(node.get("ssh_alias")) is str
                and re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9.-]{0,63}", node["ssh_alias"])
            )
        if node["provider"] == "docker":
            need(node.get("state_root") == "/opt/omnia-runtime/state")
        else:
            need(
                node.get("kubectl") == "/usr/local/bin/kubectl"
                and node.get("context") == "default"
            )
    return nodes


def kube_json(runner, context: str, *arguments: str) -> dict:
    result = runner.run(["--context", context, *arguments, "-o", "json"], timeout=60)
    need(result.ok)
    value = json.loads(result.stdout)
    need(type(value) is dict and type(value.get("items")) is list)
    return value


def cold_module():
    name = "backup_cold_peer"
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(
            name, Path(__file__).with_name("cold-retained.py")
        )
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


def published_module():
    name = "backup_published_peer"
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(
            name, Path(__file__).with_name("published-artifacts.py")
        )
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


def validate_runtime_images(facts: list[dict]) -> dict:
    """Validate saved, filtered CRI evidence; never equate config/manifest hashes."""
    need(type(facts) is list)
    seen, projects = set(), set()
    for fact in facts:
        project = str(UUID(fact["project_id"]))
        need(project == fact["project_id"] and fact["namespace"] == "app-" + project)
        need(fact["component"] in {"app", "core", "guard", "redis", "analytics-relay"})
        need(all(type(fact.get(key)) is str and fact[key] for key in
                 ("namespace_uid", "pod", "pod_uid", "container", "container_id", "reference", "runtime_image_id")))
        need(re.fullmatch(r"sha256:[0-9a-f]{64}", fact["config_id"]))
        resolved = fact["runtime_image_id"].removeprefix("docker-pullable://")
        canonical = published_module().canonical_digest
        need(re.fullmatch(r"[^\s@]+@sha256:[0-9a-f]{64}", resolved)
             and type(fact["repo_digests"]) is list
             and canonical(resolved) in {canonical(ref) for ref in fact["repo_digests"]})
        need(fact["platform"]["os"] == "linux" and fact["platform"]["architecture"])
        key = (fact["namespace"], fact["pod_uid"], fact["container"])
        need(key not in seen)
        seen.add(key)
        projects.add(project)
    # Same-role replicas must resolve to the same image/config/platform.
    return published_module()._runtime_facts(facts, projects)


def kube_runtime_images(runner, cri_runner, context: str) -> list[dict]:
    """Read pod bindings and local CRI metadata only; discard all image Config."""
    facts, images = [], {}
    for namespace in kube_json(runner, context, "get", "namespaces")["items"]:
        meta = namespace["metadata"]
        name = meta["name"]
        if not name.startswith("app-"):
            continue
        project = str(UUID(name[4:]))
        tags = meta.get("labels") or {}
        need(not meta.get("deletionTimestamp") and tags.get("omnia.retired") in {"true", "false"})
        if tags["omnia.retired"] == "true":
            continue  # Cold-retained census independently owns these obligations.
        need(all(tags.get(key) == value for key, value in
                 {PART: "max-app", MANAGED: "omnia-orchestrator", PROJECT: project}.items()))
        found = set()
        for pod in kube_json(runner, context, "get", "pods", "--namespace", name)["items"]:
            if pod.get("kind") != "Pod":
                continue
            metadata, spec, status = pod["metadata"], pod["spec"], pod["status"]
            labels = metadata.get("labels") or {}
            component = labels.get(COMPONENT)
            if component in COMPONENTS:
                continue
            role = "guard" if component == "boundary" else component
            need(role in {"app", "core", "guard", "redis", "analytics-relay"})
            need(all(labels.get(key) == value for key, value in
                     {PART: "max-app", MANAGED: "omnia-orchestrator", PROJECT: project}.items()))
            need(not metadata.get("deletionTimestamp") and status.get("phase") == "Running")
            containers, states = spec["containers"], status.get("containerStatuses", [])
            need(len(containers) == len(states) == 1)
            container, state = containers[0], states[0]
            need(container["name"] == state["name"] and state.get("ready") is True
                 and "running" in state.get("state", {}))
            resolved = state["imageID"].removeprefix("docker-pullable://")
            need(re.fullmatch(r"[^\s@]+@sha256:[0-9a-f]{64}", resolved))
            if resolved not in images:
                result = cri_runner.run(["crictl", "inspecti", "-o", "json", resolved], timeout=60)
                need(result.ok)
                raw = json.loads(result.stdout)
                image = raw["status"]
                platform = raw["info"]["imageSpec"]
                canonical = published_module().canonical_digest
                need(canonical(resolved) in {canonical(ref) for ref in image["repoDigests"]})
                images[resolved] = {
                    "config_id": image["id"], "repo_digests": sorted({canonical(ref) for ref in image["repoDigests"]}),
                    "platform": {key: platform.get(key) or "" for key in ("os", "architecture", "variant")},
                }
            facts.append({"project_id": project, "namespace": name, "namespace_uid": meta["uid"],
                          "component": role, "pod": metadata["name"], "pod_uid": metadata["uid"],
                          "container": container["name"], "container_id": state["containerID"],
                          "reference": container["image"], "runtime_image_id": state["imageID"],
                          **images[resolved]})
            found.add(role)
        need(found - {"analytics-relay"} == {"app", "core", "guard", "redis"})
    facts.sort(key=lambda row: (row["project_id"], row["component"], row["pod_uid"], row["container"]))
    validate_runtime_images(facts)
    return facts


def kube_census(runner, context: str, image: str) -> dict:
    namespaces = kube_json(runner, context, "get", "namespaces")
    targets, retired, other_claims, cold_retained = [], [], [], []
    for item in namespaces["items"]:
        metadata = item["metadata"]
        name = metadata["name"]
        if not name.startswith("app-"):
            continue
        project = str(UUID(name[4:]))
        need(name == "app-" + project and not metadata.get("deletionTimestamp"))
        labels = metadata.get("labels") or {}
        retirement = labels.get("omnia.retired")
        ownership = {PART: "max-app", MANAGED: "omnia-orchestrator", PROJECT: project}
        exact_owner = all(labels.get(key) == value for key, value in ownership.items())
        legacy_retired = retirement == "true" and all(
            key not in labels for key in ownership
        )
        need(retirement in {"true", "false"} and (exact_owner or legacy_retired))
        resources = kube_json(runner, context, "get", "pods,pvc", "--namespace", name)[
            "items"
        ]
        pods = [row for row in resources if row.get("kind") == "Pod"]
        claims = {
            row["metadata"]["name"]: row
            for row in resources
            if row.get("kind") == "PersistentVolumeClaim"
        }
        if retirement == "true":
            need(not pods)
            if claims:
                cold = cold_module()
                owner = cold.uuid(
                    next(iter(claims.values()))["metadata"]
                    .get("labels", {})
                    .get("omnia.owner-id")
                )
                volumes = []
                for claim in claims.values():
                    kind = cold.claim_kind(claim, project, owner)
                    volumes.append(
                        {
                            "name": claim["metadata"]["name"],
                            "uid": cold.uuid(claim["metadata"]["uid"]),
                            "kind": kind,
                        }
                    )
                need(
                    {
                        row["name"]
                        for row in volumes
                        if row["kind"] in {"core", "project"}
                    }
                    == set(cold.DATABASES)
                )
                cold_retained.append(
                    {
                        "namespace": name,
                        "namespace_uid": cold.uuid(metadata["uid"]),
                        "project_id": project,
                        "owner_id": owner,
                        "volumes": sorted(volumes, key=lambda row: row["name"]),
                    }
                )
            else:
                retired.append(
                    {
                        "namespace": name,
                        "uid": metadata["uid"],
                        "reason": "retired_empty",
                    }
                )
            continue
        used = set()
        for component in COMPONENTS:
            selected = [
                pod
                for pod in pods
                if (pod["metadata"].get("labels") or {}).get(COMPONENT) == component
            ]
            need(len(selected) == 1)
            pod = selected[0]
            meta, spec, status = pod["metadata"], pod["spec"], pod["status"]
            tags = meta.get("labels") or {}
            need(
                tags.get(PROJECT) == project
                and tags.get(MANAGED) == "omnia-orchestrator"
                and not meta.get("deletionTimestamp")
                and status.get("phase") == "Running"
            )
            containers = [
                row for row in spec["containers"] if row.get("name") == "postgres"
            ]
            states = [
                row
                for row in status.get("containerStatuses", [])
                if row.get("name") == "postgres"
            ]
            need(
                len(containers) == len(states) == 1
                and containers[0]["image"] == image
                and states[0].get("ready") is True
                and "running" in states[0].get("state", {})
                and type(states[0].get("containerID")) is str
                and states[0]["containerID"]
                and type(states[0].get("imageID")) is str
                and states[0]["imageID"].endswith(image.split("@", 1)[1])
            )
            mounts = [
                row
                for row in containers[0].get("volumeMounts", [])
                if row.get("mountPath") == "/var/lib/postgresql/data"
            ]
            need(len(mounts) == 1)
            volumes = [
                row for row in spec["volumes"] if row["name"] == mounts[0]["name"]
            ]
            need(len(volumes) == 1 and "persistentVolumeClaim" in volumes[0])
            claim = volumes[0]["persistentVolumeClaim"]["claimName"]
            need(
                claim in claims
                and claims[claim].get("status", {}).get("phase") == "Bound"
            )
            used.add(claim)
            targets.append(
                {
                    "project_id": project,
                    "workspace_id": str(
                        uuid5(NAMESPACE_URL, f"omnia:public-cell:{project}")
                    ),
                    "namespace": name,
                    "namespace_uid": metadata["uid"],
                    "pod": meta["name"],
                    "pod_uid": meta["uid"],
                    "container_id": states[0].get("containerID"),
                    "component": component,
                    "volume": claim,
                    "pvc_uid": claims[claim]["metadata"]["uid"],
                    "image": image,
                }
            )
        for claim in sorted(claims.keys() - used):
            other_claims.append(
                {
                    "namespace": name,
                    "claim": claim,
                    "scope": "non-database-pvc-not-copied",
                }
            )
        # An unexpected PostgreSQL pod must never disappear through a selector.
        need(
            all(
                (pod["metadata"].get("labels") or {}).get(COMPONENT) in COMPONENTS
                for pod in pods
                if any(
                    "postgres" in row.get("image", "")
                    for row in pod["spec"]["containers"]
                )
            )
        )
    return {
        "targets": sorted(
            targets, key=lambda value: (value["namespace"], value["component"])
        ),
        "retired": sorted(retired, key=lambda value: value["namespace"]),
        "non_database_pvcs": other_claims,
        "cold_retained": sorted(cold_retained, key=lambda row: row["namespace"]),
    }


def _service_account_mount(mount: dict, volumes: list[dict]) -> None:
    """Allow only Kubernetes' separate, read-only injected API credentials."""
    name = mount.get("name")
    need(type(name) is str and re.fullmatch(r"kube-api-access-[a-z0-9]{5}", name))
    need(mount.get("readOnly") is True and mount == {"name": name, "readOnly": True,
                   "mountPath": "/var/run/secrets/kubernetes.io/serviceaccount"})
    matches = [volume for volume in volumes if volume.get("name") == name]
    need(len(matches) == 1 and set(matches[0]) == {"name", "projected"})
    projected = matches[0]["projected"]
    need(type(projected) is dict and set(projected) <= {"defaultMode", "sources"}
         and projected.get("defaultMode", 420) == 420)
    sources = projected.get("sources")
    need(type(sources) is list and len(sources) == 3
         and all(type(source) is dict for source in sources))
    token = sources[0].get("serviceAccountToken", {})
    need(type(token) is dict and set(sources[0]) == {"serviceAccountToken"}
         and set(token) == {"expirationSeconds", "path"}
         and token.get("path") == "token"
         and type(token.get("expirationSeconds")) is int
         and 0 < token["expirationSeconds"] <= 86400)
    need(sources[1] == {"configMap": {"name": "kube-root-ca.crt",
         "items": [{"key": "ca.crt", "path": "ca.crt"}]}})
    need(sources[2] == {"downwardAPI": {"items": [{"fieldRef": {
         "apiVersion": "v1", "fieldPath": "metadata.namespace"}, "path": "namespace"}]}})


def _boundary_source_mount(container: dict, volumes: list[dict]) -> dict:
    mounts = container.get("volumeMounts", [])
    need(type(mounts) is list and 1 <= len(mounts) <= 2)
    boundary = [item for item in mounts if item.get("mountPath") == "/run/omnia-boundary"]
    need(len(boundary) == 1 and boundary[0].get("readOnly") is True
         and not boundary[0].get("subPath") and not boundary[0].get("subPathExpr"))
    for mount in mounts:
        if mount is not boundary[0]:
            _service_account_mount(mount, volumes)
    return boundary[0]


def _relay_source_mount(pod: dict, fact: dict) -> dict:
    """Accept only the owned, stateless receipt relay and its public source."""
    metadata, spec = pod["metadata"], pod["spec"]
    need(metadata.get("namespace") == fact["namespace"])
    owners = metadata.get("ownerReferences", [])
    need(type(owners) is list and len(owners) == 1)
    owner = owners[0]
    need(owner.get("apiVersion") == "apps/v1" and owner.get("kind") == "ReplicaSet"
         and owner.get("controller") is True
         and type(owner.get("uid")) is str and owner["uid"]
         and type(owner.get("name")) is str
         and re.fullmatch(r"analytics-relay-[a-z0-9]+", owner["name"]))
    need(not any(spec.get(key) for key in
                 ("initContainers", "ephemeralContainers", "hostNetwork", "hostPID", "hostIPC")))
    container = spec["containers"][0]
    command = container.get("command")
    need(container["name"] == "relay" and type(command) is list and len(command) == 9
         and command[:8] == ["python3", "/run/relay/relay.py", "--bind", "0.0.0.0",
                             "--relay-only", "--analytics-project", fact["project_id"],
                             "--analytics-origin"]
         and type(command[8]) is str and 0 < len(command[8]) <= 2048)
    need(not any(container.get(key) for key in
                 ("args", "env", "envFrom", "volumeDevices", "lifecycle")))
    security = container.get("securityContext", {})
    need(security.get("readOnlyRootFilesystem") is True
         and security.get("allowPrivilegeEscalation") is False
         and not security.get("privileged")
         and security.get("runAsUser") == security.get("runAsGroup") == 65534
         and security.get("capabilities", {}).get("drop") == ["ALL"]
         and not security.get("capabilities", {}).get("add"))
    mounts, volumes = container.get("volumeMounts", []), spec.get("volumes", [])
    need(type(mounts) is list and 1 <= len(mounts) <= 2 and type(volumes) is list)
    source = [mount for mount in mounts if mount.get("mountPath") == "/run/relay"]
    need(source == [{"name": "relay", "mountPath": "/run/relay", "readOnly": True}])
    need(len(volumes) == len(mounts)
         and len({volume.get("name") for volume in volumes}) == len(volumes)
         and {volume.get("name") for volume in volumes} == {mount.get("name") for mount in mounts})
    for mount in mounts:
        if mount is not source[0]:
            _service_account_mount(mount, volumes)
    volume = next(volume for volume in volumes if volume.get("name") == "relay")
    need(set(volume) == {"name", "configMap"})
    reference = volume["configMap"]
    need(type(reference) is dict and set(reference) <= {"name", "defaultMode", "optional", "items"}
         and reference.get("name") == "analytics-relay" and not reference.get("optional")
         and reference.get("defaultMode", 420) == 420
         and reference.get("items", [{"key": "relay.py", "path": "relay.py"}])
         == [{"key": "relay.py", "path": "relay.py"}])
    return source[0]


def boundary_snapshot(runner, context: str, images: list[dict]) -> list[dict]:
    """Bind public executable sources to every actual guard/relay Pod mount."""
    rows = []
    for fact in images:
        role = fact["component"]
        if role not in SOURCE_LAYOUTS:
            continue
        layout = SOURCE_LAYOUTS[role]
        namespace = fact["namespace"]
        pods = kube_json(runner, context, "get", "pods", "--namespace", namespace)["items"]
        matches = [pod for pod in pods if pod.get("kind") == "Pod"
                   and pod["metadata"]["uid"] == fact["pod_uid"]]
        need(len(matches) == 1)
        pod = matches[0]
        metadata, status = pod["metadata"], pod["status"]
        need(metadata["name"] == fact["pod"] and not metadata.get("deletionTimestamp")
             and status.get("phase") == "Running")
        need(all(metadata.get("labels", {}).get(key) == value for key, value in
                 {PART: "max-app", MANAGED: "omnia-orchestrator", PROJECT: fact["project_id"],
                  COMPONENT: layout["component"]}.items()))
        containers = pod["spec"]["containers"]
        need(len(containers) == 1 and containers[0]["name"] == fact["container"])
        container = containers[0]
        states = status.get("containerStatuses", [])
        need(len(states) == 1 and states[0]["name"] == fact["container"]
             and states[0].get("ready") is True and "running" in states[0].get("state", {})
             and states[0].get("containerID") == fact["container_id"]
             and states[0].get("imageID") == fact["runtime_image_id"]
             and container.get("image") == fact["reference"])
        if role == "analytics-relay":
            mount = _relay_source_mount(pod, fact)
        else:
            need(container.get("command") == ["python3", layout["path"]])
            mount = _boundary_source_mount(container, pod["spec"].get("volumes", []))
            volumes = [item for item in pod["spec"].get("volumes", [])
                       if item.get("name") == mount["name"]]
            need(len(volumes) == 1)
            sources = volumes[0].get("projected", {}).get("sources", [])
            need(all(not any(key.get("path") == layout["key"] for key in
                             source.get("secret", {}).get("items", [])) for source in sources))
            refs = [item["configMap"] for item in sources if "configMap" in item
                    and any(key.get("path") == layout["key"] for key in item["configMap"].get("items", []))]
            need(len(refs) == 1 and refs[0].get("name") == layout["configmap"]
                 and refs[0].get("items") == [{"key": layout["key"], "path": layout["key"]}]
                 and not refs[0].get("optional"))
        maps = kube_json(runner, context, "get", "configmaps", "--field-selector", "metadata.name=" + layout["configmap"],
                         "--namespace", namespace)["items"]
        need(len(maps) == 1)
        config = maps[0]
        metadata = config["metadata"]
        need(metadata.get("name") == layout["configmap"] and not metadata.get("deletionTimestamp"))
        need(all(metadata.get("labels", {}).get(key) == value for key, value in
                 {PART: "max-app", MANAGED: "omnia-orchestrator", PROJECT: fact["project_id"],
                  COMPONENT: layout["component"]}.items()))
        if role == "analytics-relay":
            need(metadata.get("namespace") == namespace
                 and set(config.get("data", {})) == {layout["key"]} and not config.get("binaryData"))
        source = config.get("data", {}).get(layout["key"])
        need(type(source) is str and 0 < len(source.encode("utf-8")) <= 1024 * 1024)
        payload = source.encode("utf-8")
        row = {**{key: fact[key] for key in
                  ("project_id", "namespace", "namespace_uid", "pod", "pod_uid", "container", "container_id")},
               "configmap_name": layout["configmap"], "configmap_uid": metadata["uid"],
               "configmap_resource_version": metadata["resourceVersion"],
               "volume": mount["name"], "key": layout["key"], "mounted_path": layout["path"],
               "file": fact["project_id"] + layout["suffix"],
               "bytes": len(payload), "sha256": hashlib.sha256(payload).hexdigest()}
        if role == "analytics-relay":
            row.update(component=role, pod_binding_sha256=digest({
                "spec": pod["spec"], "ownerReferences": pod["metadata"]["ownerReferences"]}))
        rows.append(row)
    return sorted(rows, key=lambda row: (row["project_id"], row.get("component", "guard"),
                                         row["pod_uid"], row["container"]))


def validate_boundary_sources(rows, images, directory=None):
    """Old guard-only bundles remain valid; every observed relay needs evidence."""
    need(type(rows) is list)
    sources = {}
    for fact in images:
        if fact["component"] not in SOURCE_LAYOUTS:
            continue
        key = (fact["project_id"], fact["component"], fact["pod_uid"], fact["container"])
        need(key not in sources)
        sources[key] = fact
    need(len(rows) == len(sources))
    seen, shared = set(), {}
    for row in rows:
        role = row.get("component", "guard")
        key = (row["project_id"], role, row["pod_uid"], row["container"])
        need(key in sources and key not in seen)
        seen.add(key)
        fact, layout = sources[key], SOURCE_LAYOUTS[role]
        need(all(row.get(key) == fact[key] for key in
                 ("namespace", "namespace_uid", "pod", "pod_uid", "container", "container_id")))
        need(row.get("file") == row["project_id"] + layout["suffix"]
             and row.get("configmap_name") == layout["configmap"] and row.get("key") == layout["key"]
             and row.get("mounted_path") == layout["path"]
             and all(type(row.get(key)) is str and row[key] for key in
                     ("configmap_uid", "configmap_resource_version", "volume"))
             and type(row.get("bytes")) is int and 0 < row["bytes"] <= 1024 * 1024
             and type(row.get("sha256")) is str and re.fullmatch(r"[0-9a-f]{64}", row["sha256"]))
        if role == "analytics-relay":
            need(type(row.get("pod_binding_sha256")) is str
                 and re.fullmatch(r"[0-9a-f]{64}", row["pod_binding_sha256"]))
        source = (row["project_id"], role)
        identity = tuple(row[field] for field in
                         ("configmap_uid", "configmap_resource_version", "bytes", "sha256"))
        need(source not in shared or shared[source] == identity)
        shared[source] = identity
        if directory is not None:
            path = directory / row["file"]
            need(path.is_file() and not path.is_symlink())
            sha, size = hashlib.sha256(path.read_bytes()).hexdigest(), path.stat().st_size
            need(sha == row["sha256"] and size == row["bytes"])


def validate_mounted_sources(runner, context: str, rows: list[dict]) -> None:
    """Rehash mounted bytes without copying secrets or creating a backup stage."""
    probe = ("import hashlib,json,sys; "
             "p=open(sys.argv[1],'rb').read(1048577); "
             "print(json.dumps({'bytes':len(p),'sha256':hashlib.sha256(p).hexdigest()}))")
    for row in rows:
        result = runner.run(
            ["--context", context, "exec", "--namespace", row["namespace"], row["pod"],
             "--container", row["container"], "--", "python3", "-c", probe, row["mounted_path"]],
            timeout=60)
        need(result.ok and json.loads(result.stdout) == {"bytes": row["bytes"], "sha256": row["sha256"]})


def kube_backup(cells, spec: dict, out: Path) -> tuple[dict, int]:
    runner = cells.DockerRunner(spec["kubectl"])
    cri = cells.DockerRunner("/usr/local/bin/k3s")
    images = kube_runtime_images(runner, cri, spec["context"])
    boundaries = boundary_snapshot(runner, spec["context"], images)
    validate_boundary_sources(boundaries, images)
    captured = set()
    for row in boundaries:
        if row["file"] in captured:
            continue  # Replica source identity was checked above; never overwrite.
        result = runner.run_to_file(
            ["--context", spec["context"], "exec", "--namespace", row["namespace"], row["pod"],
             "--container", row["container"], "--", "cat", row["mounted_path"]],
            path=out / row["file"], timeout=60)
        need(result.ok and result.size_bytes == row["bytes"] and result.sha256 == row["sha256"])
        os.chmod(out / row["file"], 0o600)
        captured.add(row["file"])
    validate_boundary_sources(boundaries, images, out)
    validate_mounted_sources(runner, spec["context"], boundaries)
    before = kube_census(runner, spec["context"], spec["postgres_image"])
    authorities = spec.get("cold_authorities", [])
    need(type(authorities) is list)
    by_project = {item["project_id"]: item for item in authorities}
    need(len(by_project) == len(authorities))
    cold_retained = []
    for obligation in before["cold_retained"]:
        authority = by_project.get(obligation["project_id"])
        need(authority is not None and authority["owner_id"] == obligation["owner_id"])
        cold_retained.append(
            cold_module().collect_one(
                cells, runner, spec, authority, out, expected_inventory=obligation
            )
        )
    workspaces: dict[str, dict] = {}
    for target in before["targets"]:
        prefix = [
            "--context",
            spec["context"],
            "exec",
            "--namespace",
            target["namespace"],
            target["pod"],
            "--container",
            "postgres",
            "--",
            "env",
            "PGOPTIONS=" + PGOPTIONS,
        ]
        kind = "core" if target["component"] == "core-postgres" else "project"
        filename = target["workspace_id"] + (
            "-core.dump" if kind == "core" else "-project.sql"
        )
        command = [
            "pg_dumpall",
            "-h",
            "/tmp,/var/run/postgresql",
            "-U",
            "postgres",
            "--lock-wait-timeout=30s",
        ]
        result = runner.run_to_file(
            [*prefix, *command], path=out / filename, timeout=1800
        )
        need(result.ok and result.size_bytes > 0)
        count = runner.run(
            [
                *prefix,
                "psql",
                "-X",
                "-v",
                "ON_ERROR_STOP=1",
                "-qAt",
                "-h",
                "/tmp,/var/run/postgresql",
                "-U",
                "postgres",
                "-d",
                "postgres",
                "-c",
                cells._TABLE_COUNT_SQL,
            ],
            timeout=60,
        )
        need(count.ok and re.fullmatch(r"\d+", count.text().strip()))
        workspace = workspaces.setdefault(
            target["workspace_id"],
            {
                "workspace_id": target["workspace_id"],
                "project_id": target["project_id"],
                "role": "production",
                "databases": [],
                "data_volumes": [],
            },
        )
        workspace["databases"].append(
            {
                "kind": kind,
                "volume": target["volume"],
                "status": "ok",
                "method": "kubectl_exec",
                "format": "plain",
                "file": filename,
                "bytes": result.size_bytes,
                "sha256": result.sha256,
                "tables": int(count.text().strip()),
            }
        )
    need(kube_census(runner, spec["context"], spec["postgres_image"]) == before)
    need(kube_runtime_images(runner, cri, spec["context"]) == images)
    need(boundary_snapshot(runner, spec["context"], images) == boundaries)
    validate_mounted_sources(runner, spec["context"], boundaries)
    inventory = out / "runtime-inventory.json"
    cells._write_private_json(inventory, before)
    with tarfile.open(out / "state.tar.gz", "w:gz") as archive:
        archive.add(inventory, arcname="runtime-inventory.json")
    os.chmod(out / "state.tar.gz", 0o600)
    sha, size = cells._file_digest(out / "state.tar.gz")
    rows = list(workspaces.values())
    manifest = {
        "schema_version": cells.SCHEMA_VERSION,
        "provider": "kubernetes",
        "created_at": cells._iso_now(),
        "state_archive": {
            "file": "state.tar.gz",
            "bytes": size,
            "sha256": sha,
            "scope": "runtime-census",
        },
        "workspaces": rows,
        "orphans": [],
        "runtime_census": before,
        "recovery_coverage_version": 2,
        "runtime_images": images,
        "boundary_sources": boundaries,
        "cold_retained": cold_retained,
        "coverage": {
            "version": 1,
            "scope": "local-docker",
            "expected": cells._artifact_identities(rows),
            "issues": [],
            "external_publications": [],
        },
    }
    return manifest, 0


def export_peer(cells, spec: dict) -> int:
    import signal

    def deadline(_number, _frame):
        raise TimeoutError("peer backup deadline exceeded")

    signal.signal(signal.SIGALRM, deadline)
    signal.alarm(6800)
    root = Path(spec["temp_root"])
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    need(not root.is_symlink())
    out = Path(tempfile.mkdtemp(prefix="yleum-backup-peer-", dir=root))
    succeeded = False
    phase = "peer_prepare"
    try:
        os.chmod(out, 0o700)
        with redirect_stdout(sys.stderr):
            phase = "database_backup"
            if spec["provider"] == "docker":
                census = cells.parse_platform_census(
                    json.dumps(spec["platform_census"])
                )
                manifest, code = cells.run_backup(
                    state_root=Path(spec["state_root"]),
                    out_dir=out,
                    postgres_image=spec["postgres_image"],
                    helper_image=spec["helper_image"],
                    runner=cells.DockerRunner(),
                    local_scope=True,
                    platform_census_json=json.dumps(census),
                    resolve_docker_sources_with_sudo=True,
                )
                if code == 0:
                    phase = "published_artifacts"
                    add_published(cells, manifest, Path(spec["state_root"]), out,
                                  spec.get("runtime_images", []))
            else:
                manifest, code = kube_backup(cells, spec, out)
            phase = "manifest_write"
            manifest["node"] = spec["name"]
            manifest["producer_sha256"] = spec["producer_sha256"]
            cells._write_private_json(out / "MANIFEST.json", manifest)
        phase = "archive_stream"
        with tarfile.open(fileobj=sys.stdout.buffer, mode="w|gz") as archive:
            for item in sorted(out.iterdir()):
                need(item.is_file() and not item.is_symlink())
                archive.add(item, arcname=item.name)
        succeeded = code == 0
        if not succeeded:
            _emit_peer_error(_peer_error_record(spec["name"], "database_backup", exit_code=code))
        return code
    except Exception as error:  # Typed metadata only; failure stays fatal.
        raise PeerCommandError(_peer_error_record(spec.get("name"), phase, error)) from None
    finally:
        if succeeded:
            # Only this fresh flat stage. Failed/uncertain helper intent evidence
            # stays private on the peer until its resources are reconciled.
            for item in out.iterdir():
                need(item.is_file() and not item.is_symlink())
                item.unlink()
            out.rmdir()


def peer_program() -> str:
    return (
        "import base64,json,sys,types,zlib; "
        "p=json.loads(zlib.decompress(base64.b64decode(sys.argv[1]))); "
        "c=types.ModuleType('backup_cells_peer'); sys.modules[c.__name__]=c; "
        "exec(compile(p['sources']['cells'],'backup_cells_peer','exec'),c.__dict__); "
        "d=types.ModuleType('backup_cold_peer'); sys.modules[d.__name__]=d; "
        "exec(compile(p['sources'].get('cold',''),'backup_cold_peer','exec'),d.__dict__); "
        "a=types.ModuleType('backup_published_peer'); sys.modules[a.__name__]=a; "
        "exec(compile(p['sources'].get('published',''),'backup_published_peer','exec'),a.__dict__); "
        "r=types.ModuleType('runtime_backup_peer'); "
        "exec(compile(p['sources']['runtime'],'runtime_backup_peer','exec'),r.__dict__); "
        "raise SystemExit(r.peer_main(c,p))"
    )


def encode_peer(payload: dict) -> str:
    value = base64.b64encode(zlib.compress(json.dumps(payload).encode(), 9)).decode()
    need(len(value) < 100000)
    return value


def peer_main(cells, payload: dict) -> int:
    try:
        # The root dispatcher preserves the owner child's final record and exit.
        return dispatch_peer(cells, payload)
    except Exception as error:  # Secret-safe peer CLI boundary.
        record = (
            error.diagnostic if isinstance(error, PeerCommandError)
            else _peer_error_record(payload.get("spec", {}).get("name"), "peer_dispatch", error)
        )
        _emit_peer_error(record)
        return 1


def dispatch_peer(cells, payload: dict) -> int:
    spec = payload["spec"]
    if spec.get("mode") == "images-only":
        need(spec["provider"] == "kubernetes")
        runner = cells.DockerRunner(spec["kubectl"])
        facts = kube_runtime_images(runner,
                                    cells.DockerRunner("/usr/local/bin/k3s"), spec["context"])
        sources = boundary_snapshot(runner, spec["context"], facts)
        validate_boundary_sources(sources, facts)
        if "expected_boundary_sources" in spec:
            need(sources == spec["expected_boundary_sources"])
        validate_mounted_sources(runner, spec["context"], sources)
        print(json.dumps({"runtime_images": facts, "producer_sha256": spec["producer_sha256"]}))
        return 0
    need(spec.get("mode") is None)
    if spec["provider"] != "docker" or spec.get("as_owner") is True:
        return export_peer(cells, spec)
    # The controller validates credential ownership against its Unix identity.
    # Root only owns the new staging parent; no existing state is chowned.
    import pwd

    root = Path(spec["temp_root"])
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    need(not root.is_symlink())
    stage = tempfile.mkdtemp(prefix=".peer-backup-owner-", dir=root)
    try:
        owner = pwd.getpwnam("zeuszcz")
        os.chown(stage, owner.pw_uid, owner.pw_gid)
        payload["spec"] = {**spec, "temp_root": stage, "as_owner": True}
        census = cells.parse_platform_census(json.dumps(spec["platform_census"]))
        return subprocess.run(
            [
                "sudo",
                "-n",
                "-u",
                "zeuszcz",
                "/opt/omnia/apps/orchestrator/.venv/bin/python",
                "-c",
                peer_program(),
                encode_peer(payload),
            ],
            timeout=7000,
            check=False,
            env={**os.environ, "BACKUP_PLATFORM_CENSUS_JSON": json.dumps(census)},
        ).returncode
    finally:
        try:
            os.rmdir(stage)
        except OSError:
            pass  # Retain failed backup evidence; never recursively delete here.


def unpack(path: Path, out: Path) -> None:
    out.mkdir(mode=0o700)
    with tarfile.open(path, "r:gz") as archive:
        names = set()
        for member in archive:
            need(
                member.isfile()
                and member.name not in names
                and Path(member.name).name == member.name
                and member.name not in {".", ".."}
                and not member.name.startswith("/")
            )
            names.add(member.name)
            stream = archive.extractfile(member)
            need(stream is not None)
            with os.fdopen(
                os.open(out / member.name, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600),
                "wb",
            ) as sink:
                while chunk := stream.read(1024 * 1024):
                    sink.write(chunk)
    need("MANIFEST.json" in names)


def published_pointer(cells, directory, sidecar, witness):
    sha, size = cells._file_digest(directory / "PUBLISHED.json")
    return {"version": 1, "file": "PUBLISHED.json", "sha256": sha, "bytes": size,
            "blob_count": len(sidecar["blobs"]), "blob_bytes": sum(b["size"] for b in sidecar["blobs"].values()),
            "runtime_witness": witness,
            "publications": [{key: row[key] for key in
                              ("project_id", "release_id", "epoch", "journal_sha256", "databases", "images", "durable_pvc_gaps")}
                             for row in sidecar["publications"]]}


def add_published(cells, manifest, state_root, directory, runtime_images):
    module = published_module()
    runner = cells.DockerRunner()
    witness = module.resolve_witness(cells, runner, state_root, directory / "state.tar.gz", runtime_images)
    sidecar = module.collect(cells, runner, state_root, directory / "state.tar.gz", directory,
                             runtime_witness=witness)
    manifest["published_artifacts"] = published_pointer(cells, directory, sidecar, witness)
    manifest["recovery_coverage_version"] = 2


def validate_local(cells, directory: Path) -> dict:
    manifest = read(directory / "MANIFEST.json")
    cells._validate_manifest(manifest)
    need(not manifest["coverage"]["issues"])
    for entry in cells._verifiable_entries(manifest):
        need(entry.get("status") == "ok" and type(entry.get("file")) is str)
        name = entry["file"]
        need(cells._artifact_name(entry) == name)
        path = directory / name
        need(path.is_file() and not path.is_symlink())
        sha, size = cells._file_digest(path)
        need(size > 0 and sha == entry.get("sha256") and size == entry.get("bytes"))
    if manifest.get("provider") == "kubernetes":
        cold_module().validate(cells, directory, manifest.get("cold_retained", []))
        cold_module().coverage(manifest)
    version = manifest.get("recovery_coverage_version", 1)
    need(type(version) is int and version in {1, 2})
    if version == 2:
        if manifest.get("provider") == "kubernetes":
            facts = validate_runtime_images(manifest.get("runtime_images"))
            need(set(facts) == {row["project_id"] for row in manifest["workspaces"]})
            validate_boundary_sources(manifest.get("boundary_sources"), manifest["runtime_images"], directory)
        else:
            pointer = manifest.get("published_artifacts")
            need(type(pointer) is dict)
            witness = pointer["runtime_witness"]
            sidecar = published_module().validate(directory, directory / "state.tar.gz", runtime_witness=witness)
            need(pointer == published_pointer(cells, directory, sidecar, witness))
    else:
        need("published_artifacts" not in manifest and "runtime_images" not in manifest)
    return manifest


def close_coverage(manifests: dict[str, dict], *, require_published=False) -> dict:
    need(set(manifests) == set(NODES))
    expected = {}
    for name in ("core", "commerce"):
        for item in manifests[name]["coverage"]["external_publications"]:
            project = item["project_id"]
            need(project not in expected or expected[project] == item)
            expected[project] = item
    runtime = manifests["runtime"]
    observed = {}
    for workspace in runtime["workspaces"]:
        need(
            len(workspace["databases"]) == 2
            and {row["kind"] for row in workspace["databases"]} == {"core", "project"}
        )
        project = workspace["project_id"]
        need(project not in observed)
        observed[project] = {
            "project_id": project,
            "workspace_id": workspace["workspace_id"],
            "namespace": "app-" + project,
        }
    need(observed == expected)
    cold = cold_module().coverage(runtime)
    result = {
        "version": 1,
        "full_dr_verified": False,
        "coverage_complete": True,
        "scope": "platform-and-project-databases",
        "nodes": {name: digest(manifest) for name, manifest in manifests.items()},
        "published_projects": sorted(observed),
        "published_databases": len(observed) * 2,
        "non_database_pvcs": runtime["runtime_census"]["non_database_pvcs"],
        "cold_retained": cold,
        "excluded_system_databases": "max-* are covered by the separate max-backup service",
    }
    if require_published or any(item.get("recovery_coverage_version", 1) == 2 for item in manifests.values()):
        need(all(item.get("recovery_coverage_version") == 2 for item in manifests.values()))
        facts = validate_runtime_images(runtime["runtime_images"])
        validate_boundary_sources(runtime.get("boundary_sources"), runtime["runtime_images"])
        need(set(facts) == set(observed))
        published, pointers, gaps = {}, {}, {}
        for node in ("core", "commerce"):
            pointer = manifests[node].get("published_artifacts")
            need(type(pointer) is dict)
            need(pointer["file"] == "PUBLISHED.json" and pointer["bytes"] > 0
                 and re.fullmatch(r"[0-9a-f]{64}", pointer["sha256"]))
            pointers[node] = {key: pointer[key] for key in ("file", "bytes", "sha256", "blob_count", "blob_bytes")}
            for row in pointer["publications"]:
                project = row["project_id"]
                need(project not in published and project in observed and row["images"] == facts[project])
                databases = row["databases"]
                need(len(databases) == 2 and {db["kind"] for db in databases} == {"core", "project"})
                need(all(db["project_id"] == project and db["workspace_id"] == observed[project]["workspace_id"]
                         and db["source"] == "existing-runtime-backup" for db in databases))
                published[project] = row["release_id"]
                gaps[project] = row["durable_pvc_gaps"]
        need(set(published) == set(observed))
        result.update(version=2, scope="platform-and-project-databases-and-active-published-artifacts",
                      published_artifacts=pointers, active_releases=published,
                      runtime_images=runtime["runtime_images"], boundary_sources=runtime["boundary_sources"], durable_pvc_gaps=gaps)
    return result


def load_cells(path: Path):
    spec = importlib.util.spec_from_file_location("backup_cells_peer", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def collect(cells, args) -> None:
    nodes = topology(args.topology)
    census = cells.parse_platform_census(
        os.environ.get("BACKUP_PLATFORM_CENSUS_JSON", "")
    )
    need(
        re.fullmatch(r".+@sha256:[0-9a-f]{64}", args.postgres_image)
        and re.fullmatch(r".+@sha256:[0-9a-f]{64}", args.helper_image)
    )
    args.out.mkdir(mode=0o700)
    manifests = {"core": validate_local(cells, args.core_cells)}
    core = next(node for node in nodes if node["name"] == "core")
    with cold_module().publication_locks(
        cells, Path(core["state_root"]), args.core_cells / "state.tar.gz"
    ) as authorities:
        _collect_peers(cells, args, nodes, manifests, authorities, census)


def _collect_peers(cells, args, nodes, manifests, authorities, census):
    sources = {
        "cells": args.cells_script.read_text(),
        "runtime": Path(__file__).read_text(),
        "cold": Path(__file__).with_name("cold-retained.py").read_text(),
        "published": Path(__file__).with_name("published-artifacts.py").read_text(),
    }
    by_name = {node["name"]: node for node in nodes}
    common = {"temp_root": args.peer_temp_root, "postgres_image": args.postgres_image,
              "helper_image": args.helper_image, "cold_authorities": authorities, "platform_census": census,
              "producer_sha256": {name: hashlib.sha256(value.encode()).hexdigest() for name, value in sources.items()}}
    runtime_spec = {**common, **by_name["runtime"]}
    manifests["runtime"] = _fetch_peer(cells, args.out, sources, runtime_spec)
    images = manifests["runtime"]["runtime_images"]
    # Core's original state.tar.gz remains the publication/request authority.
    manifest_path = args.core_cells / "MANIFEST.json"
    original = manifest_path.read_bytes()
    need(json.loads(original) == manifests["core"])
    add_published(cells, manifests["core"], Path(by_name["core"]["state_root"]), args.core_cells, images)
    manifests["core"]["producer_sha256"] = common["producer_sha256"]
    replacement = args.core_cells / "MANIFEST.published.json"
    cells._write_private_json(replacement, manifests["core"])
    need(not manifest_path.is_symlink() and manifest_path.read_bytes() == original)
    os.replace(replacement, manifest_path)
    commerce_spec = {**common, **by_name["commerce"], "runtime_images": images}
    manifests["commerce"] = _fetch_peer(cells, args.out, sources, commerce_spec)
    coverage = close_coverage(manifests, require_published=True)
    # No second PG export: recheck only Pod/CRI bindings and ConfigMap source witnesses.
    need(_fetch_runtime_images(cells, sources, {**runtime_spec,
         "expected_boundary_sources": manifests["runtime"]["boundary_sources"]}) == images)
    cold_module().verify_authorities(
        args.core_cells / "state.tar.gz", manifests["runtime"].get("cold_retained", [])
    )
    coverage["topology_sha256"] = hashlib.sha256(args.topology.read_bytes()).hexdigest()
    cells._write_private_json(args.out / "COVERAGE.json", coverage)


def _peer_command(sources, spec):
    interpreter = "/opt/omnia/apps/orchestrator/.venv/bin/python" if spec["provider"] == "docker" else "python3"
    payload = encode_peer({"sources": sources, "spec": spec})
    command = "sudo -n " + shlex.quote(interpreter) + " -c " + shlex.quote(peer_program()) + " " + shlex.quote(payload)
    return ["-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes", "-o", "ConnectTimeout=15",
            spec["ssh_alias"], command]


def _fetch_peer(cells, out, sources, spec):
    archive = out / (spec["name"] + ".tgz")
    result = cells.DockerRunner("ssh").run_to_file(_peer_command(sources, spec), path=archive, timeout=7200)
    if not result.ok:
        raise _peer_failure(result, spec["name"])
    with tempfile.TemporaryDirectory(prefix="verify-peer-", dir=out) as temp:
        directory = Path(temp) / spec["name"]
        unpack(archive, directory)
        manifest = validate_local(cells, directory)
    need(manifest.get("node") == spec["name"] and manifest.get("producer_sha256") == spec["producer_sha256"])
    need(manifest.get("recovery_coverage_version") == 2)
    return manifest


def _fetch_runtime_images(cells, sources, spec):
    result = cells.DockerRunner("ssh").run(_peer_command(sources, {**spec, "mode": "images-only"}), timeout=600)
    need(result.ok)
    value = json.loads(result.stdout)
    need(value["producer_sha256"] == spec["producer_sha256"])
    validate_runtime_images(value["runtime_images"])
    return value["runtime_images"]


def verify(cells, args) -> None:
    manifests = {}
    with tempfile.TemporaryDirectory(prefix="yleum-peer-restore-") as temp:
        for name in NODES:
            directory = args.core_cells if name == "core" else Path(temp) / name
            if name != "core":
                unpack(args.peers / (name + ".tgz"), directory)
            manifests[name] = validate_local(cells, directory)
            _, code = cells.run_verify(
                backup_dir=directory,
                postgres_image=args.postgres_image,
                runner=cells.DockerRunner(),
            )
            need(code == 0)
            if name == "runtime":
                cold_module().verify_authorities(
                    args.core_cells / "state.tar.gz",
                    manifests[name].get("cold_retained", []),
                )
                cold_module().verify(
                    cells,
                    directory,
                    manifests[name].get("cold_retained", []),
                    args.postgres_image,
                    cells.DockerRunner(),
                )
    recorded = read(args.peers / "COVERAGE.json")
    actual = close_coverage(manifests)
    need(all(recorded.get(key) == value for key, value in actual.items()
             if not (actual["version"] == 1 and key == "full_dr_verified")))
    need(recorded.get("full_dr_verified", False) is False)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("collect", "verify"))
    parser.add_argument("--cells-script", type=Path, required=True)
    parser.add_argument("--core-cells", type=Path, required=True)
    parser.add_argument("--postgres-image", required=True)
    parser.add_argument("--helper-image")
    parser.add_argument("--topology", type=Path)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--peers", type=Path)
    parser.add_argument("--peer-temp-root", default="/opt/omnia-runtime/backups")
    args = parser.parse_args()
    try:
        cells = load_cells(args.cells_script)
        if args.command == "collect":
            need(args.topology is not None and args.out is not None)
            collect(cells, args)
        else:
            need(args.peers is not None)
            verify(cells, args)
        print(
            json.dumps(
                {"coverage_complete": True, "full_dr_verified": False,
                 "scope": (read((args.out if args.command == "collect" else args.peers) / "COVERAGE.json"))["scope"]}
            )
        )
        return 0
    except Exception as error:  # noqa: BLE001 -- secret-safe outer command boundary
        # SQL, kubectl and peer stderr can contain retained secrets or row data.
        failure = {"coverage_complete": False, "error_class": type(error).__name__}
        if isinstance(error, PeerCommandError):
            failure["peer_error"] = error.diagnostic
        print(
            json.dumps(failure),
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
