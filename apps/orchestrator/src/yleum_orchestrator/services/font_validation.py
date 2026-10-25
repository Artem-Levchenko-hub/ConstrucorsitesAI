"""Validate untrusted font bytes without entering any generation reader cgroup."""

from __future__ import annotations

import json
import re
import socket
from contextlib import suppress
from time import monotonic
from typing import Any
from uuid import uuid4

import docker  # type: ignore[import-untyped]

from yleum_orchestrator.core.config import get_settings
from yleum_orchestrator.core.errors import OrchestratorError
from yleum_orchestrator.services.restoration_database import close_controller_socket

MAX_INPUT = 8 * 1024**2
MAX_OUTPUT = 2 * 1024**2
LIFETIME = 20
WORKER = "/app/src/yleum_api/services/font_validation_worker.py"
_PUBLIC_IMAGE_ENV = frozenset(
    {
        "PATH",
        "LANG",
        "GPG_KEY",
        "PYTHON_VERSION",
        "PYTHON_SHA256",
        "PYTHON_PIP_VERSION",
        "PYTHON_GET_PIP_URL",
        "PYTHON_GET_PIP_SHA256",
        "PYTHON_SETUPTOOLS_VERSION",
        "PYTHONUNBUFFERED",
        "PIP_NO_CACHE_DIR",
        "UV_LINK_MODE",
        "PLAYWRIGHT_BROWSERS_PATH",
    }
)


class FontValidationCleanupUnconfirmed(OrchestratorError):
    """Private exact job identity retained for admission reconciliation."""

    def __init__(self, name: str, nonce: str, image_id: str) -> None:
        super().__init__("container_failure", "Font validator cleanup unconfirmed", 503)
        self.name, self.nonce, self.image_id = name, nonce, image_id


def _remaining(deadline: float) -> float:
    value = deadline - monotonic()
    if value <= 0:
        raise TimeoutError("font validation deadline")
    return value


def _read_output(connection: Any, deadline: float) -> bytes:
    output, pending = bytearray(), bytearray()
    total = wire_bytes = 0
    while True:
        connection._sock.settimeout(_remaining(deadline))
        chunk = connection._sock.recv(65536)
        if not chunk:
            break
        wire_bytes += len(chunk)
        if wire_bytes > MAX_OUTPUT + 65536:
            raise ValueError("font stream budget")
        pending.extend(chunk)
        while len(pending) >= 8:
            size = int.from_bytes(pending[4:8], "big")
            if (
                pending[0] not in {1, 2}
                or pending[1:4] != b"\0\0\0"
                or size > MAX_OUTPUT
                or total + size > MAX_OUTPUT
            ):
                raise ValueError("font output budget")
            if len(pending) < size + 8:
                break
            total += size
            if pending[0] == 1:
                output.extend(pending[8 : size + 8])
            del pending[: size + 8]
    if pending:
        raise ValueError("truncated font output")
    return bytes(output)


def _remove_owned(client: Any, helper: Any, name: str, nonce: str, image_id: str) -> None:
    if helper is None:
        try:
            candidate = client.containers.get(name)
        except docker.errors.NotFound:
            return
        info = candidate.attrs
        if (
            info.get("Name", "").lstrip("/") != name
            or info.get("Image") != image_id
            or (info.get("Config", {}).get("Labels") or {}).get("yleum.font-job") != nonce
        ):
            raise ValueError("font job identity changed")
        helper = candidate
    helper.remove(force=True)


def reconcile_font_validation(failure: FontValidationCleanupUnconfirmed) -> bool:
    """No new job is admitted until the prior exact helper is absent or removed."""
    client = None
    try:
        client = docker.DockerClient(base_url=get_settings().docker_host, timeout=3)
        _remove_owned(client, None, failure.name, failure.nonce, failure.image_id)
        return True
    except Exception:
        return False
    finally:
        if client is not None:
            with suppress(Exception):
                client.close()


def _require_no_previous_job(client: Any) -> None:
    # A controller restart must not forget an unknown helper still starting/running.
    # Observe prior jobs only; never delete a container selected by a broad filter.
    for prior in client.containers.list(all=True, filters={"label": "yleum.font-job"}):
        state = prior.attrs.get("State", {})
        if state.get("Status") not in {"exited", "dead"} or any(
            state.get(flag) is not False for flag in ("Running", "Paused", "Restarting")
        ):
            raise OrchestratorError(
                "container_failure", "Previous font validator is not quiescent", 503
            )


def run_font_validation(raw: bytes) -> dict[str, Any]:
    """Fixed immutable image, argv, namespaces and lifetime; no project authority."""
    if not 0 < len(raw) <= MAX_INPUT:
        raise OrchestratorError("validation_failed", "Unsupported font input size", 422)
    settings = get_settings()
    release = settings.omnia_release_sha
    if not re.fullmatch(r"[0-9a-f]{40}", release):
        raise OrchestratorError("container_failure", "Font validator unavailable", 503)
    client = None
    helper = connection = None
    nonce = uuid4().hex
    name, image_id = "yleum-font-validation-" + nonce, ""
    creation_attempted = False
    deadline = monotonic() + LIFETIME
    try:
        client = docker.DockerClient(base_url=settings.docker_host, timeout=3)
        _require_no_previous_job(client)
        image = client.images.get("omnia-api:" + release)
        image_id = str(image.id)
        config = image.attrs.get("Config", {})
        env = config.get("Env") or []
        if (
            not re.fullmatch(r"sha256:[0-9a-f]{64}", image_id)
            or config.get("Volumes")
            or any(
                not isinstance(v, str) or v.split("=", 1)[0] not in _PUBLIC_IMAGE_ENV for v in env
            )
        ):
            raise ValueError("font image configuration")
        _remaining(deadline)
        creation_attempted = True
        # The configured image is an immutable ID, never an API reader tag.
        helper = client.containers.create(
            image_id,
            [
                "/usr/bin/timeout",
                "-s",
                "KILL",
                str(LIFETIME),
                "/app/.venv/bin/python",
                "-I",
                WORKER,
            ],
            name=name,
            labels={"yleum.font-job": nonce},
            entrypoint=[],
            stdin_open=True,
            tty=False,
            network_mode="none",
            read_only=True,
            cap_drop=["ALL"],
            privileged=False,
            security_opt=["no-new-privileges:true"],
            user="10001:10001",
            working_dir="/tmp",
            pids_limit=16,
            mem_limit=512 * 1024**2,
            memswap_limit=512 * 1024**2,
            nano_cpus=1_000_000_000,
            tmpfs={"/tmp": "rw,noexec,nosuid,nodev,size=64m,mode=1777"},
            environment={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "HOME": "/tmp"},
            use_config_proxy=False,
            healthcheck={"test": ["NONE"]},
            log_config=docker.types.LogConfig(type="none"),
        )
        connection = helper.attach_socket(
            params={"stdin": 1, "stdout": 1, "stderr": 1, "stream": 1}
        )
        helper.start()
        connection._sock.settimeout(_remaining(deadline))
        connection._sock.sendall(raw)
        connection._sock.shutdown(socket.SHUT_WR)
        output = _read_output(connection, deadline)
        outcome = helper.wait(timeout=_remaining(deadline))
        if outcome.get("StatusCode") != 0:
            raise OrchestratorError("validation_failed", "Font rejected by sanitizer", 422)
        data = json.loads(output)
        if type(data) is not dict:
            raise ValueError("font result shape")
        return data
    except OrchestratorError:
        raise
    except Exception:
        raise OrchestratorError(
            "container_failure", "Font validator temporarily unavailable", 503
        ) from None
    finally:
        try:
            if connection is not None:
                close_controller_socket(connection)
        finally:
            try:
                if creation_attempted:
                    _remove_owned(client, helper, name, nonce, image_id)
            except Exception:
                raise FontValidationCleanupUnconfirmed(name, nonce, image_id) from None
            finally:
                if client is not None:
                    with suppress(Exception):
                        client.close()
