"""Registry maintenance fences every writer before deleting allocated storage."""

from __future__ import annotations

import importlib
import json
import shutil
import subprocess
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

OWNER = "yleum.io/registry-maintenance-owner"
READ_ONLY = "REGISTRY_STORAGE_MAINTENANCE_READONLY_ENABLED"
PURGING = "REGISTRY_STORAGE_MAINTENANCE_UPLOADPURGING_ENABLED"
PROJECT = "76e94c8e-006c-435c-9a8e-923a564ac679"
REPOSITORY = f"max-app/{PROJECT}"
DIGEST = "sha256:" + "a" * 64
AUTHORIZATION = "Basic dXNlcjpzdXBlci1zZWNyZXQ="


def deployment() -> dict[str, Any]:
    return {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": {
            "name": "registry",
            "uid": "deployment-1",
            "resourceVersion": "10",
            "generation": 3,
            "annotations": {"external": "preserve"},
        },
        "spec": {
            "replicas": 1,
            "strategy": {"type": "Recreate"},
            "selector": {"matchLabels": {"app": "registry"}},
            "template": {
                "metadata": {"labels": {"app": "registry"}},
                "spec": {
                    "containers": [
                        {
                            "name": "registry",
                            "image": "registry:2",
                            "env": [
                                {"name": "REGISTRY_AUTH", "value": "htpasswd"},
                                {"name": "REGISTRY_AUTH_HTPASSWD_REALM", "value": "MAX Registry"},
                                {"name": "REGISTRY_AUTH_HTPASSWD_PATH", "value": "/auth/htpasswd"},
                                {"name": "REGISTRY_STORAGE_DELETE_ENABLED", "value": "true"},
                                {
                                    "name": "REGISTRY_HTTP_SECRET",
                                    "valueFrom": {
                                        "secretKeyRef": {"name": "registry-http", "key": "secret"}
                                    },
                                },
                            ],
                            "volumeMounts": [
                                {"name": "data", "mountPath": "/var/lib/registry"},
                                {"name": "auth", "mountPath": "/auth", "readOnly": True},
                            ],
                            "readinessProbe": {"httpGet": {"path": "/", "port": 5000}},
                        }
                    ],
                    "volumes": [
                        {"name": "data", "persistentVolumeClaim": {"claimName": "registry-data"}},
                        {"name": "auth", "secret": {"secretName": "registry-auth"}},
                    ],
                },
            },
        },
        "status": {
            "observedGeneration": 3,
            "replicas": 1,
            "readyReplicas": 1,
            "updatedReplicas": 1,
            "availableReplicas": 1,
        },
    }


def pod(source: dict[str, Any], uid: str) -> dict[str, Any]:
    template = deepcopy(source["spec"]["template"])
    return {
        "apiVersion": "v1",
        "kind": "Pod",
        "metadata": {**template["metadata"], "name": f"registry-{uid}", "uid": uid},
        "spec": template["spec"],
        "status": {
            "phase": "Running",
            "conditions": [{"type": "Ready", "status": "True"}],
            "containerStatuses": [{"name": "registry", "ready": True, "state": {"running": {}}}],
        },
    }


def env(source: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        item["name"]: item for item in source["spec"]["template"]["spec"]["containers"][0]["env"]
    }


def _pointer(source: Any, pointer: str) -> tuple[Any, str]:
    parts = [item.replace("~1", "/").replace("~0", "~") for item in pointer.split("/")[1:]]
    for part in parts[:-1]:
        source = source[int(part)] if isinstance(source, list) else source[part]
    return source, parts[-1]


class FakeApi:
    """An in-memory API server: implements JSON Patch CAS and rollout identity."""

    def __init__(self) -> None:
        self.deployment = deployment()
        self.pods = [pod(self.deployment, "old")]
        self.patches: list[list[dict[str, Any]]] = []
        self.commands: list[tuple[list[str], str | None]] = []
        self.keep_old = False
        self.ready = True
        self.patch_race: str | None = None
        self.writer: str | None = None
        self.writer_pid = 71
        self.fail_action: str | None = None
        self.unknown_writer = False
        self.no_nc = False
        self.bytes_kib = 200
        self.deleted: list[str] = []
        self.gc_runs = 0
        self.journal: Path | None = None
        self.config = (
            "version: 0.1\nstorage:\n  filesystem:\n    rootdirectory: /var/lib/registry\n"
            "  cache:\n    blobdescriptor: inmemory\nhttp:\n  addr: :5000\n"
        )

    def read_deployment(self, namespace: str, name: str) -> dict[str, Any]:
        assert (namespace, name) == ("registry", "registry")
        return deepcopy(self.deployment)

    def list_pods(self, namespace: str) -> list[dict[str, Any]]:
        assert namespace == "registry"
        return deepcopy(self.pods)

    def patch_deployment(
        self, namespace: str, name: str, operations: list[dict[str, Any]]
    ) -> dict[str, Any]:
        assert (namespace, name) == ("registry", "registry")
        assert self.journal is not None and self.journal.is_file(), "intent must precede patch"
        if self.patch_race:
            self.deployment["metadata"][self.patch_race] = "raced"
            self.patch_race = None
        candidate = deepcopy(self.deployment)
        for operation in operations:
            parent, key = _pointer(candidate, operation["path"])
            index: str | int = int(key) if isinstance(parent, list) and key != "-" else key
            if operation["op"] == "test":
                if parent[index] != operation["value"]:
                    raise RuntimeError("API conflict")
            elif operation["op"] == "remove":
                if isinstance(parent, list):
                    parent.pop(int(key))
                else:
                    parent.pop(key)
            elif isinstance(parent, list) and operation["op"] == "add":
                if key == "-":
                    parent.append(deepcopy(operation["value"]))
                else:
                    parent.insert(int(key), deepcopy(operation["value"]))
            else:
                parent[index] = deepcopy(operation["value"])
        self.patches.append(deepcopy(operations))
        changed_template = candidate["spec"]["template"] != self.deployment["spec"]["template"]
        candidate["metadata"]["resourceVersion"] = str(
            int(self.deployment["metadata"]["resourceVersion"]) + 1
        )
        if changed_template:
            candidate["metadata"]["generation"] += 1
            candidate["status"]["observedGeneration"] = candidate["metadata"]["generation"]
            fresh = pod(candidate, f"new-{candidate['metadata']['generation']}")
            if not self.ready:
                fresh["status"]["conditions"][0]["status"] = "False"
                fresh["status"]["containerStatuses"][0]["ready"] = False
            self.pods = [*self.pods, fresh] if self.keep_old else [fresh]
        self.deployment = candidate
        return deepcopy(candidate)

    def exec(
        self,
        namespace: str,
        name: str,
        container: str,
        command: list[str],
        *,
        stdin: str | None = None,
        timeout_seconds: float = 60,
    ) -> tuple[int, str]:
        assert namespace == "registry" and container == "registry" and timeout_seconds > 0
        assert any(item["metadata"]["name"] == name for item in self.pods)
        self.commands.append((deepcopy(command), stdin))
        if command == ["cat", "/etc/docker/registry/config.yml"]:
            return 0, self.config
        if command[0] == "nc":
            assert command == ["nc", "-w", "10", "127.0.0.1", "5001"]
            assert self.writer is not None
            assert stdin is not None
            self.deleted.append(stdin.split("\r\n", 1)[0])
            return 0, "HTTP/1.1 202 Accepted\r\nContent-Length: 0\r\n\r\n"
        action, owner = command[-2:]
        if self.fail_action == action:
            raise RuntimeError(f"transport failure containing {AUTHORIZATION}")
        if self.unknown_writer or (self.writer is not None and self.writer != owner):
            return 1, "unowned process"
        if self.no_nc:
            return 1, "nc unavailable"
        if action == "preflight":
            return (1, "writer exists") if self.writer else (0, "ok\n")
        if action in {"probe", "writer"}:
            assert env(self.deployment)[READ_ONLY]["value"] == "true"
            return 0, f"{self.writer_pid if self.writer else 0}\n"
        if action == "start":
            self.writer = owner
            return 0, f"{self.writer_pid}\n"
        if action in {"stop", "clean"}:
            self.writer = None
            return 0, "ok\n"
        if action == "du":
            return 0, f"{self.bytes_kib}\t/var/lib/registry\n"
        if action == "gc":
            assert self.writer is None, "GC must never overlap the private writer"
            self.gc_runs += 1
            self.bytes_kib = 125
            return 0, "mark and sweep\n"
        raise AssertionError(f"unexpected exec action {action}")


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


def module() -> Any:
    try:
        return importlib.import_module("yleum_orchestrator.services.registry_maintenance")
    except ModuleNotFoundError:
        pytest.fail("the registry maintenance lease is not implemented")


def lease(tmp_path: Path, api: FakeApi | None = None) -> Any:
    api = api or FakeApi()
    api.journal = tmp_path / "registry-maintenance.json"
    clock = Clock()
    return module().RegistryMaintenance(
        "unused-kubeconfig",
        api.journal,
        api=api,
        clock=clock,
        sleep=clock.sleep,
        timeout_seconds=0.2,
        poll_interval_seconds=0.1,
    )


def test_read_only_rollout_precedes_private_deletion_and_physical_gc(tmp_path: Path) -> None:
    api = FakeApi()
    original = deepcopy(api.deployment["spec"]["template"])
    with lease(tmp_path, api) as maintenance:
        assert len(api.pods) == 1 and api.pods[0]["metadata"]["uid"] != "old"
        assert env(api.deployment)[READ_ONLY]["value"] == "true"
        assert env(api.deployment)[PURGING]["value"] == "false"
        maintenance.start_private_writer()
        assert maintenance.delete_manifest(REPOSITORY, DIGEST, AUTHORIZATION) == 202
        with pytest.raises(RuntimeError, match="writer"):
            maintenance.garbage_collect()
        assert api.gc_runs == 0
        maintenance.stop_private_writer()
        assert maintenance.garbage_collect() == {
            "bytes_before": 204800,
            "bytes_after": 128000,
            "bytes_freed": 76800,
        }
    assert api.deployment["spec"]["template"] == original
    assert OWNER not in api.deployment["metadata"]["annotations"]
    assert api.deployment["metadata"]["annotations"]["external"] == "preserve"
    assert api.writer is None and not api.journal.exists()


@pytest.mark.parametrize("race", ["uid", "resourceVersion"])
def test_deployment_identity_race_cannot_acquire_maintenance(tmp_path: Path, race: str) -> None:
    api = FakeApi()
    api.patch_race = race
    with pytest.raises(RuntimeError):
        with lease(tmp_path, api):
            pytest.fail("a conflicting Deployment must never grant a deletion lease")
    assert not api.patches and not api.deleted and api.gc_runs == 0


@pytest.mark.parametrize("problem", ["old-pod", "not-ready", "unowned-writer", "no-nc"])
def test_rollout_and_runtime_fail_closed(tmp_path: Path, problem: str) -> None:
    api = FakeApi()
    api.keep_old = problem == "old-pod"
    api.ready = problem != "not-ready"
    api.unknown_writer = problem == "unowned-writer"
    api.no_nc = problem == "no-nc"
    with pytest.raises(RuntimeError):
        with lease(tmp_path, api):
            pytest.fail("an incomplete registry fence must never grant a lease")
    assert not api.deleted and api.gc_runs == 0


@pytest.mark.parametrize("problem", ["rolling", "replicas", "storage", "auth", "sidecar"])
def test_unsupported_deployment_is_rejected_before_mutation(tmp_path: Path, problem: str) -> None:
    api = FakeApi()
    template = api.deployment["spec"]["template"]["spec"]
    if problem == "rolling":
        api.deployment["spec"]["strategy"]["type"] = "RollingUpdate"
    elif problem == "replicas":
        api.deployment["spec"]["replicas"] = 2
    elif problem == "storage":
        template["volumes"][0] = {"name": "data", "hostPath": {"path": "/unknown"}}
    elif problem == "auth":
        template["containers"][0]["env"][0]["value"] = ""
    else:
        template["containers"].append({"name": "sidecar", "image": "registry:2"})
    with pytest.raises(RuntimeError):
        with lease(tmp_path, api):
            pytest.fail("unsupported storage or authentication must not become writable")
    assert not api.patches


def test_configuration_file_cannot_select_another_storage_driver(tmp_path: Path) -> None:
    api = FakeApi()
    api.config = "version: 0.1\nstorage:\n  s3:\n    bucket: customer-data\nhttp:\n  addr: :5000\n"
    with pytest.raises(RuntimeError):
        with lease(tmp_path, api):
            pytest.fail("GC must use only the validated filesystem configuration")
    assert not api.patches


@pytest.mark.parametrize("action", ["private-writer", "gc"])
def test_changed_configuration_cannot_select_another_storage_during_the_lease(
    tmp_path: Path,
    action: str,
) -> None:
    api = FakeApi()
    with lease(tmp_path, api) as maintenance:
        api.config = "version: 0.1\nstorage:\n  filesystem:\n    rootdirectory: /business-data\n"
        with pytest.raises(RuntimeError):
            if action == "gc":
                maintenance.garbage_collect()
            else:
                maintenance.start_private_writer()
    assert api.gc_runs == 0 and api.writer is None


def test_existing_owner_and_existing_journal_are_not_stolen(tmp_path: Path) -> None:
    api = FakeApi()
    api.deployment["metadata"]["annotations"][OWNER] = "another-owner"
    with pytest.raises(RuntimeError):
        with lease(tmp_path, api):
            pytest.fail("owned maintenance cannot be taken over")
    assert not api.patches
    api.deployment["metadata"]["annotations"].pop(OWNER)
    api.journal.write_text('{"owner":"another-owner"}', encoding="utf-8")
    with pytest.raises(RuntimeError):
        with lease(tmp_path, api):
            pytest.fail("an existing recovery journal must not be overwritten")
    assert json.loads(api.journal.read_text())["owner"] == "another-owner"


def test_unrelated_concurrent_changes_and_original_manual_read_only_survive_restore(
    tmp_path: Path,
) -> None:
    api = FakeApi()
    api.deployment["spec"]["template"]["spec"]["containers"][0]["env"].append(
        {"name": READ_ONLY, "value": "true"}
    )
    api.pods = [pod(api.deployment, "old")]
    with lease(tmp_path, api):
        api.deployment["metadata"]["annotations"]["concurrent"] = "preserved"
        api.deployment["metadata"]["resourceVersion"] = "99"
    assert env(api.deployment)[READ_ONLY]["value"] == "true"
    assert api.deployment["metadata"]["annotations"]["concurrent"] == "preserved"
    assert PURGING not in env(api.deployment)


def test_body_failure_stops_writer_before_restoring_writes(tmp_path: Path) -> None:
    api = FakeApi()
    with pytest.raises(ValueError, match="coordinator failed"):
        with lease(tmp_path, api) as maintenance:
            maintenance.start_private_writer()
            raise ValueError("coordinator failed")
    assert api.writer is None
    assert READ_ONLY not in env(api.deployment)
    assert not api.journal.exists()


def test_failed_writer_stop_retains_read_only_and_recoverable_journal(tmp_path: Path) -> None:
    api = FakeApi()
    with pytest.raises(RuntimeError):
        with lease(tmp_path, api) as maintenance:
            maintenance.start_private_writer()
            api.fail_action = "stop"
    assert env(api.deployment)[READ_ONLY]["value"] == "true"
    assert api.journal.is_file() and api.writer is not None
    assert api.gc_runs == 0


def test_crash_recovery_stops_only_the_owned_private_process_and_restores(tmp_path: Path) -> None:
    api = FakeApi()
    maintenance = lease(tmp_path, api)
    maintenance.__enter__()
    maintenance.start_private_writer()
    assert module().recover("unused-kubeconfig", api.journal, api=api) is True
    assert api.writer is None and READ_ONLY not in env(api.deployment)
    assert not api.journal.exists()
    assert module().recover("unused-kubeconfig", api.journal, api=api) is False


@pytest.mark.parametrize("race", ["uid", "owner"])
def test_recovery_never_mutates_a_replaced_or_unowned_deployment(
    tmp_path: Path,
    race: str,
) -> None:
    api = FakeApi()
    maintenance = lease(tmp_path, api)
    maintenance.__enter__()
    maintenance.start_private_writer()
    patches = len(api.patches)
    if race == "uid":
        api.deployment["metadata"]["uid"] = "replacement"
    else:
        api.deployment["metadata"]["annotations"][OWNER] = "another-owner"
    with pytest.raises(RuntimeError):
        module().recover("unused-kubeconfig", api.journal, api=api)
    assert len(api.patches) == patches and api.journal.exists()
    assert api.writer is not None


def test_same_pod_and_uid_are_rechecked_before_each_delete(tmp_path: Path) -> None:
    api = FakeApi()
    maintenance = lease(tmp_path, api)
    with maintenance:
        maintenance.start_private_writer()
        api.pods[0]["metadata"]["uid"] = "replacement-pod"
        with pytest.raises(RuntimeError):
            maintenance.delete_manifest(REPOSITORY, DIGEST, AUTHORIZATION)
        api.pods[0]["metadata"]["uid"] = maintenance.pod_uid
    assert not api.deleted


@pytest.mark.parametrize("change", ["pvc", "config-mount", "init-container"])
def test_a_mutated_live_pod_cannot_authorize_a_manifest_delete(
    tmp_path: Path,
    change: str,
) -> None:
    api = FakeApi()
    maintenance = lease(tmp_path, api)
    with maintenance:
        maintenance.start_private_writer()
        saved = deepcopy(api.pods)
        live = api.pods[0]["spec"]
        if change == "pvc":
            live["volumes"][0]["persistentVolumeClaim"]["claimName"] = "another-volume"
        elif change == "config-mount":
            live["containers"][0]["volumeMounts"].append(
                {"name": "unknown", "mountPath": "/etc/docker/registry"}
            )
        else:
            live["initContainers"] = [{"name": "writer", "image": "registry:2"}]
        with pytest.raises(RuntimeError):
            maintenance.delete_manifest(REPOSITORY, DIGEST, AUTHORIZATION)
        api.pods = saved
    assert not api.deleted


def test_intent_journal_before_an_unapplied_patch_can_be_retired_without_mutation(
    tmp_path: Path,
) -> None:
    api = FakeApi()
    maintenance = lease(tmp_path, api)
    original_patch = api.patch_deployment

    def lost_before_patch(*args: Any, **kwargs: Any) -> dict[str, Any]:
        raise KeyboardInterrupt

    api.patch_deployment = lost_before_patch
    with pytest.raises(KeyboardInterrupt):
        maintenance.__enter__()
    api.patch_deployment = original_patch
    assert api.journal.exists() and not api.patches
    assert module().recover("unused-kubeconfig", api.journal, api=api) is True
    assert not api.journal.exists() and not api.patches


def test_recovery_after_restore_patch_response_is_lost_does_not_patch_an_unowned_deployment(
    tmp_path: Path,
) -> None:
    api = FakeApi()
    maintenance = lease(tmp_path, api)
    maintenance.__enter__()
    maintenance.start_private_writer()
    original_patch = api.patch_deployment

    def lost_after_patch(*args: Any, **kwargs: Any) -> dict[str, Any]:
        original_patch(*args, **kwargs)
        raise KeyboardInterrupt

    api.patch_deployment = lost_after_patch
    with pytest.raises(KeyboardInterrupt):
        maintenance.restore()
    api.patch_deployment = original_patch
    assert api.writer is None and OWNER not in api.deployment["metadata"]["annotations"]
    patches = len(api.patches)
    assert module().recover("unused-kubeconfig", api.journal, api=api) is True
    assert len(api.patches) == patches and not api.journal.exists()


def test_recovery_retries_the_restore_patch_after_a_resource_version_race(tmp_path: Path) -> None:
    api = FakeApi()
    maintenance = lease(tmp_path, api)
    maintenance.__enter__()
    api.patch_race = "resourceVersion"
    with pytest.raises(RuntimeError):
        maintenance.restore()
    assert api.journal.exists() and OWNER in api.deployment["metadata"]["annotations"]
    api.deployment["metadata"]["resourceVersion"] = "200"
    assert module().recover("unused-kubeconfig", api.journal, api=api) is True
    assert not api.journal.exists()


def test_unknown_process_blocks_gc_even_if_it_is_not_the_tracked_private_writer(
    tmp_path: Path,
) -> None:
    api = FakeApi()
    with lease(tmp_path, api) as maintenance:
        api.unknown_writer = True
        with pytest.raises(RuntimeError):
            maintenance.garbage_collect()
        api.unknown_writer = False
    assert api.gc_runs == 0


def test_duplicate_environment_and_external_storage_overrides_never_acquire_a_lease(
    tmp_path: Path,
) -> None:
    for entry in [
        {"name": "REGISTRY_STORAGE_S3_BUCKET", "value": "business-data"},
        {"name": "REGISTRY_AUTH", "value": "htpasswd"},
    ]:
        api = FakeApi()
        api.deployment["spec"]["template"]["spec"]["containers"][0]["env"].append(entry)
        with pytest.raises(RuntimeError):
            with lease(tmp_path, api):
                pytest.fail("ambiguous environment must not authorize registry maintenance")
        assert not api.patches


@pytest.mark.parametrize("entrypoint", ["adapter", "lease", "recovery"])
@pytest.mark.parametrize("context", [None, "max-runtime"])
def test_kubeconfig_context_reaches_the_official_loader_from_every_entrypoint(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    entrypoint: str,
    context: str | None,
) -> None:
    from kubernetes import client, config

    maintenance = module()
    connection = client.ApiClient()
    captured: dict[str, Any] = {}

    def load(**kwargs: Any) -> Any:
        captured.update(kwargs)
        return connection

    monkeypatch.setattr(config, "new_client_from_config", load)
    options = {} if context is None else {"context": context}
    try:
        if entrypoint == "adapter":
            maintenance.KubernetesRegistryApi("configured-kubeconfig", **options).close()
        elif entrypoint == "lease":
            guard = maintenance.RegistryMaintenance(
                "configured-kubeconfig",
                tmp_path / "unused.json",
                **options,
            )
            guard.__exit__(None, None, None)
        else:
            api = FakeApi()
            lease(tmp_path, api).__enter__()
            # Keep the real adapter construction/loader and real recovery logic;
            # substitute only the external cluster I/O.
            for name in ("read_deployment", "patch_deployment", "list_pods", "exec"):
                monkeypatch.setattr(maintenance.KubernetesRegistryApi, name, getattr(api, name))
            assert maintenance.recover("configured-kubeconfig", api.journal, **options) is True
    finally:
        connection.close()
    assert captured["config_file"] == "configured-kubeconfig"
    assert captured.get("context") == context


def test_official_client_serialization_keeps_camel_case_and_json_patch_content_type(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from kubernetes import client, config

    real_client = client.ApiClient()
    result = client.V1Deployment(
        metadata=client.V1ObjectMeta(uid="u", resource_version="42"),
        spec=client.V1DeploymentSpec(
            selector=client.V1LabelSelector(match_labels={"app": "registry"}),
            template=client.V1PodTemplateSpec(
                spec=client.V1PodSpec(containers=[client.V1Container(name="registry")])
            ),
        ),
    )
    captured: dict[str, Any] = {}

    def call_api(path: str, method: str, **kwargs: Any) -> Any:
        captured.update({"path": path, "method": method, **kwargs})
        return result

    monkeypatch.setattr(config, "new_client_from_config", lambda **_: real_client)
    monkeypatch.setattr(real_client, "call_api", call_api)
    adapter = module().KubernetesRegistryApi("unused")
    try:
        observed = adapter.patch_deployment(
            "registry",
            "registry",
            [
                {"op": "test", "path": "/metadata/resourceVersion", "value": "42"},
            ],
        )
    finally:
        adapter.close()
    assert observed["metadata"]["resourceVersion"] == "42"
    assert observed["spec"]["selector"]["matchLabels"] == {"app": "registry"}
    assert captured["header_params"]["Content-Type"] == "application/json-patch+json"
    assert captured["body"] == [
        {"op": "test", "path": "/metadata/resourceVersion", "value": "42"},
    ]


def test_corrupt_recovery_flags_never_get_replayed_into_a_deployment(tmp_path: Path) -> None:
    api = FakeApi()
    maintenance = lease(tmp_path, api)
    maintenance.__enter__()
    payload = json.loads(api.journal.read_text())
    payload["original"][READ_ONLY] = {
        "name": READ_ONLY,
        "valueFrom": {"secretKeyRef": {"name": "unowned", "key": "flag"}},
    }
    api.journal.write_text(json.dumps(payload), encoding="utf-8")
    patches = len(api.patches)
    with pytest.raises(RuntimeError):
        module().recover("unused-kubeconfig", api.journal, api=api)
    assert len(api.patches) == patches and env(api.deployment)[READ_ONLY]["value"] == "true"


def test_official_exec_transports_authorization_over_stdin_and_discards_stderr(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from kubernetes import client, config, stream

    connection = client.ApiClient()
    captured: dict[str, Any] = {}

    class Channel:
        returncode = 0

        def __init__(self) -> None:
            self.open = True
            self.closed = False

        def write_stdin(self, value: str) -> None:
            captured["stdin"] = value

        def is_open(self) -> bool:
            return self.open

        def update(self, **_: Any) -> None:
            self.open = False

        def read_stdout(self, **_: Any) -> str:
            return "HTTP/1.1 202 Accepted\r\n\r\n"

        def read_stderr(self, **_: Any) -> str:
            return AUTHORIZATION

        def close(self) -> None:
            self.closed = True

    channel = Channel()

    def websocket(*args: Any, **kwargs: Any) -> Channel:
        captured.update(kwargs)
        return channel

    monkeypatch.setattr(config, "new_client_from_config", lambda **_: connection)
    monkeypatch.setattr(stream, "stream", websocket)
    adapter = module().KubernetesRegistryApi("unused")
    try:
        code, output = adapter.exec(
            "registry",
            "registry-pod",
            "registry",
            ["nc", "-w", "10", "127.0.0.1", "5001"],
            stdin=f"Authorization: {AUTHORIZATION}\r\n\r\n",
        )
    finally:
        adapter.close()
    assert code == 0 and "202 Accepted" in output and AUTHORIZATION not in output
    assert captured["stdin"] == f"Authorization: {AUTHORIZATION}\r\n\r\n"
    assert AUTHORIZATION not in repr(captured["command"])
    assert captured["tty"] is False and channel.closed


@pytest.mark.parametrize(
    ("repository", "digest", "authorization"),
    [
        ("platform/image", DIGEST, AUTHORIZATION),
        (REPOSITORY + "/../../platform", DIGEST, AUTHORIZATION),
        ("max-app/" + PROJECT.upper(), DIGEST, AUTHORIZATION),
        (REPOSITORY, DIGEST + "\r\nPOST /v2/", AUTHORIZATION),
        (REPOSITORY, "sha256:" + "A" * 64, AUTHORIZATION),
        (REPOSITORY, DIGEST, AUTHORIZATION + "\r\nX-Injection: yes"),
        (REPOSITORY, DIGEST, "Bearer unapproved-authentication"),
    ],
)
def test_delete_rejects_paths_digests_and_protocol_injection(
    tmp_path: Path,
    repository: str,
    digest: str,
    authorization: str,
) -> None:
    api = FakeApi()
    with lease(tmp_path, api) as maintenance:
        maintenance.start_private_writer()
        with pytest.raises(RuntimeError):
            maintenance.delete_manifest(repository, digest, authorization)
    assert not api.deleted


def test_credentials_travel_only_over_stdin_and_never_escape_errors_or_journal(
    tmp_path: Path,
) -> None:
    api = FakeApi()
    with lease(tmp_path, api) as maintenance:
        maintenance.start_private_writer()
        maintenance.delete_manifest(REPOSITORY, DIGEST, AUTHORIZATION)
        assert AUTHORIZATION not in api.journal.read_text()
        assert AUTHORIZATION not in repr(maintenance)
        nc = [(command, stdin) for command, stdin in api.commands if command[0] == "nc"]
        assert len(nc) == 1 and AUTHORIZATION in nc[0][1]
        assert all(AUTHORIZATION not in repr(command) for command, _ in api.commands)
        assert api.deleted == [f"DELETE /v2/{REPOSITORY}/manifests/{DIGEST} HTTP/1.1"]
        api.fail_action = "writer"
        with pytest.raises(RuntimeError) as error:
            maintenance.delete_manifest(REPOSITORY, DIGEST, AUTHORIZATION)
        assert AUTHORIZATION not in str(error.value)
        api.fail_action = None


def test_private_helper_and_gc_keep_authentication_storage_and_exclusive_loopback(
    tmp_path: Path,
) -> None:
    api = FakeApi()
    with lease(tmp_path, api) as maintenance:
        maintenance.start_private_writer()
        maintenance.stop_private_writer()
        maintenance.garbage_collect()
    scripts = "\n".join(command[2] for command, _ in api.commands if command[0] == "/bin/sh")
    assert "REGISTRY_HTTP_ADDR=127.0.0.1:5001" in scripts
    assert "REGISTRY_STORAGE_MAINTENANCE_READONLY_ENABLED=false" in scripts
    assert "REGISTRY_STORAGE_MAINTENANCE_UPLOADPURGING_ENABLED=false" in scripts
    assert "/bin/registry garbage-collect /etc/docker/registry/config.yml" in scripts
    assert "--delete-untagged" not in scripts
    assert "REGISTRY_AUTH=" not in scripts
    assert "kill -" in scripts and "/proc/" in scripts


@pytest.mark.parametrize("argv0", ["registry", "/bin/registry"])
def test_shell_accepts_both_official_entrypoint_argv_forms(tmp_path: Path, argv0: str) -> None:
    result = shell_probe(tmp_path, argv0=argv0)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "0"


def test_shell_treats_a_verified_owned_zombie_as_dead(tmp_path: Path) -> None:
    marker = tmp_path / "marker"
    marker.mkdir()
    for name, value in {"owner": "a" * 32, "pid": "71", "kind": "serve", "start": "991"}.items():
        (marker / name).write_text(value + "\n", newline="\n")
    dead = tmp_path / "proc" / "71"
    dead.mkdir(parents=True)
    (dead / "stat").write_text("71 (registry) Z " + "0 " * 18 + "991\n", newline="\n")
    result = shell_probe(tmp_path)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "0"


@pytest.mark.parametrize("problem", ["read-write", "unowned-process", "wildcard-listener"])
def test_shell_proof_rejects_actual_writable_or_unowned_runtime_state(
    tmp_path: Path,
    problem: str,
) -> None:
    result = shell_probe(tmp_path, problem=problem)
    assert result.returncode != 0
    assert AUTHORIZATION not in result.stdout + result.stderr


def shell_probe(
    tmp_path: Path,
    *,
    argv0: str = "/bin/registry",
    problem: str = "",
) -> subprocess.CompletedProcess[str]:
    """Run the real guard over a fixture of the kernel files it reads.

    Only /proc and the marker directory are redirected; no registry process,
    kill, network endpoint, Kubernetes cluster or production mount is used.
    """
    shell = shutil.which("sh")
    if shell is None:
        git = shutil.which("git")
        candidate = Path(git).parent.parent / "usr" / "bin" / "sh.exe" if git else Path(".")
        if candidate.is_file():
            shell = str(candidate)
    if shell is None:
        pytest.skip("a POSIX shell is required for the runtime guard fixture")
    proc = tmp_path / "proc"
    main = proc / "1"
    main.mkdir(parents=True, exist_ok=True)
    (main / "exe").touch()
    (main / "cmdline").write_bytes(f"{argv0}\0serve\0/etc/docker/registry/config.yml\0".encode())
    value = "false" if problem == "read-write" else "true"
    (main / "environ").write_bytes(f"{READ_ONLY}={value}\0{PURGING}=false\0".encode())
    net = proc / "net"
    net.mkdir(exist_ok=True)
    listener = " 0: 00000000:1389 00000000:0000 0A\n" if problem == "wildcard-listener" else ""
    (net / "tcp").write_text("sl local_address rem_address st\n" + listener, newline="\n")
    (net / "tcp6").write_text("sl local_address rem_address st\n", newline="\n")
    if problem == "unowned-process":
        (proc / "39").mkdir()
        (proc / "39" / "exe").touch()
    tools = tmp_path / "tools"
    tools.mkdir(exist_ok=True)
    stubs = {
        "readlink": "printf '/bin/registry\\n'",
        "nc": "exit 0",
        "du": "exit 99",
        "nohup": "exit 99",
    }
    for name, body in stubs.items():
        executable = tools / name
        executable.write_text("#!/bin/sh\n" + body + "\n", encoding="utf-8", newline="\n")
        executable.chmod(0o700)

    def shell_path(path: Path) -> str:
        value = path.as_posix()
        return "/" + path.drive[0].lower() + value[2:] if path.drive else value

    script = (
        module()
        ._SCRIPT.replace("/proc/", shell_path(proc) + "/")
        .replace("/tmp/yleum-registry-maintenance", shell_path(tmp_path / "marker"))
    )
    search_path = shell_path(tools) + ":" + shell_path(Path(shell).parent)
    script_file = tmp_path / "guard.sh"
    script_file.write_text(
        'PATH="$1:$PATH"; export PATH; shift\n' + script, encoding="utf-8", newline="\n"
    )
    return subprocess.run(
        [shell, "-eu", shell_path(script_file), search_path, "probe", "a" * 32],
        text=True,
        capture_output=True,
        timeout=15,
    )
