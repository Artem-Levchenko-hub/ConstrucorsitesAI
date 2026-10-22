#!/usr/bin/env python3
"""Cold retained PVC copies. Only owned helper Pods and Docker scratch are mutated."""

from __future__ import annotations

import hashlib
import http.client
import json
import os
import posixpath
import re
import secrets
import signal
import socket
import stat
import subprocess
import tarfile
import tempfile
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlencode
from uuid import UUID

PART = "app.kubernetes.io/part-of"
MANAGED = "app.kubernetes.io/managed-by"
COMPONENT = "app.kubernetes.io/component"
PROJECT = "omnia.project-id"
OWNER = "omnia.owner-id"
INTENT = "omnia.backup-intent"
BARRIER = "omnia.backup-finished"
DATABASES = {"data-core-postgres-0": "core", "data-project-postgres-0": "project"}
CONTROLLERS = (
    ("apps/v1", "deployments"),
    ("apps/v1", "statefulsets"),
    ("apps/v1", "daemonsets"),
    ("apps/v1", "replicasets"),
    ("batch/v1", "jobs"),
    ("batch/v1", "cronjobs"),
)
_BARRIER_TIMEOUT = 30.0
_CLEANUP_TIMEOUT = 60.0

# Same trusted startup overrides as the controller, but no controller-package
# import: this verifier runs with only the standalone archive helper sources.
# A source-AST equality regression keeps this narrow offline copy from drifting.
_SCRATCH_ADMIN_PGOPTIONS = " ".join(
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
    )
)

# fd3 preserves the exec stdout while command substitution captures only the
# digest. pipefail forbids a successful sha256sum from hiding tar/tee failure.
_TAR_STREAM_SCRIPT = r"""set -eu
set -o pipefail
exec 3>&1
checksum=$(tar -cf - -C "$1" . | tee /proc/self/fd/3 | sha256sum)
exec 3>&-
checksum=${checksum%% *}
case "$checksum" in ''|*[!0-9a-f]*) exit 4;; esac
[ "${#checksum}" -eq 64 ]
printf 'YLEUM-COLD-1 %s %s\n' "$2" "$checksum"
IFS= read -r acknowledgment
[ "$acknowledgment" = "YLEUM-COLD-ACK-1 $2 $checksum" ]
"""


def need(value):
    if not value:
        raise ValueError("cold retained backup proof is unconfirmed")


def uuid(value):
    need(type(value) is str and str(UUID(value)) == value)
    return value


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


class UnixConnection(http.client.HTTPConnection):
    def __init__(self, path, timeout):
        super().__init__("localhost", timeout=timeout)
        self.path = str(path)

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect(self.path)


class KubeProxy:
    """Use existing kubectl credentials without copying them or exposing a TCP proxy."""

    def __init__(self, binary, context, root):
        self.binary, self.context, self.root = binary, context, root

    def __enter__(self):
        need(os.name == "posix" and os.geteuid() == 0)
        self.directory = tempfile.TemporaryDirectory(prefix="yleum-cold-", dir="/tmp")
        os.chmod(self.directory.name, 0o700)
        self.path = Path(self.directory.name) / "api.sock"
        need(len(os.fsencode(self.path)) < 108)
        self.errors, self.process = None, None
        try:
            self.errors = tempfile.TemporaryFile()
            self.process = subprocess.Popen(
                [
                    self.binary,
                    "--context",
                    self.context,
                    "proxy",
                    "--unix-socket",
                    str(self.path),
                    "--accept-hosts=^localhost$",
                ],
                stdin=subprocess.DEVNULL,
                stdout=self.errors,
                stderr=self.errors,
                start_new_session=True,
            )
            deadline = time.monotonic() + 20
            while True:
                try:
                    self.request("GET", "/version")
                    return self
                except (OSError, http.client.HTTPException):
                    need(time.monotonic() < deadline)
                    time.sleep(0.1)
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def __exit__(self, *_):
        # The child has not been reaped: its PID/group cannot have been reused.
        try:
            if self.process is not None:
                try:
                    os.killpg(self.process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                self.process.wait(timeout=5)
        finally:
            if self.errors is not None:
                self.errors.close()
            self.directory.cleanup()

    def request(self, method, path, body=None, *, missing=False, patch=False):
        connection = UnixConnection(self.path, 60)
        try:
            headers = {
                "Content-Type": "application/merge-patch+json"
                if patch
                else "application/json"
            }
            connection.request(
                method,
                path,
                json.dumps(body).encode() if body is not None else None,
                headers,
            )
            response = connection.getresponse()
            if missing and response.status == 404:
                return None
            need(200 <= response.status < 300)
            payload = response.read(16 * 1024 * 1024 + 1)
            need(len(payload) <= 16 * 1024 * 1024)
            return json.loads(payload) if payload else {}
        finally:
            connection.close()

    def watch(self, namespace, resource_version):
        connection = UnixConnection(self.path, 60)
        query = urlencode(
            {
                "watch": "true",
                "resourceVersion": resource_version,
                "allowWatchBookmarks": "true",
                "timeoutSeconds": "2100",
            }
        )
        connection.request("GET", f"/api/v1/namespaces/{namespace}/pods?{query}")
        connection._cold_socket = connection.sock
        response = None
        try:
            response = connection.getresponse()
            need(response.status == 200)
            connection._cold_socket.settimeout(2200)
            return connection, response
        except BaseException:
            if response is not None:
                response.close()
            connection.close()
            raise


def helper_pod(plan, name, intent, image):
    need(re.fullmatch(r".+@sha256:[0-9a-f]{64}", image))
    mounts = [
        {"name": f"pvc-{index}", "mountPath": f"/source/{index}", "readOnly": True}
        for index, _ in enumerate(plan["volumes"])
    ]
    volumes = [
        {
            "name": f"pvc-{index}",
            "persistentVolumeClaim": {"claimName": item["name"], "readOnly": True},
        }
        for index, item in enumerate(plan["volumes"])
    ]
    return {
        "apiVersion": "v1",
        "kind": "Pod",
        "metadata": {
            "name": name,
            "namespace": plan["namespace"],
            "labels": {INTENT: intent},
            "annotations": {INTENT: intent},
        },
        "spec": {
            "restartPolicy": "Never",
            "activeDeadlineSeconds": 2100,
            "terminationGracePeriodSeconds": 0,
            "automountServiceAccountToken": False,
            "enableServiceLinks": False,
            "hostNetwork": False,
            "hostPID": False,
            "hostIPC": False,
            "nodeSelector": {"kubernetes.io/hostname": plan["node"]},
            "securityContext": {"seccompProfile": {"type": "RuntimeDefault"}},
            "containers": [
                {
                    "name": "archive",
                    "image": image,
                    "imagePullPolicy": "IfNotPresent",
                    "command": ["sh", "-ec", "sleep 2100"],
                    "volumeMounts": mounts,
                    "resources": {
                        "requests": {"cpu": "50m", "memory": "64Mi"},
                        "limits": {"cpu": "500m", "memory": "256Mi"},
                    },
                    "securityContext": {
                        "runAsUser": 0,
                        "runAsGroup": 0,
                        "allowPrivilegeEscalation": False,
                        "readOnlyRootFilesystem": True,
                        "capabilities": {"drop": ["ALL"], "add": ["DAC_OVERRIDE"]},
                    },
                }
            ],
            "volumes": volumes,
        },
    }


def own_pod(row, expected, uid=None):
    meta, spec = row["metadata"], row["spec"]
    need(
        meta["name"] == expected["metadata"]["name"]
        and meta["namespace"] == expected["metadata"]["namespace"]
        and meta.get("annotations", {}).get(INTENT)
        == expected["metadata"]["annotations"][INTENT]
        and not meta.get("deletionTimestamp")
    )
    actual_uid = uuid(meta["uid"])
    need(uid is None or actual_uid == uid)
    for key in (
        "restartPolicy",
        "activeDeadlineSeconds",
        "terminationGracePeriodSeconds",
        "automountServiceAccountToken",
        "enableServiceLinks",
        "hostNetwork",
        "hostPID",
        "hostIPC",
        "nodeSelector",
        "securityContext",
        "volumes",
    ):
        default = False if key in {"hostNetwork", "hostPID", "hostIPC"} else None
        need(spec.get(key, default) == expected["spec"][key])
    need(
        not spec.get("initContainers")
        and not spec.get("ephemeralContainers")
        and len(spec["containers"]) == 1
    )
    actual, wanted = spec["containers"][0], expected["spec"]["containers"][0]
    for key in (
        "name",
        "image",
        "command",
        "volumeMounts",
        "resources",
        "securityContext",
    ):
        need(actual.get(key) == wanted[key])
    need(
        not actual.get("env")
        and not actual.get("envFrom")
        and not actual.get("args")
        and not actual.get("volumeDevices")
    )
    return actual_uid


class PodWatch:
    def __init__(self, api, expected, resource_version):
        self.expected, self.uid = expected, None
        self.error, self.barrier, self.stopping = None, threading.Event(), False
        self.version = resource_version
        need(type(resource_version) is str and resource_version)
        self.connection, self.response = api.watch(
            expected["metadata"]["namespace"], resource_version
        )
        self.thread = threading.Thread(
            target=self._read, daemon=True, name="cold-pod-watch"
        )
        self.thread.start()

    def observe(self, event):
        if event.get("type") == "BOOKMARK":
            return
        need(event.get("type") in {"ADDED", "MODIFIED"})
        row = event["object"]
        self.uid = own_pod(row, self.expected, self.uid)
        if (
            row["metadata"].get("annotations", {}).get(BARRIER)
            == self.expected["metadata"]["annotations"][INTENT]
        ):
            self.barrier.set()

    def _read(self):
        try:
            while not self.stopping:
                line = self.response.readline(4 * 1024 * 1024 + 1)
                need(line and len(line) <= 4 * 1024 * 1024)
                self.observe(json.loads(line))
        except Exception as error:
            if not self.stopping:
                self.error = type(error).__name__
                self.barrier.set()

    def check(self):
        need(self.error is None)

    def finish(self, uid):
        need(self.barrier.wait(_BARRIER_TIMEOUT))
        self.check()
        need(self.uid == uid)

    def close(self):
        self.stopping = True
        handle = getattr(self.connection, "_cold_socket", self.connection.sock)
        if handle is not None:
            try:
                handle.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        self.connection.close()
        self.thread.join(timeout=5)
        need(not self.thread.is_alive())
        self.response.close()


def _namespace_path(namespace, resource):
    return f"/api/v1/namespaces/{namespace}/{resource}"


def claim_kind(claim, project, owner):
    cm, spec, status = claim["metadata"], claim["spec"], claim["status"]
    tags, name = cm.get("labels", {}), cm["name"]
    ownership = {
        PART: "max-app",
        MANAGED: "omnia-orchestrator",
        PROJECT: project,
        OWNER: owner,
    }
    need(
        not cm.get("deletionTimestamp")
        and cm["namespace"] == "app-" + project
        and all(tags.get(key) == value for key, value in ownership.items())
        and status["phase"] == "Bound"
        and spec.get("volumeMode", "Filesystem") == "Filesystem"
        and spec["accessModes"] == ["ReadWriteOnce"]
        and spec["storageClassName"] == "local-path"
    )
    uuid(cm["uid"])
    if name in DATABASES:
        kind = DATABASES[name]
        need(tags.get(COMPONENT) == kind + "-postgres")
        return kind
    role = tags.get("omnia.volume-kind")
    need(tags.get(COMPONENT) == "app" and role in {"code", "data"})
    if role == "code":
        release = uuid(tags.get("omnia.release-id"))
        need(re.fullmatch(r"code-" + release[:8] + r"-[0-9a-f]{8}", name))
    else:
        need(re.fullmatch(r"data-[0-9a-f]{8}", name))
    return "files"


def cold_census(api, authority, *, expected_pod=None, uid=None):
    project, owner = uuid(authority["project_id"]), uuid(authority["owner_id"])
    namespace = f"app-{project}"
    ns = api.request("GET", f"/api/v1/namespaces/{namespace}")
    meta, labels = ns["metadata"], ns["metadata"].get("labels", {})
    need(
        meta["name"] == namespace
        and not meta.get("deletionTimestamp")
        and labels.get("omnia.retired") == "true"
    )
    ownership = {PART: "max-app", MANAGED: "omnia-orchestrator", PROJECT: project}
    need(
        all(labels.get(key) == value for key, value in ownership.items())
        or all(key not in labels for key in ownership)
    )
    pods = api.request("GET", _namespace_path(namespace, "pods"))
    version = pods["metadata"]["resourceVersion"]
    need(type(version) is str and version)
    if expected_pod is None:
        need(not pods["items"])
    else:
        need(len(pods["items"]) == 1)
        own_pod(pods["items"][0], expected_pod, uid)
    for group, kind in CONTROLLERS:
        need(
            not api.request("GET", f"/apis/{group}/namespaces/{namespace}/{kind}")[
                "items"
            ]
        )
    claims = api.request("GET", _namespace_path(namespace, "persistentvolumeclaims"))[
        "items"
    ]
    volumes, seen, nodes = [], set(), set()
    for claim in claims:
        cm, spec = claim["metadata"], claim["spec"]
        tags, name, claim_uid = cm.get("labels", {}), cm["name"], uuid(cm["uid"])
        need(
            name not in seen
            and not cm.get("deletionTimestamp")
            and cm["namespace"] == namespace
        )
        seen.add(name)
        kind = claim_kind(claim, project, owner)
        pv_name = spec["volumeName"]
        need(re.fullmatch(r"pvc-[0-9a-f-]{36}", pv_name))
        pv = api.request("GET", f"/api/v1/persistentvolumes/{pv_name}")
        pm, ps = pv["metadata"], pv["spec"]
        ref = ps["claimRef"]
        need(
            not pm.get("deletionTimestamp")
            and pm["name"] == pv_name
            and ref["namespace"] == namespace
            and ref["name"] == name
            and ref["uid"] == claim_uid
            and pv["status"]["phase"] == "Bound"
            and ps.get("volumeMode", "Filesystem") == "Filesystem"
            and ps["accessModes"] == ["ReadWriteOnce"]
            and ps["storageClassName"] == "local-path"
        )
        need(
            ps.get("local", {})
            .get("path", "")
            .startswith("/var/lib/rancher/k3s/storage/")
        )
        terms = ps["nodeAffinity"]["required"]["nodeSelectorTerms"]
        need(len(terms) == 1 and len(terms[0]["matchExpressions"]) == 1)
        expression = terms[0]["matchExpressions"][0]
        need(
            expression["key"] == "kubernetes.io/hostname"
            and expression["operator"] == "In"
            and len(expression["values"]) == 1
        )
        nodes.add(expression["values"][0])
        volumes.append(
            {
                "name": name,
                "uid": claim_uid,
                "kind": kind,
                "pv_name": pv_name,
                "pv_uid": uuid(pm["uid"]),
                "binding_sha256": digest(
                    {"claim_spec": spec, "claim_labels": tags, "pv_spec": ps}
                ),
            }
        )
    need(
        {row["name"] for row in volumes if row["kind"] in {"core", "project"}}
        == set(DATABASES)
        and len(nodes) == 1
    )
    return {
        "namespace": namespace,
        "namespace_uid": uuid(meta["uid"]),
        "project_id": project,
        "owner_id": owner,
        "node": next(iter(nodes)),
        "namespace_labels_sha256": digest(labels),
        "pod_resource_version": version,
        "volumes": sorted(volumes, key=lambda row: row["name"]),
    }


def binding(plan):
    return {key: value for key, value in plan.items() if key != "pod_resource_version"}


def filesystem_manifest(path):
    """Validate paths/links before extraction and hash every regular file's bytes."""
    rows, seen, links = [], {}, set()
    with tarfile.open(path, "r:") as archive:
        for member in archive:
            need(len(rows) < 200_000 and len(member.name) <= 4096)
            name = posixpath.normpath(member.name)
            need(
                not member.name.startswith("/")
                and name != ".."
                and not name.startswith("../")
                and name not in seen
                and (
                    member.isfile()
                    or member.isdir()
                    or member.issym()
                    or member.islnk()
                )
            )
            ancestor = posixpath.dirname(name)
            while ancestor:
                need(ancestor not in links)
                ancestor = posixpath.dirname(ancestor)
            kind = (
                "file"
                if member.isfile()
                else "directory"
                if member.isdir()
                else "symlink"
                if member.issym()
                else "hardlink"
            )
            if kind in {"symlink", "hardlink"}:
                links.add(name)
                target = posixpath.normpath(
                    posixpath.join(posixpath.dirname(name), member.linkname)
                    if member.issym()
                    else member.linkname
                )
                need(
                    not member.linkname.startswith("/")
                    and target != ".."
                    and not target.startswith("../")
                )
            sha = None
            if member.isfile():
                data = archive.extractfile(member)
                need(data is not None)
                hasher = hashlib.sha256()
                while chunk := data.read(1024 * 1024):
                    hasher.update(chunk)
                sha = hasher.hexdigest()
            seen[name] = kind
            rows.append(
                {
                    "name": name,
                    "kind": kind,
                    "size": member.size,
                    "mode": member.mode,
                    "uid": member.uid,
                    "gid": member.gid,
                    "link": member.linkname,
                    "sha256": sha,
                }
            )
    # A later link must not turn a previously seen parent into an escape either.
    for name in seen:
        ancestor = posixpath.dirname(name)
        while ancestor:
            need(ancestor not in links)
            ancestor = posixpath.dirname(ancestor)
    by_name = {row["name"]: row for row in rows}
    for row in rows:
        if row["kind"] == "hardlink":
            target, visited = row, set()
            while target["kind"] == "hardlink":
                need(target["name"] not in visited)
                visited.add(target["name"])
                key = posixpath.normpath(target["link"])
                need(key in by_name)
                target = by_name[key]
            need(target["kind"] == "file")
            row.update(
                kind="file", size=target["size"], sha256=target["sha256"], link=""
            )
    need(rows)
    return {
        "entries": len(rows),
        "filesystem_sha256": digest(sorted(rows, key=lambda row: row["name"])),
    }


def cold_file(namespace_uid, pvc_uid):
    return f"cold-{uuid(namespace_uid)}-{uuid(pvc_uid)}.tar"


def collect_one(
    cells,
    runner,
    spec,
    authority,
    out,
    *,
    expected_inventory=None,
    api=None,
    watch_factory=PodWatch,
):
    if api is None:
        with KubeProxy(spec["kubectl"], spec["context"], out) as client:
            return collect_one(
                cells,
                runner,
                spec,
                authority,
                out,
                api=client,
                expected_inventory=expected_inventory,
            )
    plan = cold_census(api, authority)
    if expected_inventory is not None:
        need(
            all(
                plan.get(key) == value
                for key, value in expected_inventory.items()
                if key != "volumes"
            )
        )
        need(
            [
                {key: row[key] for key in ("name", "uid", "kind")}
                for row in plan["volumes"]
            ]
            == expected_inventory["volumes"]
        )
    name, intent = "omnia-backup-cold-" + secrets.token_hex(12), secrets.token_hex(16)
    expected = helper_pod(plan, name, intent, spec["helper_image"])
    path = _namespace_path(plan["namespace"], "pods")
    watch, helper_uid, attempted, cleaned = None, None, False, False
    artifacts = []
    cells._write_private_json(
        out / (name + "-intent.json"),
        {
            "namespace": plan["namespace"],
            "namespace_uid": plan["namespace_uid"],
            "name": name,
            "intent": intent,
            "plan_sha256": digest(plan),
            "plan": binding(plan),
            "authority": authority,
            "expected_pod": expected,
            "producer_sha256": spec.get("producer_sha256", {}),
        },
    )
    try:
        watch = watch_factory(api, expected, plan["pod_resource_version"])
        attempted = True
        created = api.request("POST", path, expected)
        helper_uid = own_pod(created, expected)
        cells._write_private_json(
            out / (name + "-created.json"), {"name": name, "uid": helper_uid}
        )
        deadline = time.monotonic() + 120
        while True:
            watch.check()
            current = api.request("GET", path + "/" + name)
            own_pod(current, expected, helper_uid)
            states = current.get("status", {}).get("containerStatuses", [])
            if len(states) == 1 and states[0].get("ready") is True:
                need(
                    "running" in states[0].get("state", {})
                    and states[0]
                    .get("imageID", "")
                    .endswith(spec["helper_image"].split("@", 1)[1])
                )
                break
            need(time.monotonic() < deadline)
            time.sleep(0.25)
        need(
            binding(cold_census(api, authority, expected_pod=expected, uid=helper_uid))
            == binding(plan)
        )
        for index, volume in enumerate(plan["volumes"]):
            watch.check()
            filename = cold_file(plan["namespace_uid"], volume["uid"])
            nonce = secrets.token_hex(16)
            result = runner.run_acknowledged_to_file(
                [
                    "--context",
                    spec["context"],
                    "exec",
                    "--stdin",
                    "--namespace",
                    plan["namespace"],
                    name,
                    "--container",
                    "archive",
                    "--",
                    "sh",
                    "-ec",
                    _TAR_STREAM_SCRIPT,
                    "cold-tar",
                    f"/source/{index}",
                    nonce,
                ],
                path=out / filename,
                timeout=1800,
                nonce=nonce,
            )
            need(result.ok and result.size_bytes > 0)
            artifacts.append(
                {
                    **volume,
                    "file": filename,
                    "bytes": result.size_bytes,
                    "sha256": result.sha256,
                    **filesystem_manifest(out / filename),
                }
            )
        current = api.request("GET", path + "/" + name)
        own_pod(current, expected, helper_uid)
        api.request(
            "PATCH",
            path + "/" + name,
            {
                "metadata": {
                    "uid": helper_uid,
                    "resourceVersion": current["metadata"]["resourceVersion"],
                    "annotations": {BARRIER: intent},
                }
            },
            patch=True,
        )
        watch.finish(helper_uid)
        need(
            binding(cold_census(api, authority, expected_pod=expected, uid=helper_uid))
            == binding(plan)
        )
        watch.check()
    finally:
        try:
            if watch is not None:
                watch.close()
        finally:
            if attempted:
                current = api.request("GET", path + "/" + name, missing=True)
                if current is not None:
                    actual_uid = own_pod(current, expected, helper_uid)
                    api.request(
                        "DELETE",
                        path + "/" + name,
                        {
                            "apiVersion": "v1",
                            "kind": "DeleteOptions",
                            "gracePeriodSeconds": 0,
                            "preconditions": {"uid": actual_uid},
                        },
                    )
                deadline = time.monotonic() + _CLEANUP_TIMEOUT
                while api.request("GET", path + "/" + name, missing=True) is not None:
                    need(time.monotonic() < deadline)
                    time.sleep(0.25)
                cleaned = True
    need(cleaned and helper_uid is not None)
    need(binding(cold_census(api, authority)) == binding(plan))
    cells._write_private_json(
        out / (name + "-cleaned.json"),
        {"name": name, "uid": helper_uid, "cleanup_confirmed": True},
    )
    return {
        **binding(plan),
        "authority": authority,
        "volumes": artifacts,
        "proof": {
            "helper_name": name,
            "helper_uid": helper_uid,
            "watch_start": plan["pod_resource_version"],
            "barrier": intent,
            "cleanup_confirmed": True,
        },
    }


def validate(cells, directory, entries):
    need(type(entries) is list)
    seen = set()
    for entry in entries:
        need(
            entry["proof"]["cleanup_confirmed"] is True
            and entry["proof"]["watch_start"]
            and entry["proof"]["barrier"]
            and uuid(entry["proof"]["helper_uid"])
        )
        need(
            entry["project_id"] == entry["authority"]["project_id"]
            and entry["owner_id"] == entry["authority"]["owner_id"]
        )
        for volume in entry["volumes"]:
            name = cold_file(entry["namespace_uid"], volume["uid"])
            need(volume["file"] == name and name not in seen)
            seen.add(name)
            path = directory / name
            need(path.is_file() and not path.is_symlink())
            sha, size = cells._file_digest(path)
            need(size > 0 and sha == volume["sha256"] and size == volume["bytes"])
            tree = filesystem_manifest(path)
            need(all(volume.get(key) == value for key, value in tree.items()))


def coverage(manifest):
    obligations = manifest["runtime_census"].get("cold_retained", [])
    entries = manifest.get("cold_retained", [])
    need(type(entries) is list and type(obligations) is list)
    expected, actual = {}, {}
    for row in obligations:
        name = row["namespace"]
        need(name == "app-" + uuid(row["project_id"]) and name not in expected)
        uuid(row["namespace_uid"])
        uuid(row["owner_id"])
        expected[name] = row
    for row in entries:
        name = row["namespace"]
        need(name in expected and name not in actual)
        obligation = expected[name]
        need(
            all(
                row.get(key) == value
                for key, value in obligation.items()
                if key != "volumes"
            )
        )
        volumes = [
            {key: item[key] for key in ("name", "uid", "kind")}
            for item in row["volumes"]
        ]
        need(
            volumes == obligation["volumes"]
            and len({item["uid"] for item in volumes}) == len(volumes)
        )
        need(
            {item["name"] for item in volumes if item["kind"] in {"core", "project"}}
            == set(DATABASES)
        )
        for item in volumes:
            need(item["kind"] == DATABASES.get(item["name"], "files"))
        authority = row["authority"]
        need(
            authority["project_id"] == row["project_id"]
            and authority["owner_id"] == row["owner_id"]
        )
        uuid(authority["workspace_id"])
        need(
            re.fullmatch(r"[0-9a-f]{64}", authority["journal_sha256"])
            and type(authority["lock_inode"]) is int
            and authority["lock_inode"] > 0
            and type(authority["lock_owner"]) is int
            and authority["lock_owner"] >= 0
        )
        actual[name] = volumes
    need(set(actual) == set(expected))
    return {
        "namespaces": sorted(actual),
        "pvcs": sum(len(rows) for rows in actual.values()),
        "database_clusters": sum(
            row["kind"] in {"core", "project"}
            for rows in actual.values()
            for row in rows
        ),
        "file_volumes": sum(
            row["kind"] == "files" for rows in actual.values() for row in rows
        ),
    }


def archived_publication(archive, project):
    """A retirement authority must have one bounded, regular journal member."""
    name = f"state/cell-publications/{uuid(project)}/publication.json"
    matched = [member for member in archive.getmembers() if member.name == name]
    need(
        len(matched) == 1
        and matched[0].isfile()
        and 0 < matched[0].size <= 4 * 1024 * 1024
    )
    stream = archive.extractfile(matched[0])
    need(stream is not None)
    with stream:
        payload = stream.read(4 * 1024 * 1024 + 1)
    need(len(payload) == matched[0].size)
    return payload


def verify_authorities(state_archive, entries):
    with tarfile.open(state_archive, "r:gz") as archive:
        for entry in entries:
            authority = entry["authority"]
            payload = archived_publication(archive, entry["project_id"])
            need(hashlib.sha256(payload).hexdigest() == authority["journal_sha256"])
            record = json.loads(payload)
            release = (
                record.get("active_release") or record.get("prepared_release") or {}
            )
            need(
                record.get("disabled") is True
                and record.get("deletion_completed") is True
                and record.get("project_id") == entry["project_id"]
                and record.get("owner_id") == entry["owner_id"]
                and record.get("production_workspace_id") == authority["workspace_id"]
                and release.get("placement", {}).get("backend") == "kubernetes"
                and release.get("placement", {}).get("namespace") == entry["namespace"]
            )


def _scratch_backup(cells, directory, image, runner):
    """Administrative transport only for the verifier's disposable PGDATA copy."""

    class CopiedScratchBackup(cells.CellBackup):
        def helper_flags(self, name, *, read_caps=False, write_caps=False):
            flags = super().helper_flags(
                name, read_caps=read_caps, write_caps=write_caps
            )
            # Only the disposable write helper must retain archived SGID bits
            # while tar restores owners/groups outside the root user's groups.
            # Reader helpers and the production collector keep the base flags.
            return flags + (["--cap-add", "FSETID"] if write_caps else [])

        @staticmethod
        def project_admin_connection():
            # The copied server already uses our private trust socket/HBA. Keep
            # startup defenses against retained role defaults without importing
            # production controller dependencies or changing roles/credentials.
            return cells.CellBackup.scratch_connection(), {
                "PGOPTIONS": _SCRATCH_ADMIN_PGOPTIONS
            }

    return CopiedScratchBackup(
        runner=runner,
        out_dir=directory,
        postgres_image=image,
        helper_image=image,
        redactor=cells.SecretRedactor(),
    )


def verify(cells, directory, entries, image, runner):
    validate(cells, directory, entries)
    for entry in entries:
        for volume in entry["volumes"]:
            pool = cells.ScratchPool(runner)
            try:
                copied = pool.volume_name()
                need(
                    runner.run(
                        ["volume", "create", "--label", cells._SCRATCH_LABEL, copied],
                        timeout=60,
                    ).ok
                )
                backup = _scratch_backup(cells, directory, image, runner)
                source = directory / volume["file"]
                restored = runner.run(
                    [
                        "run",
                        "--interactive",
                        *backup.helper_flags(pool.container_name(), write_caps=True),
                        "--volume",
                        f"{copied}:/target",
                        image,
                        "tar",
                        "--numeric-owner",
                        "-xf",
                        "-",
                        "-C",
                        "/target",
                    ],
                    stdin_path=source,
                    timeout=1800,
                )
                need(restored.ok)
                with tempfile.TemporaryDirectory(prefix="cold-roundtrip-") as temporary:
                    os.chmod(temporary, 0o700)
                    tar_path = Path(temporary) / "restored.tar"
                    result = runner.run_to_file(
                        [
                            "run",
                            *backup.helper_flags(pool.container_name(), read_caps=True),
                            "--volume",
                            f"{copied}:/source:ro",
                            image,
                            "tar",
                            "-cf",
                            "-",
                            "-C",
                            "/source",
                            ".",
                        ],
                        path=tar_path,
                        timeout=1800,
                    )
                    need(
                        result.ok
                        and filesystem_manifest(tar_path) == filesystem_manifest(source)
                    )
                    if volume["kind"] in {"core", "project"}:
                        sql = Path(temporary) / "cluster.sql"
                        target = cells.DatabaseTarget(
                            kind="project",
                            volume=copied,
                            container="",
                            source_dir="/source/pgdata"
                            if volume["kind"] == "core"
                            else "/source",
                            password=None,
                            project_role_protocol=1,
                        )
                        outcome = backup._scratch_attempt(target, sql, pool)
                        need(outcome is not None and outcome.status == "ok")
                        need(
                            cells._restore_entry(
                                backup, sql, "project", cells._outcome_payload(outcome)
                            )["status"]
                            == "ok"
                        )
            finally:
                pool.release()


@contextmanager
def publication_locks(cells, state_root, state_archive):
    """Hold existing canonical flock inodes without creating/chowning/truncating."""
    import fcntl

    records, opened = [], []
    try:
        for project, payload in cells._publication_records(state_root):
            if not (
                payload.get("disabled") is True
                and payload.get("deletion_completed") is True
            ):
                continue
            release = (
                payload.get("active_release") or payload.get("prepared_release") or {}
            )
            if release.get("placement", {}).get("backend") != "kubernetes":
                continue
            uuid(project)
            need(
                payload.get("project_id") == project
                and release["placement"].get("namespace") == "app-" + project
            )
            path = (
                state_root / "cell-publications" / "locks" / f"workspace-{project}.lock"
            )
            fd = os.open(path, os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC)
            opened.append((fd, path, os.fstat(fd)))
            info = os.fstat(fd)
            need(stat.S_ISREG(info.st_mode) and stat.S_IMODE(info.st_mode) == 0o600)
            deadline = time.monotonic() + 60
            while True:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    need(time.monotonic() < deadline)
                    time.sleep(0.1)
            journal = state_root / "cell-publications" / project / "publication.json"
            current = journal.read_bytes()
            need(
                json.loads(current) == payload and journal.stat().st_uid == info.st_uid
            )
            with tarfile.open(state_archive, "r:gz") as archive:
                need(archived_publication(archive, project) == current)
            records.append(
                {
                    "project_id": project,
                    "owner_id": uuid(payload["owner_id"]),
                    "workspace_id": uuid(payload["production_workspace_id"]),
                    "journal_sha256": hashlib.sha256(current).hexdigest(),
                    "lock_inode": info.st_ino,
                    "lock_owner": info.st_uid,
                }
            )
        yield records
        for record in records:
            journal = (
                state_root
                / "cell-publications"
                / record["project_id"]
                / "publication.json"
            )
            need(
                hashlib.sha256(journal.read_bytes()).hexdigest()
                == record["journal_sha256"]
            )
    finally:
        try:
            for _fd, path, before in opened:
                after = path.lstat()
                need(
                    (
                        after.st_dev,
                        after.st_ino,
                        after.st_uid,
                        after.st_gid,
                        after.st_mode,
                    )
                    == (
                        before.st_dev,
                        before.st_ino,
                        before.st_uid,
                        before.st_gid,
                        before.st_mode,
                    )
                )
        finally:
            for fd, _, _ in reversed(opened):
                os.close(fd)
