"""Exclusive, crash-recoverable maintenance of the deployed Distribution v2 registry.

Only the public registry is restarted read-only. Authenticated manifest deletion uses
an owned loopback process; physical GC cannot run until that process is dead. The
journal contains identities and the two overridden flags, never registry credentials.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import secrets
import stat
import time
from collections.abc import Callable
from copy import deepcopy
from pathlib import Path
from types import TracebackType
from typing import Any, Protocol
from uuid import UUID

import yaml  # type: ignore[import-untyped]

OWNER_ANNOTATION = "yleum.io/registry-maintenance-owner"
READ_ONLY = "REGISTRY_STORAGE_MAINTENANCE_READONLY_ENABLED"
UPLOAD_PURGING = "REGISTRY_STORAGE_MAINTENANCE_UPLOADPURGING_ENABLED"
CONFIG_PATH = "/etc/docker/registry/config.yml"
STORAGE_PATH = "/var/lib/registry"
_FLAGS = {READ_ONLY: "true", UPLOAD_PURGING: "false"}
_DNS = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?")
_NONCE = re.compile(r"[a-f0-9]{32}")
_DIGEST = re.compile(r"sha256:[a-f0-9]{64}")
_MAX_OUTPUT = 1024 * 1024


class RegistryMaintenanceError(RuntimeError):
    """A safety proof failed. Public writes must remain fenced until recovery."""


class RegistryApi(Protocol):
    """Kubernetes boundary; JSON dictionaries use Kubernetes camelCase field names."""

    def read_deployment(self, namespace: str, name: str) -> dict[str, Any]: ...

    def patch_deployment(
        self, namespace: str, name: str, operations: list[dict[str, Any]]
    ) -> dict[str, Any]: ...

    def list_pods(self, namespace: str) -> list[dict[str, Any]]: ...

    def exec(
        self,
        namespace: str,
        pod: str,
        container: str,
        command: list[str],
        *,
        stdin: str | None = None,
        timeout_seconds: float = 60,
    ) -> tuple[int, str]: ...


class KubernetesRegistryApi:
    """Official client with JSON Patch CAS and private, bounded exec stdin."""

    def __init__(
        self,
        kubeconfig_path: str,
        *,
        context: str | None = None,
        timeout_seconds: float = 180,
    ) -> None:
        from kubernetes import client, config

        self._client = config.new_client_from_config(config_file=kubeconfig_path, context=context)
        self._apps = client.AppsV1Api(self._client)
        self._core = client.CoreV1Api(self._client)
        self._timeout = timeout_seconds

    def _json(self, obj: Any) -> dict[str, Any]:
        result = self._client.sanitize_for_serialization(obj)
        if not isinstance(result, dict):
            raise RegistryMaintenanceError("invalid Kubernetes response")
        return result

    def read_deployment(self, namespace: str, name: str) -> dict[str, Any]:
        return self._json(
            self._apps.read_namespaced_deployment(name, namespace, _request_timeout=self._timeout)
        )

    def patch_deployment(
        self, namespace: str, name: str, operations: list[dict[str, Any]]
    ) -> dict[str, Any]:
        # call_api explicitly selects JSON Patch; generated clients otherwise choose
        # their first supported content type, which need not be JSON Patch.
        result = self._client.call_api(
            "/apis/apps/v1/namespaces/{namespace}/deployments/{name}",
            "PATCH",
            path_params={"namespace": namespace, "name": name},
            body=operations,
            header_params={
                "Content-Type": "application/json-patch+json",
                "Accept": "application/json",
            },
            response_type="V1Deployment",
            auth_settings=["BearerToken"],
            _return_http_data_only=True,
            _request_timeout=self._timeout,
        )
        return self._json(result)

    def list_pods(self, namespace: str) -> list[dict[str, Any]]:
        # List the entire namespace: labels can change, but an old PVC consumer
        # still prevents GC. Follow every page rather than accepting a partial fence.
        pods: list[dict[str, Any]] = []
        seen: set[str] = set()
        continuation = ""
        while True:
            page = self._json(
                self._core.list_namespaced_pod(
                    namespace,
                    limit=1000,
                    _continue=continuation or None,
                    _request_timeout=self._timeout,
                )
            )
            pods.extend(page.get("items") or [])
            continuation = (page.get("metadata") or {}).get("continue") or ""
            if not continuation:
                return pods
            if continuation in seen:
                raise RegistryMaintenanceError("incomplete Kubernetes pod inventory")
            seen.add(continuation)

    def exec(
        self,
        namespace: str,
        pod: str,
        container: str,
        command: list[str],
        *,
        stdin: str | None = None,
        timeout_seconds: float = 60,
    ) -> tuple[int, str]:
        from kubernetes import client
        from kubernetes.stream import stream

        # stream replaces ApiClient.request: never share that client with GET/PATCH.
        api_client = client.ApiClient(configuration=self._client.configuration)
        channel = None
        try:
            core = client.CoreV1Api(api_client)
            channel = stream(
                core.connect_get_namespaced_pod_exec,
                pod,
                namespace,
                container=container,
                command=command,
                stdin=stdin is not None,
                stdout=True,
                stderr=True,
                tty=False,
                _preload_content=False,
                _request_timeout=timeout_seconds,
            )
            if stdin is not None:
                channel.write_stdin(stdin)
            deadline = time.monotonic() + timeout_seconds
            output: list[str] = []
            output_size = 0
            while channel.is_open():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise RegistryMaintenanceError("registry exec timed out")
                channel.update(timeout=min(remaining, 0.1))
                chunk = channel.read_stdout(timeout=0) or ""
                # Drain stderr without forwarding potentially credentialed diagnostics.
                channel.read_stderr(timeout=0)
                output_size += len(chunk)
                if output_size > _MAX_OUTPUT:
                    raise RegistryMaintenanceError("registry exec output exceeded limit")
                output.append(chunk)
            result = channel.returncode
            return (result if isinstance(result, int) else 1), "".join(output)
        finally:
            if channel is not None:
                channel.close()
            api_client.close()

    def close(self) -> None:
        self._client.close()


# Fixed script and validated nonce only. No credentials enter shell arguments or
# temporary files. Process ownership requires nonce, PID, start time, executable,
# command line and inherited flags to agree before a signal can be sent.
_SCRIPT = r"""
set -eu
action=$1
owner=$2
marker=/tmp/yleum-registry-maintenance
export LC_ALL=C
die() { exit 1; }
for tool in nc du readlink tr grep awk nohup; do command -v "$tool" >/dev/null || die; done
test "$(readlink /proc/1/exe)" = /bin/registry || die
case "$(tr '\000' ' ' < /proc/1/cmdline)" in
  '/bin/registry serve /etc/docker/registry/config.yml '|\
  'registry serve /etc/docker/registry/config.yml ') ;;
  *) die ;;
esac
if test "$action" != preflight; then
  tr '\000' '\n' < /proc/1/environ |
    grep -Fxq 'REGISTRY_STORAGE_MAINTENANCE_READONLY_ENABLED=true' || die
  tr '\000' '\n' < /proc/1/environ |
    grep -Fxq 'REGISTRY_STORAGE_MAINTENANCE_UPLOADPURGING_ENABLED=false' || die
fi
test ! -L "$marker" || die
pid=0
kind=
owned() {
  test -d "$marker" && test ! -L "$marker/owner" && test -f "$marker/owner" || die
  test "$(cat "$marker/owner")" = "$owner" || die
}
load() {
  if test -e "$marker"; then
    owned
    if test -e "$marker/pid" || test -L "$marker/pid"; then
      for f in pid kind start; do test -f "$marker/$f" && test ! -L "$marker/$f" || die; done
      pid=$(cat "$marker/pid"); kind=$(cat "$marker/kind")
      case "$pid" in ''|*[!0-9]*) die ;; esac
      test "$pid" -gt 1 || die
      case "$kind" in serve|garbage-collect) ;; *) die ;; esac
      if test -e "/proc/$pid/exe"; then
        test "$(readlink "/proc/$pid/exe")" = /bin/registry || die
        test "$(awk '{print $22}' "/proc/$pid/stat")" = "$(cat "$marker/start")" || die
        test "$(tr '\000' ' ' < "/proc/$pid/cmdline")" = \
          "/bin/registry $kind /etc/docker/registry/config.yml " || die
        tr '\000' '\n' < "/proc/$pid/environ" |
          grep -Fxq "YLEUM_REGISTRY_MAINTENANCE_OWNER=$owner" || die
        tr '\000' '\n' < "/proc/$pid/environ" |
          grep -Fxq 'REGISTRY_STORAGE_MAINTENANCE_UPLOADPURGING_ENABLED=false' || die
        if test "$kind" = serve; then
          tr '\000' '\n' < "/proc/$pid/environ" |
            grep -Fxq 'REGISTRY_HTTP_ADDR=127.0.0.1:5001' || die
          tr '\000' '\n' < "/proc/$pid/environ" |
            grep -Fxq 'REGISTRY_STORAGE_MAINTENANCE_READONLY_ENABLED=false' || die
        fi
      elif test -d "/proc/$pid"; then
        # An orphaned child may remain a zombie until PID 1 reaps it.
        test "$(awk '{print $3}' "/proc/$pid/stat")" = Z || die
        test "$(awk '{print $22}' "/proc/$pid/stat")" = "$(cat "$marker/start")" || die
        pid=0
      else
        pid=0
      fi
    fi
  fi
}
scan() {
  for file in /proc/[0-9]*/exe; do
    executable=$(readlink "$file" 2>/dev/null || true)
    case "$executable" in /bin/registry|'/bin/registry (deleted)')
      process=${file#/proc/}; process=${process%/exe}
      test "$process" = 1 || test "$process" = "$pid" || die ;;
    esac
  done
}
ports() {
  # 1389 is port 5001. An owned writer must have exactly one IPv4 loopback
  # listener; any wildcard, IPv6, foreign or lingering listener fails closed.
  awk -v wanted="$1" '
    FNR > 1 && $4 == "0A" && $2 ~ /:1389$/ {
      count++; if ($2 != "0100007F:1389") bad=1
    }
    END {exit (bad || count != wanted)}' /proc/net/tcp /proc/net/tcp6
}
prepare() {
  if test ! -e "$marker"; then
    (umask 077; mkdir "$marker"; printf '%s\n' "$owner" > "$marker/owner") || die
  fi
  owned
}
stop() {
  load; scan
  if test "$pid" -ne 0; then
    kill -TERM "$pid" || die
    attempts=0
    while kill -0 "$pid" 2>/dev/null; do
      # Zombies cannot write; their /proc/exe link has disappeared.
      test -e "/proc/$pid/exe" || break
      attempts=$((attempts+1)); test "$attempts" -lt 100 || die
      sleep 0.1
    done
    test ! -e "/proc/$pid/exe" || die
  fi
  pid=0; scan; ports 0
  if test -e "$marker"; then rm -f "$marker/pid" "$marker/start" "$marker/kind"; fi
}
case "$action" in
  preflight)
    test ! -e "$marker" && test ! -L "$marker" || die
    scan; ports 0; printf 'ok\n' ;;
  probe|writer)
    load; scan
    if test "$pid" -ne 0; then test "$kind" = serve || die; ports 1; else ports 0; fi
    printf '%s\n' "$pid" ;;
  start)
    load; scan; test "$pid" = 0 || die; ports 0; prepare
    # The child writes its own PID before exec. If the controller disappears,
    # recovery uses this owned marker rather than trusting a stale response.
    nohup /bin/sh -ceu '
      marker=$1; owner=$2
      umask 077
      printf "%s\n" serve > "$marker/kind"
      awk "{print \$22}" /proc/$$/stat > "$marker/start"
      printf "%s\n" "$$" > "$marker/pid"
      export YLEUM_REGISTRY_MAINTENANCE_OWNER="$owner"
      export REGISTRY_HTTP_ADDR=127.0.0.1:5001
      export REGISTRY_STORAGE_MAINTENANCE_READONLY_ENABLED=false
      export REGISTRY_STORAGE_MAINTENANCE_UPLOADPURGING_ENABLED=false
      exec /bin/registry serve /etc/docker/registry/config.yml
    ' registry-private-writer "$marker" "$owner" </dev/null >/dev/null 2>&1 &
    attempts=0
    while :; do
      attempts=$((attempts+1)); test "$attempts" -lt 100 || die
      sleep 0.1
      test -f "$marker/pid" || continue
      load; scan
      test "$pid" -ne 0 || die
      if ports 1; then
        response=$(printf 'GET /v2/ HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n' |
          nc -w 2 127.0.0.1 5001)
        case "$response" in 'HTTP/1.1 401 '*|'HTTP/1.0 401 '*) break ;; *) die ;; esac
      fi
    done
    printf '%s\n' "$pid" ;;
  stop) stop; printf 'ok\n' ;;
  clean)
    stop
    if test -e "$marker"; then owned; rm -f "$marker/owner"; rmdir "$marker"; fi
    printf 'ok\n' ;;
  du) load; scan; test "$pid" = 0 || die; ports 0; du -sk /var/lib/registry ;;
  gc)
    load; scan; test "$pid" = 0 || die; ports 0; prepare
    # GC itself is owned too: an exec timeout must never restore writes while
    # a detached garbage collector is still sweeping the PVC.
    /bin/sh -ceu '
      marker=$1; owner=$2
      umask 077
      printf "%s\n" garbage-collect > "$marker/kind"
      awk "{print \$22}" /proc/$$/stat > "$marker/start"
      printf "%s\n" "$$" > "$marker/pid"
      export YLEUM_REGISTRY_MAINTENANCE_OWNER="$owner"
      exec /bin/registry garbage-collect /etc/docker/registry/config.yml
    ' registry-garbage-collector "$marker" "$owner" &
    child=$!
    result=0; wait "$child" || result=$?
    # A failed GC may leave unreferenced bytes; it never authorizes a new writer.
    pid=0; scan; ports 0
    rm -f "$marker/pid" "$marker/start" "$marker/kind"
    exit "$result" ;;
  *) die ;;
esac
"""


def _require(condition: object, message: str) -> None:
    if not condition:
        raise RegistryMaintenanceError(message)


def _fingerprint(template: dict[str, Any]) -> str:
    payload = json.dumps(template, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(payload).hexdigest()


def _env(container: dict[str, Any]) -> dict[str, dict[str, Any]]:
    entries = container.get("env") or []
    _require(isinstance(entries, list), "invalid registry environment")
    result: dict[str, dict[str, Any]] = {}
    for item in entries:
        _require(
            isinstance(item, dict) and isinstance(item.get("name"), str),
            "invalid registry environment",
        )
        name = item["name"]
        _require(name not in result, "duplicate registry environment")
        result[name] = item
    return result


def _layout(deployment: dict[str, Any]) -> tuple[str, str, dict[str, Any]]:
    meta, spec = deployment["metadata"], deployment["spec"]
    _require(isinstance(meta.get("uid"), str) and meta["uid"], "missing Deployment UID")
    _require(
        isinstance(meta.get("resourceVersion"), str) and meta["resourceVersion"],
        "missing Deployment resourceVersion",
    )
    _require(
        spec.get("replicas") == 1 and (spec.get("strategy") or {}).get("type") == "Recreate",
        "registry requires one Recreate replica",
    )
    template = spec["template"]["spec"]
    _require(
        not any(
            template.get(key)
            for key in (
                "initContainers",
                "ephemeralContainers",
                "hostPID",
                "hostNetwork",
                "shareProcessNamespace",
            )
        ),
        "unsupported registry pod layout",
    )
    containers = template["containers"]
    _require(len(containers) == 1, "unsupported registry containers")
    container = containers[0]
    _require(
        re.fullmatch(
            r"registry:2(?:\.\d+\.\d+)?(?:@sha256:[a-f0-9]{64})?", container.get("image", "")
        ),
        "unsupported registry image",
    )
    _require(_DNS.fullmatch(container.get("name", "")), "invalid registry container")
    _require(
        not any(container.get(key) for key in ("command", "args", "envFrom")),
        "unsupported registry command or injected environment",
    )
    environment = _env(container)
    values = {key: value.get("value") for key, value in environment.items()}
    _require(
        values.get("REGISTRY_AUTH") == "htpasswd"
        and values.get("REGISTRY_AUTH_HTPASSWD_PATH") == "/auth/htpasswd"
        and values.get("REGISTRY_STORAGE_DELETE_ENABLED") == "true",
        "registry requires existing htpasswd authentication and manifest deletion",
    )
    allowed = {
        "REGISTRY_AUTH",
        "REGISTRY_AUTH_HTPASSWD_PATH",
        "REGISTRY_AUTH_HTPASSWD_REALM",
        "REGISTRY_STORAGE_DELETE_ENABLED",
        "REGISTRY_STORAGE_FILESYSTEM_ROOTDIRECTORY",
        "REGISTRY_HTTP_ADDR",
        "REGISTRY_HTTP_SECRET",
        "REGISTRY_HTTP_RELATIVEURLS",
        READ_ONLY,
        UPLOAD_PURGING,
    }
    _require(
        all(not key.startswith("REGISTRY_") or key in allowed for key in environment),
        "unsupported registry configuration override",
    )
    _require(
        values.get("REGISTRY_STORAGE_FILESYSTEM_ROOTDIRECTORY", STORAGE_PATH) == STORAGE_PATH
        and values.get("REGISTRY_HTTP_ADDR", ":5000") in {":5000", "0.0.0.0:5000"},
        "unsupported registry storage or public address",
    )
    for flag in _FLAGS:
        _require(
            flag not in environment or values[flag] in {"true", "false"},
            "unsupported registry maintenance flag",
        )
    mounts = container.get("volumeMounts") or []
    volumes = template.get("volumes") or []
    _require(len(mounts) == len(volumes) == 2, "unsupported registry mounts")
    mounted = {item["mountPath"]: item for item in mounts}
    _require(set(mounted) == {STORAGE_PATH, "/auth"}, "unsupported registry mount paths")
    _require(
        all(not item.get("subPath") and not item.get("subPathExpr") for item in mounts),
        "unsupported registry mount subpath",
    )
    by_name = {item["name"]: item for item in volumes}
    data, auth = mounted[STORAGE_PATH], mounted["/auth"]
    claim = (by_name[data["name"]].get("persistentVolumeClaim") or {}).get("claimName", "")
    _require(
        _DNS.fullmatch(claim)
        and not data.get("readOnly")
        and auth.get("readOnly") is True
        and (by_name[auth["name"]].get("secret") or {}).get("secretName"),
        "registry requires writable filesystem PVC and read-only htpasswd secret",
    )
    _require(container.get("readinessProbe"), "registry requires a readiness probe")
    return container["name"], claim, container


def _config(raw: str) -> str:
    try:
        parsed = yaml.safe_load(raw)
        _require(
            isinstance(parsed, dict) and parsed.get("version") == 0.1,
            "unsupported Distribution configuration",
        )
        storage = parsed["storage"]
        _require(
            set(storage) <= {"filesystem", "cache", "delete", "maintenance"}
            and storage["filesystem"] == {"rootdirectory": STORAGE_PATH},
            "registry configuration must use the expected filesystem",
        )
        _require(
            set(parsed) <= {"version", "log", "loglevel", "storage", "http", "health", "auth"}
            and (parsed.get("http") or {}).get("addr") in {":5000", "0.0.0.0:5000"}
            and not any(
                (parsed.get("http") or {}).get(key)
                for key in ("tls", "net", "prefix", "host", "debug")
            ),
            "unsupported registry HTTP configuration",
        )
        auth = parsed.get("auth")
        _require(not auth or set(auth) == {"htpasswd"}, "unsupported registry authentication")
        return hashlib.sha256(raw.encode()).hexdigest()
    except RegistryMaintenanceError:
        raise
    except Exception:
        raise RegistryMaintenanceError("invalid Distribution configuration") from None


def _cas(deployment: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {"op": "test", "path": "/metadata/uid", "value": deployment["metadata"]["uid"]},
        {
            "op": "test",
            "path": "/metadata/resourceVersion",
            "value": deployment["metadata"]["resourceVersion"],
        },
    ]


def _annotation_patch(
    metadata: dict[str, Any],
    prefix: str,
    owner: str | None,
    originally_present: bool = True,
) -> dict[str, Any]:
    annotations = deepcopy(metadata.get("annotations") or {})
    if owner is None:
        annotations.pop(OWNER_ANNOTATION, None)
        if not annotations and not originally_present:
            return {"op": "remove", "path": prefix + "/annotations"}
    else:
        annotations[OWNER_ANNOTATION] = owner
    return {"op": "add", "path": prefix + "/annotations", "value": annotations}


def _flag_patch(
    container: dict[str, Any],
    container_index: int,
    replacements: dict[str, dict[str, Any] | None],
) -> list[dict[str, Any]]:
    entries = container.get("env") or []
    prefix = f"/spec/template/spec/containers/{container_index}/env"
    patches: list[dict[str, Any]] = []
    remaining = dict(replacements)
    for index in reversed(range(len(entries))):
        name = entries[index]["name"]
        if name not in remaining:
            continue
        value = remaining.pop(name)
        patch: dict[str, Any] = {
            "op": "replace" if value else "remove",
            "path": f"{prefix}/{index}",
        }
        if value:
            patch["value"] = value
        patches.append(patch)
    for value in remaining.values():
        if value is not None:
            patches.append({"op": "add", "path": prefix + "/-", "value": value})
    return patches


def _saved_flags(value: object) -> bool:
    return (
        isinstance(value, dict)
        and set(value) == set(_FLAGS)
        and all(
            entry is None
            or entry == {"name": name, "value": "true"}
            or entry == {"name": name, "value": "false"}
            for name, entry in value.items()
        )
    )


class RegistryMaintenance:
    """One owned read-only registry lease. Existing journals require recover()."""

    def __init__(
        self,
        kubeconfig_path: str,
        journal_path: Path,
        *,
        namespace: str = "registry",
        deployment: str = "registry",
        context: str | None = None,
        api: RegistryApi | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        timeout_seconds: float = 180,
        poll_interval_seconds: float = 1,
    ) -> None:
        _require(
            _DNS.fullmatch(namespace) and _DNS.fullmatch(deployment),
            "invalid Kubernetes registry address",
        )
        _require(timeout_seconds > 0 and poll_interval_seconds > 0, "invalid maintenance timeout")
        self.namespace, self.deployment = namespace, deployment
        self.journal_path = Path(journal_path).absolute()
        self._api = api or KubernetesRegistryApi(
            kubeconfig_path,
            context=context,
            timeout_seconds=timeout_seconds,
        )
        self._owns_api = api is None
        self._clock, self._sleep = clock, sleep
        self._timeout, self._poll = timeout_seconds, poll_interval_seconds
        self._state: dict[str, Any] | None = None

    @property
    def pod_uid(self) -> str | None:
        value = (self._state or {}).get("pod", {}).get("uid")
        return value if isinstance(value, str) else None

    def _read(self) -> dict[str, Any]:
        try:
            return self._api.read_deployment(self.namespace, self.deployment)
        except Exception:
            raise RegistryMaintenanceError("registry Deployment read failed") from None

    def _patch(self, operations: list[dict[str, Any]]) -> dict[str, Any]:
        try:
            return self._api.patch_deployment(self.namespace, self.deployment, operations)
        except Exception:
            raise RegistryMaintenanceError("registry Deployment changed or patch failed") from None

    def _pods(self) -> list[dict[str, Any]]:
        assert self._state is not None
        try:
            all_pods = self._api.list_pods(self.namespace)
            selector = self._state["selector"]
            return [
                item
                for item in all_pods
                if all(
                    (item.get("metadata", {}).get("labels") or {}).get(key) == value
                    for key, value in selector.items()
                )
                or any(
                    (volume.get("persistentVolumeClaim") or {}).get("claimName")
                    == self._state["claim"]
                    for volume in item.get("spec", {}).get("volumes", [])
                )
            ]
        except Exception:
            raise RegistryMaintenanceError("registry pod inventory failed") from None

    def _exec(
        self,
        pod: dict[str, Any],
        command: list[str],
        *,
        stdin: str | None = None,
    ) -> str:
        assert self._state is not None
        try:
            code, output = self._api.exec(
                self.namespace,
                pod["metadata"]["name"],
                self._state["container"],
                command,
                stdin=stdin,
                timeout_seconds=self._timeout,
            )
            _require(
                code == 0 and isinstance(output, str) and len(output) <= _MAX_OUTPUT,
                "registry exec failed",
            )
            return output
        except Exception:
            raise RegistryMaintenanceError(
                "registry exec failed; recovery may be required"
            ) from None

    def _run(self, action: str, pod: dict[str, Any] | None = None) -> str:
        assert self._state is not None
        target = pod or {"metadata": self._state["pod"]}
        return self._exec(
            target,
            ["/bin/sh", "-ceu", _SCRIPT, "registry-maintenance", action, self._state["owner"]],
        )

    def _journal_parent(self) -> None:
        _require(self.journal_path.parent.is_dir(), "registry journal directory does not exist")
        _require(
            all(
                not part.is_symlink()
                for part in (self.journal_path.parent, *self.journal_path.parents)
            ),
            "registry journal directory cannot be a symlink",
        )

    def _save(self, *, create: bool = False) -> None:
        assert self._state is not None
        self._journal_parent()
        payload = (json.dumps(self._state, sort_keys=True) + "\n").encode()
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        temporary = self.journal_path.with_name(
            self.journal_path.name + "." + self._state["owner"] + "." + secrets.token_hex(8)
        )
        target = self.journal_path if create else temporary
        try:
            if not create:
                old = self._load()
                _require(old["owner"] == self._state["owner"], "registry journal ownership changed")
            descriptor = os.open(target, flags, 0o600)
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            if not create:
                os.replace(temporary, self.journal_path)
            # Persist the directory entry too on Linux, where the job is deployed.
            if os.name != "nt":
                directory = os.open(
                    self.journal_path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
                )
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
        except Exception:
            raise RegistryMaintenanceError("registry journal could not be persisted") from None

    def _load(self) -> dict[str, Any]:
        self._journal_parent()
        try:
            before = self.journal_path.lstat()
            _require(stat.S_ISREG(before.st_mode), "invalid registry journal file")
            descriptor = os.open(
                self.journal_path,
                os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0),
            )
            with os.fdopen(descriptor, "r", encoding="utf-8") as stream:
                actual = os.fstat(stream.fileno())
                _require(
                    stat.S_ISREG(actual.st_mode)
                    and (before.st_dev, before.st_ino) == (actual.st_dev, actual.st_ino)
                    and actual.st_size <= 65536,
                    "invalid registry journal file",
                )
                data: object = json.loads(stream.read(65537))
            _require(
                isinstance(data, dict)
                and type(data.get("version")) is int
                and data["version"] == 1
                and _NONCE.fullmatch(data.get("owner", ""))
                and data.get("namespace") == self.namespace
                and data.get("deployment") == self.deployment
                and isinstance(data.get("uid"), str)
                and data["uid"]
                and _DNS.fullmatch(data.get("container", ""))
                and _DNS.fullmatch(data.get("claim", ""))
                and _saved_flags(data.get("original"))
                and re.fullmatch(r"[a-f0-9]{64}", data.get("config", ""))
                and isinstance(data.get("selector"), dict)
                and data["selector"]
                and all(
                    isinstance(key, str) and isinstance(value, str)
                    for key, value in data["selector"].items()
                )
                and isinstance(data.get("old_pods"), list)
                and all(isinstance(value, str) and value for value in data["old_pods"])
                and isinstance(data.get("pod"), dict)
                and (
                    not data["pod"]
                    or (
                        set(data["pod"]) == {"name", "uid"}
                        and all(isinstance(value, str) and value for value in data["pod"].values())
                    )
                )
                and type(data.get("annotations_present")) is bool
                and type(data.get("template_annotations_present")) is bool
                and data.get("phase") in {"intent", "fenced", "restoring"}
                and data.get("process") in {None, "serve", "garbage-collect"}
                and (data["process"] is None or data["pod"])
                and (data["phase"] != "restoring" or _saved_flags(data.get("restore_flags"))),
                "invalid registry recovery journal",
            )
            assert isinstance(data, dict)
            return data
        except Exception:
            raise RegistryMaintenanceError("registry recovery journal is invalid") from None

    def _owned(self, current: dict[str, Any]) -> None:
        assert self._state is not None
        _require(
            current["metadata"].get("uid") == self._state["uid"], "registry Deployment UID changed"
        )
        for metadata in (current["metadata"], current["spec"]["template"]["metadata"]):
            _require(
                (metadata.get("annotations") or {}).get(OWNER_ANNOTATION) == self._state["owner"],
                "registry maintenance owner changed",
            )

    def _pod_layout_matches(self, current: dict[str, Any], pod: dict[str, Any]) -> bool:
        wanted = current["spec"]["template"]["spec"]
        live = pod.get("spec") or {}
        if any(
            live.get(key)
            for key in (
                "initContainers",
                "ephemeralContainers",
                "hostPID",
                "hostNetwork",
                "shareProcessNamespace",
            )
        ):
            return False
        containers = live.get("containers") or []
        if len(containers) != 1:
            return False
        if any(
            containers[0].get(key) != wanted["containers"][0].get(key)
            for key in ("name", "image", "env", "command", "args")
        ):
            return False
        mounts = {item["name"]: item for item in containers[0].get("volumeMounts", [])}
        volumes = {item["name"]: item for item in live.get("volumes", [])}
        for item in wanted["containers"][0].get("volumeMounts", []):
            if mounts.pop(item["name"], None) != item:
                return False
        for item in wanted.get("volumes", []):
            if volumes.pop(item["name"], None) != item:
                return False
        # Kubernetes may inject its projected ServiceAccount token. It cannot
        # replace config/storage/auth or introduce a second process.
        return all(
            item.get("mountPath") == "/var/run/secrets/kubernetes.io/serviceaccount"
            and item.get("readOnly") is True
            and name in volumes
            and set(volumes[name]) == {"name", "projected"}
            for name, item in mounts.items()
        ) and set(volumes) == set(mounts)

    def _ready(self, current: dict[str, Any], pods: list[dict[str, Any]]) -> bool:
        assert self._state is not None
        if len(pods) != 1:
            return False
        pod = pods[0]
        metadata, status = pod.get("metadata") or {}, pod.get("status") or {}
        if not self._pod_layout_matches(current, pod):
            return False
        if any(not metadata.get(key) for key in ("uid", "name")) or metadata.get(
            "deletionTimestamp"
        ):
            return False
        if (metadata.get("annotations") or {}).get(OWNER_ANNOTATION) != self._state["owner"]:
            return False
        if metadata["uid"] in self._state["old_pods"]:
            return False
        observed = current.get("status") or {}
        return bool(
            status.get("phase") == "Running"
            and any(
                item.get("type") == "Ready" and item.get("status") == "True"
                for item in status.get("conditions", [])
            )
            and len(status.get("containerStatuses", [])) == 1
            and status["containerStatuses"][0].get("ready") is True
            and "running" in status["containerStatuses"][0].get("state", {})
            and observed.get("observedGeneration", 0) >= current["metadata"]["generation"]
            and all(
                observed.get(key, 0) == 1
                for key in ("replicas", "updatedReplicas", "readyReplicas", "availableReplicas")
            )
        )

    def _fence(self) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        assert self._state is not None
        current = self._read()
        self._owned(current)
        _layout(current)
        _require(
            _fingerprint(current["spec"]["template"]) == self._state["template"],
            "registry pod template changed during maintenance",
        )
        return current, self._pods()

    def _check_config(self, pod: dict[str, Any]) -> None:
        assert self._state is not None
        _require(
            _config(self._exec(pod, ["cat", CONFIG_PATH])) == self._state["config"],
            "registry configuration changed during maintenance",
        )

    def __enter__(self) -> RegistryMaintenance:
        _require(self._state is None, "registry maintenance lease cannot be reused")
        _require(
            not self.journal_path.exists() and not self.journal_path.is_symlink(),
            "registry recovery journal already exists",
        )
        current = self._read()
        try:
            container_name, claim, container = _layout(current)
            metadata = current["spec"]["template"]["metadata"]
            _require(
                not any(
                    (item.get("annotations") or {}).get(OWNER_ANNOTATION)
                    for item in (current["metadata"], metadata)
                ),
                "registry maintenance is already owned",
            )
            selector = current["spec"]["selector"]
            _require(
                not selector.get("matchExpressions") and selector.get("matchLabels"),
                "unsupported registry selector",
            )
            self._state = {
                "version": 1,
                "owner": secrets.token_hex(16),
                "namespace": self.namespace,
                "deployment": self.deployment,
                "uid": current["metadata"]["uid"],
                "container": container_name,
                "claim": claim,
                "selector": selector["matchLabels"],
                "original": {key: deepcopy(_env(container).get(key)) for key in _FLAGS},
                "annotations_present": "annotations" in current["metadata"],
                "template_annotations_present": "annotations" in metadata,
                "old_pods": [],
                "pod": {},
                "process": None,
                "phase": "intent",
            }
            pods = self._pods()
            _require(
                len(pods) == 1
                and pods[0].get("status", {}).get("phase") == "Running"
                and not pods[0]["metadata"].get("deletionTimestamp"),
                "registry requires one existing running PVC consumer",
            )
            self._run("preflight", pods[0])
            self._state["config"] = _config(self._exec(pods[0], ["cat", CONFIG_PATH]))
            self._state["old_pods"] = [item["metadata"]["uid"] for item in pods]
            patches = _cas(current)
            patches.append(
                _annotation_patch(current["metadata"], "/metadata", self._state["owner"])
            )
            patches.append(
                _annotation_patch(metadata, "/spec/template/metadata", self._state["owner"])
            )
            patches.extend(
                _flag_patch(
                    container,
                    0,
                    {key: {"name": key, "value": value} for key, value in _FLAGS.items()},
                )
            )
            self._save(create=True)
            fenced = self._patch(patches)
            self._owned(fenced)
            self._state["template"] = _fingerprint(fenced["spec"]["template"])
            self._state["phase"] = "fenced"
            self._save()
            deadline = self._clock() + self._timeout
            while True:
                fenced, pods = self._fence()
                if self._ready(fenced, pods):
                    self._state["pod"] = {key: pods[0]["metadata"][key] for key in ("name", "uid")}
                    self._save()
                    self._check_config(pods[0])
                    _require(self._run("probe").strip() == "0", "unexpected registry writer")
                    return self
                _require(self._clock() < deadline, "read-only registry rollout timed out")
                self._sleep(min(self._poll, max(0, deadline - self._clock())))
        except Exception:
            if self._state is not None and self.journal_path.exists():
                try:
                    self.restore()
                except Exception:
                    raise RegistryMaintenanceError(
                        "registry acquisition failed; recovery required"
                    ) from None
            self._state = None
            raise

    def assert_read_only(self) -> None:
        _require(
            self._state is not None and self.pod_uid, "registry maintenance lease is not active"
        )
        assert self._state is not None
        current, pods = self._fence()
        _require(
            self._ready(current, pods) and pods[0]["metadata"]["uid"] == self.pod_uid,
            "registry read-only pod changed or is not ready",
        )
        self._check_config(pods[0])
        self._run("probe")

    def start_private_writer(self) -> None:
        self.assert_read_only()
        assert self._state is not None
        _require(self._run("writer").strip() == "0", "registry private writer already exists")
        self._state["process"] = "serve"
        self._save()
        result = self._run("start").strip()
        _require(
            result.isascii() and result.isdigit() and int(result) > 1,
            "registry private writer identity is invalid",
        )
        self._state["pid"] = int(result)
        self._save()

    def delete_manifest(self, repository: str, digest: str, authorization: str) -> int:
        # Validate before constructing either an HTTP request or a shell command.
        try:
            project = repository.removeprefix("max-app/")
            _require(
                repository == "max-app/" + str(UUID(project)) and _DIGEST.fullmatch(digest),
                "invalid application registry manifest",
            )
            _require(
                len(authorization) <= 8192
                and re.fullmatch(r"Basic [A-Za-z0-9+/]+={0,2}", authorization),
                "invalid registry authorization",
            )
            _require(
                b":" in base64.b64decode(authorization[6:], validate=True),
                "invalid registry authorization",
            )
        except Exception:
            raise RegistryMaintenanceError(
                "invalid application manifest or authorization"
            ) from None
        self.assert_read_only()
        assert self._state is not None
        _require(
            self._state.get("process") == "serve"
            and self._run("writer").strip() == str(self._state.get("pid")),
            "registry private writer is not owned",
        )
        request = (
            f"DELETE /v2/{repository}/manifests/{digest} HTTP/1.1\r\n"
            f"Host: 127.0.0.1:5001\r\nAuthorization: {authorization}\r\n"
            "Content-Length: 0\r\nConnection: close\r\n\r\n"
        )
        response = self._exec(
            {"metadata": self._state["pod"]}, ["nc", "-w", "10", "127.0.0.1", "5001"], stdin=request
        )
        status = re.match(r"HTTP/1\.[01] ([1-5][0-9]{2})[ \r\n]", response)
        _require(status, "invalid registry manifest deletion response")
        assert status is not None
        return int(status[1])

    def stop_private_writer(self) -> None:
        self.assert_read_only()
        assert self._state is not None
        self._run("stop")
        _require(self._run("writer").strip() == "0", "registry private writer remains alive")
        self._state["process"] = None
        self._state.pop("pid", None)
        self._save()

    def _allocated_bytes(self) -> int:
        result = self._run("du")
        match = re.fullmatch(r"([0-9]+)\s+/var/lib/registry\s*", result)
        _require(match, "invalid registry allocated-byte observation")
        assert match is not None
        return int(match[1]) * 1024

    def garbage_collect(self) -> dict[str, int]:
        self.assert_read_only()
        assert self._state is not None
        _require(
            self._state.get("process") is None and self._run("writer").strip() == "0",
            "registry private writer must stop before garbage collection",
        )
        before = self._allocated_bytes()
        self._state["process"] = "garbage-collect"
        self._save()
        self._run("gc")
        _require(self._run("writer").strip() == "0", "registry garbage collector remains alive")
        self._state["process"] = None
        self._save()
        self.assert_read_only()
        after = self._allocated_bytes()
        return {"bytes_before": before, "bytes_after": after, "bytes_freed": max(0, before - after)}

    def restore(self) -> None:
        """Stop owned processes, then CAS only our flags and annotations back."""
        if self._state is None:
            return
        current = self._read()
        _require(
            current["metadata"].get("uid") == self._state["uid"], "registry Deployment UID changed"
        )
        owners = [
            (item.get("annotations") or {}).get(OWNER_ANNOTATION)
            for item in (current["metadata"], current["spec"]["template"]["metadata"])
        ]
        # A crash can precede acquisition or follow the final patch response.
        # Retire an already reconciled journal without mutating an unowned object.
        if (
            not any(owners)
            and self._state.get("phase") in {"intent", "restoring"}
            and self._state.get("process") is None
        ):
            containers = current["spec"]["template"]["spec"]["containers"]
            matching = [
                value for value in containers if value.get("name") == self._state["container"]
            ]
            expected = self._state.get("restore_flags", self._state["original"])
            if len(matching) == 1 and all(
                _env(matching[0]).get(key) == expected[key] for key in _FLAGS
            ):
                self._retire_journal()
                return
        self._owned(current)
        if self._state["pod"]:
            pods = self._pods()
            # Never enable writes while any PVC consumer has an unknown process.
            for pod in pods:
                _require(
                    (pod["metadata"].get("annotations") or {}).get(OWNER_ANNOTATION)
                    == self._state["owner"],
                    "unowned registry pod prevents recovery",
                )
                self._run("stop", pod)
                self._run("clean", pod)
            self._state["process"] = None
            self._state.pop("pid", None)
            self._save()
        # Re-read after stopping: preserve unrelated updates and use their fresh RV.
        current = self._read()
        self._owned(current)
        containers = current["spec"]["template"]["spec"]["containers"]
        matches = [
            (index, value)
            for index, value in enumerate(containers)
            if value.get("name") == self._state["container"]
        ]
        _require(len(matches) == 1, "registry container changed during recovery")
        index, container = matches[0]
        environment = _env(container)
        replacements = {
            key: self._state["original"][key]
            for key, value in _FLAGS.items()
            if environment.get(key) == {"name": key, "value": value}
        }
        patches = _cas(current)
        patches.extend(_flag_patch(container, index, replacements))
        patches.append(
            _annotation_patch(
                current["metadata"], "/metadata", None, self._state["annotations_present"]
            )
        )
        patches.append(
            _annotation_patch(
                current["spec"]["template"]["metadata"],
                "/spec/template/metadata",
                None,
                self._state["template_annotations_present"],
            )
        )
        self._state["phase"] = "restoring"
        self._state["restore_flags"] = {
            key: replacements.get(key, environment.get(key)) for key in _FLAGS
        }
        self._save()
        self._patch(patches)
        self._retire_journal()

    def _retire_journal(self) -> None:
        assert self._state is not None
        owned = self._load()
        _require(owned["owner"] == self._state["owner"], "registry journal ownership changed")
        self.journal_path.unlink()
        self._state = None

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        try:
            self.restore()
        finally:
            if self._owns_api:
                assert isinstance(self._api, KubernetesRegistryApi)
                self._api.close()


def recover(
    kubeconfig_path: str,
    journal_path: Path,
    *,
    namespace: str = "registry",
    deployment: str = "registry",
    context: str | None = None,
    api: RegistryApi | None = None,
    timeout_seconds: float = 180,
) -> bool:
    """Recover an interrupted job; never take over a changed UID or owner."""
    path = Path(journal_path)
    if not path.exists() and not path.is_symlink():
        return False
    lease = RegistryMaintenance(
        kubeconfig_path,
        path,
        namespace=namespace,
        deployment=deployment,
        context=context,
        api=api,
        timeout_seconds=timeout_seconds,
    )
    try:
        lease._state = lease._load()
        lease.restore()
        return True
    finally:
        if lease._owns_api:
            assert isinstance(lease._api, KubernetesRegistryApi)
            lease._api.close()
