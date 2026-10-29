"""Public business data bridge. No credentials, source changes or app restarts."""

from __future__ import annotations

import hashlib
import io
import json
import os
import tarfile
import tempfile
from copy import copy
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

APP_CONFIG_DIRECTORY = "/run/yleum-config"
APP_CONFIG_PATH = APP_CONFIG_DIRECTORY + "/business.json"
# Reserve space for Kubernetes metadata and JSON escaping below the 1 MiB object limit.
MAX_APP_CONFIG_BYTES = 900 * 1024
_PUBLIC_CONFIG_FIELDS = frozenset({
    "app_name", "app_type", "summary", "audience", "primary_action", "features", "style",
    "brand_colors", "content", "operator", "support", "legal",
})


def config_digest(config: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(config, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    ).hexdigest()


def app_config_envelope(config: dict[str, Any], version: int) -> bytes:
    if isinstance(version, bool) or not isinstance(version, int) or version < 0:
        raise ValueError("invalid application configuration version")
    if set(config) - _PUBLIC_CONFIG_FIELDS:
        raise ValueError("application configuration must contain only public business fields")
    payload = json.dumps(
        {"config_version": version, "config": config},
        separators=(",", ":"), ensure_ascii=True,
    ).encode()
    if len(payload) > MAX_APP_CONFIG_BYTES:
        raise ValueError("application configuration exceeds transport budget")
    return payload


def write_app_config(directory: Path, config: dict[str, Any], version: int) -> None:
    """Controller directory is mounted read-only and never captured with app source."""
    payload = app_config_envelope(config, version)
    if directory.is_symlink():
        raise ValueError("unsafe application configuration directory")
    directory.mkdir(parents=True, exist_ok=True, mode=0o755)
    path = directory / "business.json"
    if path.is_symlink():
        raise ValueError("unsafe application configuration file")
    fd, temporary = tempfile.mkstemp(prefix=".business-", dir=directory)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o644)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def app_file_matches(container: Any, config: dict[str, Any], version: int) -> bool:
    """Read one fixed actual in-container file; never an owner-supplied path."""
    expected = app_config_envelope(config, version)
    # Per-reader timeout; changing the shared client's timeout could affect writers.
    api = copy(container.client.api)
    api.timeout = 3
    chunks, _ = api.get_archive(container.id, APP_CONFIG_PATH, chunk_size=65536)
    archive = io.BytesIO()
    try:
        for chunk in chunks:
            if archive.tell() + len(chunk) > MAX_APP_CONFIG_BYTES + 65536:
                return False
            archive.write(chunk)
    finally:
        close = getattr(chunks, "close", None)
        if close:
            close()
    archive.seek(0)
    with tarfile.open(fileobj=archive, mode="r:") as contents:
        entries = contents.getmembers()
        if len(entries) != 1 or not entries[0].isfile():
            return False
        source = contents.extractfile(entries[0])
        return source is not None and source.read(MAX_APP_CONFIG_BYTES + 1) == expected


def core_config_matches(address: str, config: dict[str, Any]) -> bool:
    import http.client

    connection = http.client.HTTPConnection(address, 3000, timeout=3)
    try:
        connection.request("GET", "/api/omnia/config")
        response = connection.getresponse()
        raw = response.read(MAX_APP_CONFIG_BYTES + 1)
        return (
            response.status == 200 and len(raw) <= MAX_APP_CONFIG_BYTES
            and json.loads(raw) == config
        )
    finally:
        connection.close()


def observe_docker_config(
    backend: Any, config: dict[str, Any], version: int, *, release_id: str | None = None,
) -> dict[str, bool]:
    backend = copy(backend)
    backend.client = copy(backend.client)
    backend.client.api = copy(backend.client.api)
    backend.client.api.timeout = 3
    app = backend._container()
    core = backend._lookup(
        backend.client.containers, backend.stem + "-max-core", "managed-max-core",
    )
    gateway = backend._lookup(
        backend.client.containers, backend.stem + "-gateway", "max-gateway",
    )
    if any(container is None for container in (app, core, gateway)):
        return {"core_matches": False, "app_matches": False}
    containers = (app, core, gateway)

    def identities() -> tuple[Any, ...]:
        result = []
        for container in containers:
            container.reload()
            if container.status != "running":
                raise ValueError("application runtime is not running")
            result.append((container.id, container.attrs.get("State", {}).get("StartedAt")))
        return tuple(result)

    before = identities()
    if release_id is not None and (
        app.attrs.get("Config", {}).get("Labels", {}).get("omnia.public_release_id")
        != release_id
    ):
        return {"core_matches": False, "app_matches": False}
    mount = [entry for entry in app.attrs.get("Mounts", [])
             if entry.get("Destination") == APP_CONFIG_DIRECTORY]
    if (len(mount) != 1 or mount[0].get("RW") is not False
            or mount[0].get("Source") != str(backend.app_config_directory)):
        return {"core_matches": False, "app_matches": False}
    address = core.attrs["NetworkSettings"]["Networks"][backend.internal_network]["IPAddress"]
    core_matches = core_config_matches(address, config)
    app_matches = app_file_matches(app, config, version)
    if identities() != before:
        return {"core_matches": False, "app_matches": False}
    return {"core_matches": core_matches, "app_matches": app_matches}


def status_projection(
    state: str, desired: int, *, controller: int | None = None,
    confirmed: int | None = None, reason: str | None = None,
) -> dict[str, Any]:
    return {
        "state": state, "desired_config_version": desired,
        "controller_config_version": controller, "confirmed_config_version": confirmed,
        "checked_at": datetime.now(UTC).isoformat(), "reason": reason,
    }
