"""Private controller authority for independent project database credentials."""

from __future__ import annotations

import fcntl
import json
import os
import re
import secrets
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from uuid import UUID

from yleum_orchestrator.services.cell_state import (
    _set_file_mode,
    _validate_ancestor_dir_stat,
    _validate_dir_stat,
    _validate_regular_fd,
)

_FILE_MODE = 0o600
_DIR_MODE = 0o700
_MAX_RECORD_BYTES = 4096
_PASSWORD = re.compile(r"[A-Za-z0-9_-]{32,128}", re.ASCII)
_RECORD_KEYS = frozenset(
    {"version", "workspace_id", "runtime_password", "migrator_password", "admin_password"}
)
_FILE_FLAGS = os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK
_DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC


@dataclass(frozen=True, slots=True, repr=False)
class ProjectDatabaseCredentials:
    runtime_password: str
    migrator_password: str
    admin_password: str


class ProjectDatabaseCredentialStore:
    """Keep all three passwords in one durable, UUID-bound private record.

    This authority allocates credentials only; it does not install database roles.
    Readers and creators take the same per-workspace advisory lock. Staged records
    are fsynced before atomic publication, so peers see one complete winning bundle.
    """

    def __init__(self, root: str | Path) -> None:
        # Do not resolve: symlinks must be rejected during descriptor traversal.
        self.root = Path(os.path.abspath(root))

    def load_or_create(self, workspace_id: UUID) -> ProjectDatabaseCredentials:
        return self._access(workspace_id, create=True)

    def load(self, workspace_id: UUID) -> ProjectDatabaseCredentials:
        """Read an existing bundle; missing authority or records fail closed."""
        return self._access(workspace_id, create=False)

    def _access(self, workspace_id: UUID, *, create: bool) -> ProjectDatabaseCredentials:
        try:
            if type(workspace_id) is not UUID:
                raise ValueError("UUID required")
            with self._root_fd(create=create) as directory_fd:
                with _workspace_lock(directory_fd, workspace_id):
                    name = f"{workspace_id}.json"
                    info = _entry_stat(directory_fd, name)
                    if info is not None:
                        _require_regular(info)
                        credentials = _read_record(directory_fd, name, workspace_id)
                        # A prior publish may have renamed successfully before its
                        # directory sync failed. Confirm durability under this lock
                        # before either API returns the existing winning bundle.
                        os.fsync(directory_fd)
                        return credentials
                    if not create:
                        raise RuntimeError("credential record missing")
                    credentials = ProjectDatabaseCredentials(
                        runtime_password=secrets.token_urlsafe(32),
                        migrator_password=secrets.token_urlsafe(32),
                        admin_password=secrets.token_urlsafe(32),
                    )
                    payload = {
                        "version": 1,
                        "workspace_id": str(workspace_id),
                        **asdict(credentials),
                    }
                    _parse_record(payload, workspace_id)
                    _publish_record(directory_fd, name, payload)
                    return credentials
        except (OSError, RuntimeError, ValueError, UnicodeError):
            # Parse exceptions retain input in their attributes. Never expose them
            # through exception chaining, file paths, passwords, or DSNs.
            raise RuntimeError(
                "project database credential authority unavailable or invalid"
            ) from None

    @contextmanager
    def _root_fd(self, *, create: bool) -> Iterator[int]:
        directory_fd = os.open(self.root.anchor, _DIR_FLAGS)
        try:
            parts = self.root.parts[1:]
            if not parts:
                raise RuntimeError("private directory required")
            for index, part in enumerate(parts):
                if create:
                    try:
                        os.mkdir(part, _DIR_MODE, dir_fd=directory_fd)
                    except FileExistsError:
                        pass
                    else:
                        os.fsync(directory_fd)
                child_fd = os.open(part, _DIR_FLAGS, dir_fd=directory_fd)
                os.close(directory_fd)
                directory_fd = child_fd
                info = os.fstat(directory_fd)
                if index == len(parts) - 1:
                    _validate_dir_stat(info, self.root)
                    if stat.S_IMODE(info.st_mode) != _DIR_MODE:
                        raise RuntimeError("private directory mode required")
                else:
                    _validate_ancestor_dir_stat(info, Path(part))
            yield directory_fd
        finally:
            os.close(directory_fd)


def _entry_stat(directory_fd: int, name: str) -> os.stat_result | None:
    try:
        return os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
    except FileNotFoundError:
        return None


def _require_regular(info: os.stat_result) -> None:
    if not stat.S_ISREG(info.st_mode):
        raise RuntimeError("regular credential file required")


@contextmanager
def _workspace_lock(directory_fd: int, workspace_id: UUID) -> Iterator[None]:
    name = f"{workspace_id}.lock"
    info = _entry_stat(directory_fd, name)
    if info is not None:
        _require_regular(info)
    flags = os.O_RDWR | _FILE_FLAGS
    try:
        fd = os.open(name, flags | os.O_CREAT | os.O_EXCL, _FILE_MODE, dir_fd=directory_fd)
    except FileExistsError:
        fd = os.open(name, flags, dir_fd=directory_fd)
    try:
        _validate_regular_fd(fd, expected_mode=_FILE_MODE)
        fcntl.flock(fd, fcntl.LOCK_EX)
        _validate_regular_fd(fd, expected_mode=_FILE_MODE)
        info = _entry_stat(directory_fd, name)
        opened = os.fstat(fd)
        if info is None or (info.st_dev, info.st_ino) != (opened.st_dev, opened.st_ino):
            raise RuntimeError("credential lock changed")
        yield
    finally:
        os.close(fd)


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise RuntimeError("duplicate credential record key")
        result[key] = value
    return result


def _read_record(directory_fd: int, name: str, workspace_id: UUID) -> ProjectDatabaseCredentials:
    fd = os.open(name, os.O_RDONLY | _FILE_FLAGS, dir_fd=directory_fd)
    try:
        _validate_regular_fd(fd, expected_mode=_FILE_MODE)
        if os.fstat(fd).st_size > _MAX_RECORD_BYTES:
            raise RuntimeError("credential record too large")
        with os.fdopen(fd, "rb", closefd=False) as handle:
            encoded = handle.read(_MAX_RECORD_BYTES + 1)
        if len(encoded) > _MAX_RECORD_BYTES:
            raise RuntimeError("credential record too large")
        payload = json.loads(encoded, object_pairs_hook=_unique_json_object)
        return _parse_record(payload, workspace_id)
    finally:
        os.close(fd)


def _parse_record(payload: object, workspace_id: UUID) -> ProjectDatabaseCredentials:
    if not isinstance(payload, dict) or frozenset(payload) != _RECORD_KEYS:
        raise RuntimeError("invalid credential record structure")
    if type(payload["version"]) is not int or payload["version"] != 1:
        raise RuntimeError("invalid credential record version")
    if payload["workspace_id"] != str(workspace_id):
        raise RuntimeError("credential record identity mismatch")
    passwords = [
        payload[key] for key in ("runtime_password", "migrator_password", "admin_password")
    ]
    if any(not isinstance(value, str) or not _PASSWORD.fullmatch(value) for value in passwords):
        raise RuntimeError("invalid credential value")
    if len(set(passwords)) != 3:
        raise RuntimeError("database credentials must be independent")
    return ProjectDatabaseCredentials(*passwords)


def _publish_record(directory_fd: int, name: str, payload: dict[str, Any]) -> None:
    temporary = f".{name}.{secrets.token_hex(16)}.tmp"
    fd = os.open(
        temporary,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | _FILE_FLAGS,
        _FILE_MODE,
        dir_fd=directory_fd,
    )
    try:
        _set_file_mode(fd, _FILE_MODE)
        _validate_regular_fd(fd, expected_mode=_FILE_MODE)
        with os.fdopen(fd, "w", encoding="ascii", closefd=False) as handle:
            json.dump(payload, handle, separators=(",", ":"), ensure_ascii=True)
            handle.flush()
            os.fsync(fd)
        if _entry_stat(directory_fd, name) is not None:
            raise RuntimeError("credential record appeared during publication")
        os.replace(temporary, name, src_dir_fd=directory_fd, dst_dir_fd=directory_fd)
        os.fsync(directory_fd)
    finally:
        os.close(fd)
        try:
            os.unlink(temporary, dir_fd=directory_fd)
        except FileNotFoundError:
            pass
