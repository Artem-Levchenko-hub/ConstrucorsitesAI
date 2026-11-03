"""Fail-closed coordination of controller roots, workload roots and registry GC.

Observation is useful on its own: dry runs start the policy's two-hour grace.
The registry fence is acquired only when an aged deletion plan is nonempty.
All mutable journals, receipts, reports and the lifetime lock live outside state.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import re
import secrets
import stat
import time
from collections.abc import Callable, Iterable
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Never, Protocol
from uuid import UUID

import httpx

from yleum_orchestrator.core.cell_resources import WorkspaceLockTimeout, WorkspaceLockUnavailable
from yleum_orchestrator.services.build_retention_inventory import (
    APPLICATION_IMAGE,
    ProtectedBuildRoots,
    collect_roots,
)
from yleum_orchestrator.services.cell_lock import WorkspaceOperationLock
from yleum_orchestrator.services.registry_maintenance import (
    OWNER_ANNOTATION,
    KubernetesRegistryApi,
    RegistryMaintenance,
    RegistryMaintenanceError,
    recover,
)
from yleum_orchestrator.services.registry_retention import (
    RegistryPlan,
    RegistryRetention,
    RegistryRetentionError,
)

_HEX = re.compile(r"[a-f0-9]{64}\Z")
_DIGEST = re.compile(r"sha256:[a-f0-9]{64}\Z")
_ARCHIVE = re.compile(r"[a-f0-9]{32}\.tar\Z")
_NAME = re.compile(r"[a-z0-9][a-z0-9-]{0,62}\Z")
_MAX_JSON = 4 * 1024 * 1024
_ROOT_FIELDS = {
    "version",
    "archive_names",
    "image_ids",
    "image_refs",
    "busy_workspaces",
    "active_operations",
    "observed_at",
    "generation",
    "nonce",
}
_WORKLOAD_KINDS = {
    "Pod",
    "PodTemplate",
    "ReplicationController",
    "Deployment",
    "StatefulSet",
    "DaemonSet",
    "ReplicaSet",
    "Job",
    "CronJob",
}
_FileIdentity = tuple[int, int, int, int, int]
_RegistryPaths = tuple[Path, Path, Path, Path, Path]


class CoordinatorError(RuntimeError):
    """Only fixed error codes cross the CLI/report boundary."""

    def __init__(self, code: str, *, skipped: bool = False) -> None:
        super().__init__(code)
        self.code, self.skipped = code, skipped


@dataclass(frozen=True, slots=True)
class RetentionConfig:
    state_root: Path
    journal_root: Path
    controllers_file: Path
    registry_url: str = field(repr=False)
    registry_username: str = field(repr=False)
    registry_password: str = field(repr=False)
    internal_token: str = field(repr=False)
    kubeconfig_path: str = field(repr=False)
    kube_context: str | None = field(default=None, repr=False)
    expected_controller_count: int = 2
    apply: bool = False
    recover_only: bool = False
    report: Path | None = None


@dataclass(frozen=True, slots=True)
class Controller:
    name: str
    url: str = field(repr=False)
    token_file: Path | None = field(default=None, repr=False)


@dataclass(frozen=True, slots=True)
class KubernetesRoots:
    image_refs: frozenset[str]
    generation: str


class KubernetesInventory(Protocol):
    def snapshot(self) -> KubernetesRoots: ...

    def registry_uid(self) -> str: ...

    async def wait_restored_ready(self, uid: str) -> None: ...


class RegistryLease(Protocol):
    def assert_read_only(self) -> None: ...

    def start_private_writer(self) -> None: ...

    def delete_manifest(self, repository: str, digest: str, authorization: str) -> int: ...

    def stop_private_writer(self) -> None: ...

    def garbage_collect(self) -> dict[str, int]: ...


@dataclass(frozen=True, slots=True, repr=False)
class CoordinatorDependencies:
    """Replace slow boundaries while retaining the real policy/lease in integration tests."""

    local_roots: Callable[[Path], ProtectedBuildRoots]
    http_client: httpx.Client
    kubernetes: KubernetesInventory
    retention_factory: Callable[[Path], RegistryRetention]
    maintenance_factory: Callable[[Path], AbstractContextManager[RegistryLease]]
    recover: Callable[[Path], bool]
    now: Callable[[], datetime] = lambda: datetime.now(UTC)
    lock: WorkspaceOperationLock | None = None


@dataclass(frozen=True, slots=True)
class _Inventory:
    controllers: tuple[tuple[str, ProtectedBuildRoots], ...]
    kubernetes: KubernetesRoots

    @property
    def protected(self) -> set[str]:
        result = set(self.kubernetes.image_refs)
        for _, roots in self.controllers:
            result.update(roots.image_ids)
            result.update(roots.image_refs)
        return result

    def signature(self) -> object:
        # observed_at and the request nonce change on every valid read.
        return (
            tuple(
                (
                    name,
                    roots.generation,
                    roots.archive_names,
                    roots.image_ids,
                    roots.image_refs,
                    roots.busy_workspaces,
                    roots.active_operations,
                )
                for name, roots in self.controllers
            ),
            self.kubernetes,
        )


@dataclass(frozen=True, slots=True)
class _PendingGc:
    deployment_uid: str
    identity: _FileIdentity


def _fail(code: str, *, skipped: bool = False) -> Never:
    raise CoordinatorError(code, skipped=skipped)


def _link(info: os.stat_result) -> bool:
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    )


def _absolute(path: Path) -> Path:
    return Path(os.path.abspath(path))


def _directory(path: Path) -> None:
    for part in (*reversed(path.parents), path):
        info = part.lstat()
        if _link(info) or not stat.S_ISDIR(info.st_mode):
            _fail("unsafe_path")


def _identity(info: os.stat_result) -> _FileIdentity:
    # Windows lstat reports creation time in ctime, while fstat may report the
    # change time. Device/inode/size/mtime remain comparable across both calls.
    changed = info.st_ctime_ns if os.name != "nt" else 0
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, changed


def _file_identity(path: Path) -> _FileIdentity | None:
    _directory(path.parent)
    try:
        info = path.lstat()
    except FileNotFoundError:
        return None
    if _link(info) or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        _fail("unsafe_path")
    return _identity(info)


def _read_file(path: Path, limit: int = _MAX_JSON, *, private: bool = False) -> bytes:
    before = _file_identity(path)
    if before is None:
        _fail("configuration_unavailable")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
    with os.fdopen(os.open(path, flags), "rb") as handle:
        info = os.fstat(handle.fileno())
        if _identity(info) != before or info.st_size > limit:
            _fail("configuration_changed")
        if private and os.name != "nt" and stat.S_IMODE(info.st_mode) & 0o077:
            _fail("unsafe_token_permissions")
        raw = handle.read(limit + 1)
        if len(raw) > limit or _identity(os.fstat(handle.fileno())) != before:
            _fail("configuration_changed")
    if _file_identity(path) != before:
        _fail("configuration_changed")
    return raw


def _unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            _fail("invalid_json")
        result[key] = value
    return result


def _invalid_constant(_value: str) -> Never:
    _fail("invalid_json")


def _decode(raw: bytes) -> Any:
    try:
        if len(raw) > _MAX_JSON:
            _fail("invalid_json")
        return json.loads(raw, object_pairs_hook=_unique, parse_constant=_invalid_constant)
    except (ValueError, UnicodeError):
        raise CoordinatorError("invalid_json") from None


def _write_json(path: Path, payload: object) -> None:
    """Atomic private writes, with no symlink traversal or replacement of raced files."""
    before = _file_identity(path)
    temporary = path.with_name(f".{path.name}.{secrets.token_hex(16)}.tmp")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(temporary, flags, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(
                (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode()
            )
            handle.flush()
            os.fsync(handle.fileno())
        if _file_identity(path) != before:
            _fail("journal_changed")
        _directory(path.parent)
        os.replace(temporary, path)
        _sync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def _sync_directory(path: Path) -> None:
    if os.name != "nt":
        directory = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory)
        finally:
            os.close(directory)


def _registry_fingerprint(config: RetentionConfig) -> str:
    # Bind recovery work to its registry without persisting configuration or credentials.
    canonical = str(httpx.URL(config.registry_url)).rstrip("/")
    return hashlib.sha256(canonical.encode()).hexdigest()


def _pending_gc(path: Path, config: RetentionConfig) -> _PendingGc | None:
    before = _file_identity(path)
    if before is None:
        return None
    payload = _decode(_read_file(path))
    if (
        not isinstance(payload, dict)
        or set(payload) != {"version", "registry_fingerprint", "deployment_uid"}
        or type(payload["version"]) is not int
        or payload["version"] != 1
        or not isinstance(payload["registry_fingerprint"], str)
        or not _HEX.fullmatch(payload["registry_fingerprint"])
        or not isinstance(payload["deployment_uid"], str)
        or not 0 < len(payload["deployment_uid"]) <= 256
        or any(not 33 <= ord(char) < 127 for char in payload["deployment_uid"])
    ):
        _fail("invalid_pending_gc_intent")
    if payload["registry_fingerprint"] != _registry_fingerprint(config):
        _fail("pending_gc_registry_changed")
    if _file_identity(path) != before:
        _fail("pending_gc_changed")
    return _PendingGc(payload["deployment_uid"], before)


def _clear_pending_gc(path: Path, config: RetentionConfig, expected: _PendingGc) -> None:
    if _pending_gc(path, config) != expected:
        _fail("pending_gc_changed")
    _directory(path.parent)
    path.unlink()
    _sync_directory(path.parent)


def _deletion_receipts(path: Path) -> list[dict[str, str]]:
    if _file_identity(path) is None:
        return []
    payload = _decode(_read_file(path))
    if (
        not isinstance(payload, dict)
        or set(payload) != {"version", "deleted"}
        or type(payload["version"]) is not int
        or payload["version"] != 1
        or not isinstance(payload["deleted"], list)
    ):
        _fail("invalid_deletion_receipts")
    receipts: list[dict[str, str]] = []
    identities: set[tuple[str, str]] = set()
    for item in payload["deleted"]:
        if (
            not isinstance(item, dict)
            or set(item) != {"repository", "digest"}
            or not isinstance(item["repository"], str)
            or not item["repository"].startswith("max-app/")
            or not _valid_uuid(item["repository"].removeprefix("max-app/"))
            or not isinstance(item["digest"], str)
            or not _DIGEST.fullmatch(item["digest"])
        ):
            _fail("invalid_deletion_receipts")
        identity = (item["repository"], item["digest"])
        if identity in identities:
            _fail("invalid_deletion_receipts")
        identities.add(identity)
        receipts.append({"repository": item["repository"], "digest": item["digest"]})
    return receipts


def load_controllers(path: Path, expected_count: int = 2) -> tuple[Controller, ...]:
    """Read an explicit complete coverage list; never infer a missing peer as empty."""
    if type(expected_count) is not int or expected_count < 1:
        _fail("invalid_controller_count")
    payload = _decode(_read_file(_absolute(path)))
    if (
        not isinstance(payload, dict)
        or set(payload) != {"version", "controllers"}
        or type(payload["version"]) is not int
        or payload["version"] != 1
        or not isinstance(payload["controllers"], list)
        or len(payload["controllers"]) < expected_count
    ):
        _fail("invalid_controller_configuration")
    controllers: list[Controller] = []
    names: set[str] = set()
    urls: set[str] = set()
    for item in payload["controllers"]:
        if (
            not isinstance(item, dict)
            or set(item) - {"name", "url", "token_file"}
            or not {"name", "url"} <= set(item)
            or not isinstance(item["name"], str)
            or not _NAME.fullmatch(item["name"])
            or not isinstance(item["url"], str)
        ):
            _fail("invalid_controller_configuration")
        url = item["url"]
        if url != "local":
            try:
                parsed = httpx.URL(url)
            except Exception:
                raise CoordinatorError("invalid_controller_configuration") from None
            if (
                parsed.scheme not in {"http", "https"}
                or not parsed.host
                or parsed.userinfo
                or parsed.path not in {"", "/"}
                or parsed.query
                or parsed.fragment
            ):
                _fail("invalid_controller_configuration")
            url = str(parsed).rstrip("/")
        token_file = item.get("token_file")
        if token_file is not None and (
            not isinstance(token_file, str) or not Path(token_file).is_absolute() or url == "local"
        ):
            _fail("invalid_controller_configuration")
        if item["name"] in names or url in urls:
            _fail("invalid_controller_configuration")
        names.add(item["name"])
        urls.add(url)
        controllers.append(Controller(item["name"], url, Path(token_file) if token_file else None))
    if sum(controller.url == "local" for controller in controllers) != 1:
        _fail("invalid_controller_configuration")
    return tuple(sorted(controllers, key=lambda controller: controller.name))


def _valid_uuid(value: str) -> bool:
    try:
        return str(UUID(value)) == value
    except ValueError:
        return False


def _strings(value: object, validate: Callable[[str], object]) -> frozenset[str]:
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not validate(item) for item in value
    ):
        _fail("invalid_controller_inventory", skipped=True)
    if value != sorted(set(value)):
        _fail("invalid_controller_inventory", skipped=True)
    return frozenset(value)


def _validate_roots(payload: object, now: datetime, nonce: str) -> ProtectedBuildRoots:
    if (
        not isinstance(payload, dict)
        or set(payload) != _ROOT_FIELDS
        or type(payload.get("version")) is not int
        or payload["version"] != 1
        or payload.get("nonce") != nonce
        or type(payload.get("active_operations")) is not int
        or payload["active_operations"] < 0
        or not isinstance(payload.get("generation"), str)
        or not _HEX.fullmatch(payload["generation"])
        or not isinstance(payload.get("observed_at"), str)
    ):
        _fail("invalid_controller_inventory", skipped=True)
    try:
        observed = datetime.fromisoformat(payload["observed_at"])
        if observed.utcoffset() is None or not -5 <= (now - observed).total_seconds() <= 60:
            _fail("stale_controller_inventory", skipped=True)
    except (ValueError, TypeError, OverflowError):
        raise CoordinatorError("stale_controller_inventory", skipped=True) from None
    roots = ProtectedBuildRoots(
        archive_names=_strings(payload["archive_names"], _ARCHIVE.fullmatch),
        image_ids=_strings(payload["image_ids"], _DIGEST.fullmatch),
        image_refs=_strings(payload["image_refs"], APPLICATION_IMAGE.fullmatch),
        busy_workspaces=_strings(payload["busy_workspaces"], _valid_uuid),
        active_operations=payload["active_operations"],
        observed_at=payload["observed_at"],
        generation=payload["generation"],
    )
    if roots.active_operations or roots.busy_workspaces:
        _fail("controller_busy", skipped=True)
    return roots


def _local_payload(roots: ProtectedBuildRoots, nonce: str) -> dict[str, Any]:
    return {
        "version": 1,
        "archive_names": sorted(roots.archive_names),
        "image_ids": sorted(roots.image_ids),
        "image_refs": sorted(roots.image_refs),
        "busy_workspaces": sorted(roots.busy_workspaces),
        "active_operations": roots.active_operations,
        "observed_at": roots.observed_at,
        "generation": roots.generation,
        "nonce": nonce,
    }


def _inventory(
    config: RetentionConfig,
    controllers: tuple[Controller, ...],
    deps: CoordinatorDependencies,
) -> _Inventory:
    if load_controllers(config.controllers_file, config.expected_controller_count) != controllers:
        _fail("inventory_changed", skipped=True)
    inventories: list[tuple[str, ProtectedBuildRoots]] = []
    for controller in controllers:
        nonce = secrets.token_hex(24)
        try:
            if controller.url == "local":
                payload = _local_payload(deps.local_roots(config.state_root), nonce)
            else:
                token = config.internal_token
                if controller.token_file is not None:
                    token = _read_file(controller.token_file, 8192, private=True).decode().strip()
                if not token or len(token) > 8192 or any(ord(char) < 33 for char in token):
                    _fail("invalid_controller_token")
                response = deps.http_client.get(
                    controller.url + "/internal/build-retention/roots",
                    params={"nonce": nonce},
                    headers={"X-Internal-Token": token},
                    follow_redirects=False,
                    timeout=30,
                )
                if response.status_code != 200:
                    _fail("controller_unavailable", skipped=True)
                payload = _decode(response.content)
            inventories.append((controller.name, _validate_roots(payload, deps.now(), nonce)))
        except CoordinatorError:
            raise
        except Exception:
            raise CoordinatorError("controller_unavailable", skipped=True) from None
    try:
        kubernetes = deps.kubernetes.snapshot()
        if not _HEX.fullmatch(kubernetes.generation) or any(
            not _valid_image(value) for value in kubernetes.image_refs
        ):
            _fail("invalid_kubernetes_inventory", skipped=True)
    except CoordinatorError:
        raise
    except Exception:
        raise CoordinatorError("kubernetes_unavailable", skipped=True) from None
    # Earlier peers must still be fresh when the complete scan finishes.
    for _, roots in inventories:
        _validate_roots(_local_payload(roots, "complete"), deps.now(), "complete")
    return _Inventory(tuple(inventories), kubernetes)


def _valid_image(value: object) -> bool:
    return (
        isinstance(value, str)
        and 0 < len(value) <= 2048
        and all(33 <= ord(char) < 127 for char in value)
    )


def kubernetes_roots(resources: Iterable[dict[str, Any]]) -> KubernetesRoots:
    """Collect image refs even from dormant specs; ignore only our expected registry rollout.

    Registry descendant identities change because maintenance recreates that one
    Deployment. Their image refs still participate in the snapshot, and the lease
    separately proves its exact Deployment UID, pod UID, configuration and process.
    Every other object's UID/resourceVersion participates in the change detector.
    """
    objects = list(resources)
    registry: dict[str, Any] | None = None
    for value in objects:
        if not isinstance(value, dict) or value.get("kind") not in _WORKLOAD_KINDS:
            _fail("invalid_kubernetes_inventory", skipped=True)
        metadata = value.get("metadata")
        if not isinstance(metadata, dict) or any(
            not isinstance(metadata.get(key), str) or not metadata[key]
            for key in ("namespace", "name", "uid", "resourceVersion")
        ):
            _fail("invalid_kubernetes_inventory", skipped=True)
        if value["kind"] == "Deployment" and (metadata["namespace"], metadata["name"]) == (
            "registry",
            "registry",
        ):
            if registry is not None:
                _fail("invalid_kubernetes_inventory", skipped=True)
            registry = value
    owned = {registry["metadata"]["uid"]} if registry else set()
    for kind in ("ReplicaSet", "Pod"):
        for value in objects:
            metadata = value["metadata"]
            if (
                value["kind"] == kind
                and metadata["namespace"] == "registry"
                and any(
                    isinstance(owner, dict)
                    and owner.get("controller") is True
                    and owner.get("uid") in owned
                    and owner.get("kind")
                    == ("Deployment" if kind == "ReplicaSet" else "ReplicaSet")
                    for owner in metadata.get("ownerReferences", [])
                )
            ):
                owned.add(metadata["uid"])
    image_refs: set[str] = set()
    signatures: list[object] = []
    identities: set[tuple[str, str, str]] = set()
    try:
        for value in objects:
            kind, metadata = value["kind"], value["metadata"]
            identity = (kind, metadata["namespace"], metadata["name"])
            if identity in identities:
                _fail("invalid_kubernetes_inventory", skipped=True)
            identities.add(identity)
            if kind == "Pod":
                spec = value["spec"]
            elif kind == "PodTemplate":
                spec = value["template"]["spec"]
            elif kind == "CronJob":
                spec = value["spec"]["jobTemplate"]["spec"]["template"]["spec"]
            else:
                spec = value["spec"]["template"]["spec"]
            if not isinstance(spec, dict) or not isinstance(spec.get("containers"), list):
                _fail("invalid_kubernetes_inventory", skipped=True)
            if not spec["containers"]:
                _fail("invalid_kubernetes_inventory", skipped=True)
            for key in ("containers", "initContainers", "ephemeralContainers"):
                containers = spec.get(key) or []
                if not isinstance(containers, list):
                    _fail("invalid_kubernetes_inventory", skipped=True)
                for container in containers:
                    if not isinstance(container, dict) or not _valid_image(container.get("image")):
                        _fail("invalid_kubernetes_inventory", skipped=True)
                    image_refs.add(container["image"])
            if metadata["uid"] not in owned:
                signatures.append((identity, metadata["uid"], metadata["resourceVersion"]))
            elif value is registry:
                signatures.append((identity, metadata["uid"]))
    except (KeyError, TypeError, AttributeError):
        raise CoordinatorError("invalid_kubernetes_inventory", skipped=True) from None
    generation = hashlib.sha256(
        json.dumps(
            {"objects": sorted(signatures), "images": sorted(image_refs)},
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    return KubernetesRoots(frozenset(image_refs), generation)


class KubernetesWorkloads:
    """Official client: every namespace, every page, all built-in pod-bearing kinds."""

    def __init__(
        self,
        kubeconfig_path: str,
        *,
        context: str | None = None,
        timeout_seconds: float = 180,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        from kubernetes import client, config

        _read_file(_absolute(Path(kubeconfig_path)))
        self._client = config.new_client_from_config(config_file=kubeconfig_path, context=context)
        self._core = client.CoreV1Api(self._client)
        self._apps = client.AppsV1Api(self._client)
        self._batch = client.BatchV1Api(self._client)
        self._timeout, self._clock, self._sleep = timeout_seconds, clock, sleep

    def _json(self, value: Any) -> dict[str, Any]:
        result = self._client.sanitize_for_serialization(value)
        if not isinstance(result, dict):
            _fail("invalid_kubernetes_inventory", skipped=True)
        return result

    def _list(self, method: Callable[..., Any], kind: str) -> list[dict[str, Any]]:
        objects: list[dict[str, Any]] = []
        continuation = ""
        seen: set[str] = set()
        resource_version: str | None = None
        for _ in range(10000):
            page = self._json(
                method(
                    limit=1000,
                    _continue=continuation or None,
                    _request_timeout=30,
                )
            )
            metadata, items = page.get("metadata"), page.get("items")
            if (
                not isinstance(metadata, dict)
                or not isinstance(items, list)
                or not isinstance(metadata.get("resourceVersion"), str)
                or not metadata["resourceVersion"]
            ):
                _fail("incomplete_kubernetes_inventory", skipped=True)
            if resource_version is not None and metadata["resourceVersion"] != resource_version:
                _fail("incomplete_kubernetes_inventory", skipped=True)
            resource_version = metadata["resourceVersion"]
            for item in items:
                if not isinstance(item, dict) or item.get("kind", kind) != kind:
                    _fail("incomplete_kubernetes_inventory", skipped=True)
                objects.append({**item, "kind": kind})
            continuation = metadata.get("continue") or ""
            if not isinstance(continuation, str):
                _fail("incomplete_kubernetes_inventory", skipped=True)
            if not continuation:
                return objects
            if continuation in seen:
                _fail("incomplete_kubernetes_inventory", skipped=True)
            seen.add(continuation)
        _fail("incomplete_kubernetes_inventory", skipped=True)
        return []

    def snapshot(self) -> KubernetesRoots:
        objects: list[dict[str, Any]] = []
        for method, kind in (
            (self._core.list_pod_for_all_namespaces, "Pod"),
            (self._core.list_pod_template_for_all_namespaces, "PodTemplate"),
            (self._core.list_replication_controller_for_all_namespaces, "ReplicationController"),
            (self._apps.list_deployment_for_all_namespaces, "Deployment"),
            (self._apps.list_stateful_set_for_all_namespaces, "StatefulSet"),
            (self._apps.list_daemon_set_for_all_namespaces, "DaemonSet"),
            (self._apps.list_replica_set_for_all_namespaces, "ReplicaSet"),
            (self._batch.list_job_for_all_namespaces, "Job"),
            (self._batch.list_cron_job_for_all_namespaces, "CronJob"),
        ):
            objects.extend(self._list(method, kind))
        return kubernetes_roots(objects)

    def _registry(self) -> dict[str, Any]:
        return self._json(
            self._apps.read_namespaced_deployment(
                "registry",
                "registry",
                _request_timeout=30,
            )
        )

    def registry_uid(self) -> str:
        value = self._registry().get("metadata", {}).get("uid")
        if not isinstance(value, str) or not value:
            _fail("invalid_registry_identity")
        return value

    def _wait_restored_ready(self, uid: str) -> None:
        deadline = self._clock() + self._timeout
        while True:
            current = self._registry()
            metadata, spec, status = current["metadata"], current["spec"], current.get("status", {})
            if metadata.get("uid") != uid:
                _fail("restore_readiness_failed")
            replicas = spec.get("replicas", 1)
            if (
                not (metadata.get("annotations") or {}).get(OWNER_ANNOTATION)
                and not (spec["template"].get("metadata", {}).get("annotations") or {}).get(
                    OWNER_ANNOTATION,
                )
                and type(replicas) is int
                and replicas == 1
                and type(metadata.get("generation")) is int
                and type(status.get("observedGeneration")) is int
                and status["observedGeneration"] >= metadata["generation"]
                and all(
                    status.get(key) == replicas
                    for key in (
                        "replicas",
                        "updatedReplicas",
                        "readyReplicas",
                        "availableReplicas",
                    )
                )
                and status.get("unavailableReplicas", 0) == 0
            ):
                return
            if self._clock() >= deadline:
                _fail("restore_readiness_failed")
            self._sleep(min(1, max(0, deadline - self._clock())))

    async def wait_restored_ready(self, uid: str) -> None:
        await asyncio.to_thread(self._wait_restored_ready, uid)

    def close(self) -> None:
        self._client.close()


def _locations(
    config: RetentionConfig,
    controllers: tuple[Controller, ...],
) -> _RegistryPaths:
    state, journal = _absolute(config.state_root), _absolute(config.journal_root)
    _directory(state)
    _directory(journal)
    if journal.is_relative_to(state):
        _fail("journal_inside_state")
    paths = (
        journal / "registry-maintenance.json",
        journal / "registry-seen.json",
        journal / "registry-receipts.json",
        _absolute(config.report or journal / "registry-report.json"),
        journal / "registry-pending-gc.json",
    )
    for path in paths:
        if path.is_relative_to(state):
            _fail("journal_inside_state")
        _file_identity(path)
    reserved = {_absolute(config.controllers_file), _absolute(Path(config.kubeconfig_path))}
    reserved.update(_absolute(item.token_file) for item in controllers if item.token_file)
    if len(set(paths)) != len(paths) or any(path in reserved for path in paths):
        _fail("report_aliases_configuration")
    return paths


def _recheck(
    initial: _Inventory,
    config: RetentionConfig,
    controllers: tuple[Controller, ...],
    deps: CoordinatorDependencies,
) -> None:
    if _inventory(config, controllers, deps).signature() != initial.signature():
        _fail("inventory_changed", skipped=True)


async def _apply(
    config: RetentionConfig,
    deps: CoordinatorDependencies,
    controllers: tuple[Controller, ...],
    initial: _Inventory,
    policy: RegistryRetention,
    plan: RegistryPlan,
    journal: Path,
    receipts_path: Path,
    pending_path: Path,
    pending: _PendingGc | None,
    report: dict[str, Any],
) -> None:
    uid = deps.kubernetes.registry_uid()
    if _pending_gc(pending_path, config) != pending:
        _fail("pending_gc_changed")
    if pending is not None and pending.deployment_uid != uid:
        _fail("pending_gc_registry_changed")
    receipts = _deletion_receipts(receipts_path) if pending is not None else []
    _write_json(receipts_path, {"version": 1, "deleted": receipts})
    try:
        with deps.maintenance_factory(journal) as lease:
            lease.assert_read_only()
            _recheck(initial, config, controllers, deps)
            callback_error: CoordinatorError | None = None

            def delete(repository: str, digest: str) -> int:
                nonlocal callback_error, pending
                try:
                    _recheck(initial, config, controllers, deps)
                    lease.assert_read_only()
                    authorization = "Basic " + base64.b64encode(
                        f"{config.registry_username}:{config.registry_password}".encode(),
                    ).decode("ascii")
                    if _pending_gc(pending_path, config) != pending:
                        _fail("pending_gc_changed")
                    if pending is None:
                        # A DELETE may succeed even if its 202 response is lost.
                        # Crash-recoverable GC intent must precede network mutation.
                        _write_json(
                            pending_path,
                            {
                                "version": 1,
                                "registry_fingerprint": _registry_fingerprint(config),
                                "deployment_uid": uid,
                            },
                        )
                        pending = _pending_gc(pending_path, config)
                        if pending is None:
                            _fail("pending_gc_changed")
                        report["pending_gc"] = True
                    status = lease.delete_manifest(repository, digest, authorization)
                    if type(status) is int and status == 202:
                        receipts.append({"repository": repository, "digest": digest})
                        report["deleted"] += 1
                        _write_json(receipts_path, {"version": 1, "deleted": receipts})
                    return status
                except CoordinatorError as error:
                    callback_error = error
                    raise

            if plan.candidates:
                try:
                    # A failed start may still have created a process; always try to stop it.
                    lease.start_private_writer()
                    try:
                        policy.apply(plan, initial.protected, delete=delete)
                    except RegistryRetentionError:
                        if callback_error is not None:
                            raise callback_error from None
                        raise
                finally:
                    lease.stop_private_writer()
            _recheck(initial, config, controllers, deps)
            lease.assert_read_only()
            if pending is None or _pending_gc(pending_path, config) != pending:
                _fail("pending_gc_changed")
            allocated = lease.garbage_collect()
            if set(allocated) != {"bytes_before", "bytes_after", "bytes_freed"} or any(
                type(value) is not int or value < 0 for value in allocated.values()
            ):
                _fail("invalid_gc_receipt")
            if allocated["bytes_freed"] != max(
                0, allocated["bytes_before"] - allocated["bytes_after"]
            ):
                _fail("invalid_gc_receipt")
            report.update(allocated)
            lease.assert_read_only()
            _clear_pending_gc(pending_path, config, pending)
            report["pending_gc"] = False
    finally:
        # restore() is a CAS patch, not a readiness wait. Keep the lifetime lock
        # until the original Deployment has completed that asynchronous rollout.
        try:
            await deps.kubernetes.wait_restored_ready(uid)
        except Exception:
            raise CoordinatorError("restore_readiness_failed") from None


async def _run(
    config: RetentionConfig,
    deps: CoordinatorDependencies,
    paths: _RegistryPaths,
    controllers: tuple[Controller, ...],
    report: dict[str, Any],
) -> None:
    journal, seen, receipts, _, pending_path = paths
    lock = deps.lock or WorkspaceOperationLock(_absolute(config.journal_root))
    async with lock.hold_named("registry-retention"):
        report["pending_gc"] = _file_identity(pending_path) is not None
        if config.recover_only:
            uid = deps.kubernetes.registry_uid()
            try:
                recovered = deps.recover(journal)
            finally:
                try:
                    await deps.kubernetes.wait_restored_ready(uid)
                except Exception:
                    raise CoordinatorError("restore_readiness_failed") from None
            report["status"] = "recovered" if recovered else "no_recovery"
            return
        if _file_identity(journal) is not None:
            _fail("recovery_required")
        pending = _pending_gc(pending_path, config)
        initial = _inventory(config, controllers, deps)
        report["controllers"], report["protected"] = len(controllers), len(initial.protected)
        policy = deps.retention_factory(seen)
        plan = policy.plan(initial.protected, deps.now())
        report["candidates"] = len(plan.candidates)
        if not config.apply:
            report["status"] = "dry_run"
            return
        if not plan.candidates and pending is None:
            report["status"] = "no_candidates"
            return
        await _apply(
            config,
            deps,
            controllers,
            initial,
            policy,
            plan,
            journal,
            receipts,
            pending_path,
            pending,
            report,
        )
        report["status"] = "applied"


def _runtime(config: RetentionConfig) -> tuple[CoordinatorDependencies, Callable[[], None]]:
    workloads = KubernetesWorkloads(config.kubeconfig_path, context=config.kube_context)
    client = httpx.Client(timeout=30, follow_redirects=False, trust_env=False)
    try:
        api = KubernetesRegistryApi(config.kubeconfig_path, context=config.kube_context)
    except Exception:
        client.close()
        workloads.close()
        raise

    def close() -> None:
        api.close()
        client.close()
        workloads.close()

    return CoordinatorDependencies(
        local_roots=collect_roots,
        http_client=client,
        kubernetes=workloads,
        retention_factory=lambda path: RegistryRetention(
            config.registry_url,
            config.registry_username,
            config.registry_password,
            path,
            client=client,
        ),
        maintenance_factory=lambda path: RegistryMaintenance(
            config.kubeconfig_path,
            path,
            api=api,
            context=config.kube_context,
        ),
        recover=lambda path: recover(
            config.kubeconfig_path,
            path,
            api=api,
            context=config.kube_context,
        ),
    ), close


async def run_retention(
    config: RetentionConfig,
    *,
    dependencies: CoordinatorDependencies | None = None,
) -> dict[str, Any]:
    """Run under one lifetime lock and return only counts and fixed status/error codes."""
    report: dict[str, Any] = {
        "version": 1,
        "status": "failed",
        "code": "ok",
        "controllers": 0,
        "protected": 0,
        "candidates": 0,
        "deleted": 0,
        "bytes_before": 0,
        "bytes_after": 0,
        "bytes_freed": 0,
        "pending_gc": False,
    }
    paths: _RegistryPaths | None = None
    close: Callable[[], None] | None = None
    try:
        if config.apply and config.recover_only:
            _fail("incompatible_modes")
        controllers = (
            ()
            if config.recover_only
            else load_controllers(
                config.controllers_file,
                config.expected_controller_count,
            )
        )
        paths = _locations(config, controllers)
        if dependencies is None:
            dependencies, close = _runtime(config)
        await _run(config, dependencies, paths, controllers, report)
    except CoordinatorError as error:
        report.update(status="skipped" if error.skipped else "failed", code=error.code)
    except (WorkspaceLockTimeout, WorkspaceLockUnavailable):
        report.update(status="failed", code="registry_lock_unavailable")
    except RegistryRetentionError:
        report.update(status="failed", code="registry_policy_failed")
    except RegistryMaintenanceError:
        report.update(status="failed", code="registry_maintenance_failed")
    except Exception:
        report.update(status="failed", code="internal_error")
    finally:
        if close is not None:
            close()
    if paths is not None:
        try:
            _write_json(paths[3], report)
        except Exception:
            report.update(status="failed", code="report_write_failed")
    return report
