"""Isolated read-only Kubernetes public configuration observer."""

from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any
from uuid import UUID

# -I intentionally ignores PYTHONPATH and the working directory. Only this
# controller-owned source root is added when the helper runs as a standalone file.
if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from yleum_orchestrator.services.app_business_config import (
    APP_CONFIG_PATH,
    MAX_APP_CONFIG_BYTES,
    config_digest,
)

OBSERVER_SCRIPT = Path(__file__).resolve()
OBSERVATION_TIMEOUT_SECONDS = 4.0
MAX_OBSERVER_INPUT_BYTES = 2048
MAX_OBSERVER_OUTPUT_BYTES = 256
# The app SDK validates config_version as a nonnegative JavaScript safe integer.
MAX_APP_CONFIG_VERSION = 2**53 - 1


def _unconfirmed() -> dict[str, bool]:
    return {"core_matches": False, "app_matches": False}


def observe_in_subprocess(
    *,
    kubeconfig_path: str,
    context: str | None,
    namespace: str,
    release_id: str,
    expected_digest: str,
    config_version: int,
) -> dict[str, bool]:
    """Hard-bound even a hung TLS/WebSocket handshake; no observer threads or raw file output."""
    request = json.dumps(
        {
            "kubeconfig_path": kubeconfig_path,
            "context": context,
            "namespace": namespace,
            "release_id": release_id,
            "expected_digest": expected_digest,
            "config_version": config_version,
        },
        separators=(",", ":"),
    ).encode("utf-8")
    if len(request) > MAX_OBSERVER_INPUT_BYTES:
        return _unconfirmed()
    deadline = time.monotonic() + OBSERVATION_TIMEOUT_SECONDS
    process = None
    try:
        process = subprocess.Popen(
            [sys.executable, "-I", str(OBSERVER_SCRIPT)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            close_fds=True,
            start_new_session=os.name == "posix",
        )
        assert process.stdin is not None and process.stdout is not None
        # At most 2 KiB fits the OS pipe even if child startup/handshake stalls.
        process.stdin.write(request)
        process.stdin.close()
        process.wait(timeout=max(0, deadline - time.monotonic()))
        if process.returncode != 0:
            return _unconfirmed()
        # Do not drain concurrently: a flooding child blocks on the bounded pipe
        # and is killed at the deadline. Nonblocking read also rejects an inherited
        # pipe held open by a descendant instead of waiting for EOF indefinitely.
        os.set_blocking(process.stdout.fileno(), False)
        output = os.read(process.stdout.fileno(), MAX_OBSERVER_OUTPUT_BYTES + 1)
        if len(output) > MAX_OBSERVER_OUTPUT_BYTES:
            return _unconfirmed()
        result = json.loads(output)
        if (
            not isinstance(result, dict)
            or set(result) != {"core_matches", "app_matches"}
            or any(type(value) is not bool for value in result.values())
        ):
            return _unconfirmed()
        return {"core_matches": result["core_matches"], "app_matches": result["app_matches"]}
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return _unconfirmed()
    finally:
        if process is not None:
            if sys.platform != "win32":
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            elif process.poll() is None:
                process.kill()
            process.wait()
            if process.stdin is not None:
                process.stdin.close()
            if process.stdout is not None:
                process.stdout.close()


class KubernetesConfigObserver:
    """Private child-only fixed file reader; this object never crosses the process boundary."""

    def __init__(self, core: Any, configuration: Any) -> None:
        self._core = core
        self._configuration = configuration

    def observe(
        self,
        namespace: str,
        release_id: str,
        expected_digest: str,
        config_version: int,
    ) -> dict[str, bool]:
        result = _unconfirmed()
        files = self.read_config_files(namespace, release_id)
        if files is None:
            return result
        for role in ("core", "app"):
            payload = files.get(role)
            if payload is None or len(payload) > MAX_APP_CONFIG_BYTES:
                continue
            try:
                value = json.loads(payload)
                if role == "app":
                    if (
                        not isinstance(value, dict)
                        or type(value.get("config_version")) is not int
                        or value["config_version"] != config_version
                    ):
                        continue
                    value = value.get("config")
                result[f"{role}_matches"] = (
                    isinstance(value, dict) and config_digest(value) == expected_digest
                )
            except (ValueError, TypeError, UnicodeError, RecursionError):
                continue
        return result

    def _exec_config_file(
        self,
        namespace: str,
        pod: str,
        command: list[str],
        *,
        container: str,
        timeout_seconds: float,
    ) -> bytes | None:
        """Drain a fixed reader one frame at a time; stop before accumulating oversized output."""
        from kubernetes import client as k8s_client
        from kubernetes.stream import stream

        deadline = time.monotonic() + timeout_seconds
        client = None
        api_client = k8s_client.ApiClient(configuration=self._configuration)
        try:
            # stream temporarily replaces ApiClient.request; never share it with status/apply calls.
            core = k8s_client.CoreV1Api(api_client)
            client = stream(
                core.connect_get_namespaced_pod_exec,
                pod,
                namespace,
                command=command,
                container=container,
                stderr=True,
                stdin=False,
                stdout=True,
                tty=False,
                _preload_content=False,
                _request_timeout=timeout_seconds,
            )
            payload = bytearray()
            while client.is_open():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                client.update(timeout=min(remaining, 0.1))
                chunk = (client.read_stdout(timeout=0) or "").encode("utf-8")
                if len(payload) + len(chunk) > MAX_APP_CONFIG_BYTES:
                    return None
                if client.read_stderr(timeout=0):
                    return None
                payload.extend(chunk)
            return bytes(payload) if client.returncode == 0 else None
        except Exception:
            return None
        finally:
            if client is not None:
                client.close()
            api_client.close()

    def read_config_files(self, namespace: str, release_id: str) -> dict[str, bytes] | None:
        """Read two fixed public files while the same containers remain the sole serving pods."""
        deadline = time.monotonic() + OBSERVATION_TIMEOUT_SECONDS

        def remaining() -> float:
            seconds = deadline - time.monotonic()
            if seconds <= 0:
                raise TimeoutError("public configuration observation timed out")
            return seconds

        def serving() -> dict[str, tuple[str, str, str, int]] | None:
            identities = {}
            for role in ("core", "app"):
                pods = self._core.list_namespaced_pod(
                    namespace,
                    label_selector=f"app.kubernetes.io/component={role}",
                    _request_timeout=remaining(),
                ).items
                ready = []
                for pod in pods:
                    metadata, status = pod.metadata, pod.status
                    if (
                        metadata is None
                        or status is None
                        or metadata.deletion_timestamp
                        or status.phase != "Running"
                        or not metadata.uid
                        or not metadata.name
                        or (metadata.labels or {}).get("omnia.release-id") != release_id
                    ):
                        continue
                    containers = [
                        item
                        for item in (status.container_statuses or [])
                        if item.name == role and item.ready and item.container_id
                    ]
                    if len(containers) == 1:
                        container = containers[0]
                        ready.append(
                            (
                                metadata.name,
                                metadata.uid,
                                container.container_id,
                                container.restart_count,
                            )
                        )
                if len(ready) != 1:
                    return None
                endpoints = self._core.read_namespaced_endpoints(
                    role, namespace, _request_timeout=remaining()
                )
                targets = [
                    address.target_ref.uid
                    if address.target_ref and address.target_ref.kind == "Pod"
                    else None
                    for subset in (endpoints.subsets or [])
                    for address in (subset.addresses or [])
                ]
                if targets != [ready[0][1]]:
                    return None
                identities[role] = ready[0]
            return identities

        # Absolute interpreters, isolated Python, fixed paths and bounded regular-file reads.
        # Neither an owner path nor a shell command crosses this boundary.
        commands = {
            "app": [
                "/usr/bin/python3",
                "-I",
                "-S",
                "-c",
                "import os,stat,sys; "
                f"f=os.open({APP_CONFIG_PATH!r},os.O_RDONLY|os.O_NONBLOCK); "
                f"s=os.fstat(f); limit={MAX_APP_CONFIG_BYTES}; "
                "sys.exit(1) if not stat.S_ISREG(s.st_mode) or s.st_size>limit else None; "
                "data=os.read(f,limit+1); os.close(f); "
                "sys.exit(1) if len(data)>limit else None; sys.stdout.buffer.write(data)",
            ],
            "core": [
                "/usr/local/bin/node",
                "-e",
                "const fs=require('node:fs'); "
                "const f=fs.openSync('/app/omnia-business-config.json',fs.constants.O_RDONLY|"
                "fs.constants.O_NONBLOCK); "
                f"const s=fs.fstatSync(f),limit={MAX_APP_CONFIG_BYTES}; "
                "if(!s.isFile()||s.size>limit)process.exit(1); "
                "const b=Buffer.alloc(limit+1),n=fs.readSync(f,b,0,b.length,null); "
                "fs.closeSync(f); if(n>limit)process.exit(1); "
                "process.stdout.write(b.subarray(0,n));",
            ],
        }
        try:
            before = serving()
            if before is None:
                return None
            files = {}
            for role in ("core", "app"):
                payload = self._exec_config_file(
                    namespace,
                    before[role][0],
                    commands[role],
                    container=role,
                    timeout_seconds=remaining(),
                )
                if payload is None:
                    return None
                files[role] = payload
            return files if serving() == before else None
        except Exception:
            # Do not expose data or cluster errors in owner output.
            return None


def _main() -> None:
    """Private child protocol: bounded input, controller kubeconfig, fixed boolean-only output."""
    result = _unconfirmed()
    try:
        payload = sys.stdin.buffer.read(MAX_OBSERVER_INPUT_BYTES + 1)
        if len(payload) > MAX_OBSERVER_INPUT_BYTES:
            raise ValueError("observer input budget")
        request = json.loads(payload)
        if not isinstance(request, dict) or set(request) != {
            "kubeconfig_path",
            "context",
            "namespace",
            "release_id",
            "expected_digest",
            "config_version",
        }:
            raise ValueError("observer request shape")
        if (
            not isinstance(request["kubeconfig_path"], str)
            or not request["kubeconfig_path"]
            or not isinstance(request["namespace"], str)
            or not request["namespace"].startswith("app-")
            or not isinstance(request["release_id"], str)
            or not request["release_id"]
            or not isinstance(request["expected_digest"], str)
            or re.fullmatch(r"[a-f0-9]{64}", request["expected_digest"]) is None
            or type(request["config_version"]) is not int
            or not 0 <= request["config_version"] <= MAX_APP_CONFIG_VERSION
            or not (request["context"] is None or isinstance(request["context"], str))
        ):
            raise ValueError("observer request values")
        UUID(request["namespace"][4:])
        from kubernetes import client, config

        api_client = config.new_client_from_config(
            config_file=request["kubeconfig_path"],
            context=request["context"],
        )
        try:
            result = KubernetesConfigObserver(
                client.CoreV1Api(api_client),
                api_client.configuration,
            ).observe(
                request["namespace"],
                request["release_id"],
                request["expected_digest"],
                request["config_version"],
            )
        finally:
            api_client.close()
    except Exception:
        # No exception text, URLs, file contents or credentials leave the child.
        pass
    sys.stdout.write(json.dumps(result, separators=(",", ":")))


if __name__ == "__main__":
    _main()
