"""Host-only project DEKs: durable Vault envelopes, ephemeral trusted-core keyrings.

The Transit key must be provisioned externally. No key/token enters generated apps.
Runtime key files require tmpfs; operators must also disable swap (tmpfs can swap).
This provider does not encrypt block devices or replace encrypted-volume provisioning.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
import re
import secrets
import stat
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlsplit
from uuid import UUID

import httpx

_MAX_BYTES = 1024 * 1024
_MAX_VERSIONS = 32
_COMPONENT = re.compile(r"[A-Za-z0-9_-]+")


class AppDataKeyError(RuntimeError):
    """Safe operational error: never includes Vault response bodies or key material."""


@dataclass(frozen=True)
class VaultDataKeyConfig:
    address: str
    token_file: Path
    mount: str
    key_name: str
    wrapped_root: Path
    runtime_root: Path


def _no_symlinks(path: Path) -> None:
    if not path.is_absolute() or ".." in path.parts:
        raise AppDataKeyError("Key storage paths must be absolute and contained")
    for entry in [*reversed(path.parents), path]:
        try:
            mode = entry.lstat().st_mode
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(mode) or entry.is_junction():
            raise AppDataKeyError("Key storage symlinks are forbidden")


def _private_directory(path: Path) -> None:
    _no_symlinks(path)
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    info = path.stat()
    if not stat.S_ISDIR(info.st_mode):
        raise AppDataKeyError("Key storage directory is invalid")
    if sys.platform != "win32" and (info.st_uid != os.geteuid() or info.st_mode & 0o077):
        raise AppDataKeyError("Key storage directory must be owned by host and private")


def _require_tmpfs(path: Path) -> None:
    """Find the deepest containing Linux mount, including an overlaid submount."""
    _no_symlinks(path)
    if sys.platform != "linux":
        raise AppDataKeyError("Runtime key directory requires Linux tmpfs")
    try:
        mounts = Path("/proc/self/mountinfo").read_text().splitlines()
        selected: tuple[int, str] | None = None
        for line in mounts:
            before, after = line.split(" - ", 1)
            raw_mount = before.split()[4]
            mount = Path(re.sub(r"\\([0-7]{3})", lambda m: chr(int(m[1], 8)), raw_mount))
            if path == mount or mount in path.parents:
                candidate = (len(mount.parts), after.split()[0])
                if selected is None or candidate[0] >= selected[0]:
                    selected = candidate
        if selected is None or selected[1] != "tmpfs":
            raise AppDataKeyError("Runtime key directory must use tmpfs")
    except (OSError, ValueError, IndexError):
        raise AppDataKeyError("Unable to verify runtime tmpfs mount") from None


def _read_private(path: Path, *, max_bytes: int = _MAX_BYTES, public: bool = False) -> bytes:
    _no_symlinks(path)
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise AppDataKeyError("Key storage file must be regular with a single hard link")
        if sys.platform != "win32":
            allowed = 0o444 if public else 0o600
            if (
                info.st_uid != os.geteuid()
                or stat.S_IMODE(info.st_mode) & ~allowed
                or (public and stat.S_IMODE(info.st_mode) != 0o444)
            ):
                raise AppDataKeyError("Key storage file permissions are unsafe")
        value = stream.read(max_bytes + 1)
        if len(value) > max_bytes:
            raise AppDataKeyError("Key storage file exceeds size limit")
        return value


def _atomic_write(path: Path, value: bytes, *, mode: int) -> None:
    _no_symlinks(path)
    temporary = path.parent / f".{path.name}.{secrets.token_hex(12)}.tmp"
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())
            if sys.platform != "win32":
                os.fchmod(stream.fileno(), mode)
        os.replace(temporary, path)
        if sys.platform != "win32":
            directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
    finally:
        temporary.unlink(missing_ok=True)


@contextmanager
def _project_lock(path: Path) -> Iterator[None]:
    _no_symlinks(path)
    descriptor = os.open(path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(descriptor, "r+b") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise AppDataKeyError("Project lock file is invalid")
        if sys.platform != "win32":
            import fcntl

            if info.st_uid != os.geteuid() or info.st_mode & 0o077:
                raise AppDataKeyError("Project lock permissions are unsafe")
        else:
            import msvcrt

            if info.st_size == 0:
                stream.write(b"\0")
                stream.flush()
        deadline = time.monotonic() + 30
        while True:
            try:
                if sys.platform != "win32":
                    fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                else:
                    stream.seek(0)
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise AppDataKeyError("Timed out waiting for project key lock") from None
                time.sleep(0.05)
        try:
            yield
        finally:
            if sys.platform != "win32":
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
            else:
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)


def _encoded(value: dict[str, Any]) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


class AppDataKeyManager:
    def __init__(self, config: VaultDataKeyConfig, *, client: httpx.Client | None = None):
        self.config = config
        try:
            url = urlsplit(config.address)
            valid = (
                url.scheme == "https"
                and url.hostname
                and not url.username
                and not url.password
                and not url.query
                and not url.fragment
                and url.path in ("", "/")
                and _COMPONENT.fullmatch(config.mount)
                and _COMPONENT.fullmatch(config.key_name)
                and not any(c.isspace() for c in config.address)
            )
            _ = url.port
        except ValueError:
            valid = False
        if not valid:
            raise AppDataKeyError("Vault address or Transit key configuration is invalid")
        for path in (config.token_file, config.wrapped_root, config.runtime_root):
            _no_symlinks(path)
        if (
            config.wrapped_root == config.runtime_root
            or config.wrapped_root in config.runtime_root.parents
            or config.runtime_root in config.wrapped_root.parents
        ):
            raise AppDataKeyError("Durable and ephemeral key storage must be separate")
        self._client = client

    def prepare(self, project_id: str, *, allow_create: bool = False) -> Path:
        """Unwrap existing keys. Creation requires an explicitly new project identity."""
        return self._prepare(project_id, allow_create=allow_create, rotate=False)

    def rotate(self, project_id: str) -> Path:
        """Retain every historical DEK; caller must recreate core with returned mount path."""
        return self._prepare(project_id, allow_create=False, rotate=True)

    def _request(
        self,
        client: httpx.Client,
        token: str,
        method: str,
        suffix: str,
        body: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        url = f"{self.config.address.rstrip('/')}/v1/{self.config.mount}/{suffix}"
        try:
            with client.stream(
                method,
                url,
                headers={"X-Vault-Token": token, "Accept-Encoding": "identity"},
                json=body,
                timeout=httpx.Timeout(10, connect=5),
                follow_redirects=False,
            ) as reply:
                if reply.status_code != 200:
                    raise AppDataKeyError(f"Vault request failed (HTTP {reply.status_code})")
                if reply.headers.get("content-encoding", "identity").lower() != "identity":
                    raise AppDataKeyError("Compressed Vault responses are not accepted")
                content = bytearray()
                deadline = time.monotonic() + 20
                for chunk in reply.iter_bytes():
                    if time.monotonic() > deadline:
                        raise AppDataKeyError("Vault response exceeded its time limit")
                    content.extend(chunk)
                    if len(content) > _MAX_BYTES:
                        raise AppDataKeyError("Vault response exceeds size limit")
                value = json.loads(content)
                if not isinstance(value, dict) or not isinstance(value.get("data"), dict):
                    raise AppDataKeyError("Vault returned invalid data")
                return cast(dict[str, Any], value["data"])
        except (httpx.HTTPError, ValueError, UnicodeError):
            raise AppDataKeyError("Vault request or response validation failed") from None

    def _read_ring(self, path: Path, project: str) -> dict[str, Any]:
        try:
            value = json.loads(_read_private(path))
            if not isinstance(value, dict):
                raise ValueError
            versions = value.get("keys")
            active = value.get("activeVersion")
            if (
                value.get("schema") != 1
                or value.get("projectId") != project
                or value.get("transit") != self._transit_identity()
                or type(active) is not int
                or not 1 <= active <= _MAX_VERSIONS
                or not isinstance(versions, dict)
                or set(versions) != {str(n) for n in range(1, active + 1)}
            ):
                raise ValueError
            for ciphertext in versions.values():
                if (
                    not isinstance(ciphertext, str)
                    or len(ciphertext) > 16384
                    or not re.fullmatch(r"vault:v[1-9][0-9]*:[A-Za-z0-9+/=_-]+", ciphertext)
                ):
                    raise ValueError
            return value
        except (ValueError, TypeError, UnicodeError):
            raise AppDataKeyError("Stored project keyring is invalid; recovery required") from None

    def _transit_identity(self) -> str:
        return f"{self.config.address.rstrip('/')}/v1/{self.config.mount}/{self.config.key_name}"

    def _prepare(self, project_id: str, *, allow_create: bool, rotate: bool) -> Path:
        try:
            project = str(UUID(project_id))
            if project != project_id:
                raise ValueError
        except (ValueError, AttributeError, TypeError):
            raise AppDataKeyError("Project identity must be a canonical UUID") from None
        try:
            _require_tmpfs(self.config.runtime_root)
            _private_directory(self.config.wrapped_root)
            _private_directory(self.config.runtime_root)
            with _project_lock(self.config.wrapped_root / f"{project}.lock"):
                ring_path = self.config.wrapped_root / f"{project}.json"
                _no_symlinks(ring_path)
                exists = ring_path.exists()
                if not exists and not allow_create:
                    raise AppDataKeyError(
                        "Established project keyring is missing; recovery required"
                    )
                ring = (
                    self._read_ring(ring_path, project)
                    if exists
                    else {
                        "schema": 1,
                        "projectId": project,
                        "activeVersion": 0,
                        "transit": self._transit_identity(),
                        "keys": {},
                    }
                )
                token = (
                    _read_private(self.config.token_file, max_bytes=8192).decode("ascii").strip()
                )
                if not token or any(ord(c) < 33 or ord(c) > 126 for c in token):
                    raise AppDataKeyError("Vault token file is invalid")
                if self._client is not None:
                    return self._materialize(self._client, token, ring, ring_path, rotate)
                with httpx.Client(verify=True, trust_env=False, follow_redirects=False) as client:
                    return self._materialize(client, token, ring, ring_path, rotate)
        except (OSError, UnicodeError):
            raise AppDataKeyError("Project key storage operation failed") from None

    def _materialize(
        self, client: httpx.Client, token: str, ring: dict[str, Any], ring_path: Path, rotate: bool
    ) -> Path:
        key_name = self.config.key_name
        policy = self._request(client, token, "GET", f"keys/{key_name}")
        if (
            policy.get("derived") is not True
            or policy.get("type") != "aes256-gcm96"
            or policy.get("exportable") is not False
            or policy.get("allow_plaintext_backup") is not False
        ):
            raise AppDataKeyError("Vault Transit key policy does not meet encryption requirements")
        context = base64.b64encode(ring["projectId"].encode("ascii")).decode("ascii")
        plaintext_keys: dict[str, str] = {}
        for version, ciphertext in ring["keys"].items():
            response = self._request(
                client,
                token,
                "POST",
                f"decrypt/{key_name}",
                {
                    "ciphertext": ciphertext,
                    "context": context,
                },
            )
            plaintext = response.get("plaintext")
            try:
                if (
                    not isinstance(plaintext, str)
                    or len(base64.b64decode(plaintext, validate=True)) != 32
                ):
                    raise ValueError
            except (ValueError, binascii.Error):
                raise AppDataKeyError("Vault returned invalid project key material") from None
            plaintext_keys[version] = plaintext
        changed = not plaintext_keys or rotate
        if changed:
            active = ring["activeVersion"] + 1
            if active > _MAX_VERSIONS:
                raise AppDataKeyError("Project key version limit reached")
            plaintext = base64.b64encode(secrets.token_bytes(32)).decode("ascii")
            response = self._request(
                client,
                token,
                "POST",
                f"encrypt/{key_name}",
                {
                    "plaintext": plaintext,
                    "context": context,
                },
            )
            ciphertext = response.get("ciphertext")
            if (
                not isinstance(ciphertext, str)
                or len(ciphertext) > 16384
                or not re.fullmatch(r"vault:v[1-9][0-9]*:[A-Za-z0-9+/=_-]+", ciphertext)
            ):
                raise AppDataKeyError("Vault returned invalid wrapped project key")
            ring["activeVersion"] = active
            ring["keys"][str(active)] = ciphertext
            plaintext_keys[str(active)] = plaintext
        runtime = self.config.runtime_root / cast(str, ring["projectId"])
        _require_tmpfs(runtime)
        _private_directory(runtime)
        content = _encoded(
            {
                "projectId": ring["projectId"],
                "activeVersion": str(ring["activeVersion"]),
                "keys": plaintext_keys,
            }
        )
        path = runtime / f"ring-{hashlib.sha256(content).hexdigest()}.json"
        _no_symlinks(path)
        if path.exists() and _read_private(path, public=True) != content:
            raise AppDataKeyError("Runtime project keyring content mismatch")
        if changed:
            _atomic_write(ring_path, _encoded(ring), mode=0o600)
        if not path.exists():
            _atomic_write(path, content, mode=0o444)
        return path
