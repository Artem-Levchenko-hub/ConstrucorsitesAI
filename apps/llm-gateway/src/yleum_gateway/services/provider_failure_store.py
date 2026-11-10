"""Bounded private POSIX packet storage; no logs, reads of packets, or cleanup.

The deployment creates the empty private root. Unsafe existing directories or
entries are never repaired here. Failed pending writes remain private evidence;
only a successfully published write loses its own temporary hardlink.
"""

from __future__ import annotations

import json
import os
import re
import stat
import time
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

_DIRECTORY = Path("/var/lib/yleum/provider-failures")
_MAX_PACKET_BYTES = 128 * 1024
_MAX_TOTAL_BYTES = 64 * 1024 * 1024
_MAX_FILES = 1024
_LOCK_WAIT_SECONDS = 0.2
_LOCK_POLL_SECONDS = 0.01
_IDENTITY = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
_NAME = re.compile(rf"(?:pending-)?{_IDENTITY}\.json\Z")


@dataclass(frozen=True)
class StoreResult:
    state: str
    artifact_id: str | None
    reason: str | None


class _Refused(Exception):
    def __init__(self, reason: str):
        self.reason = reason


def _open_directory() -> int:
    if os.name != "posix" or not _DIRECTORY.is_absolute():
        raise _Refused("store_unavailable")
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    descriptor = os.open("/", flags)
    try:
        for component in _DIRECTORY.parts[1:]:
            next_descriptor = os.open(component, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = next_descriptor
        info = os.fstat(descriptor)
        if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700:
            raise _Refused("unsafe_store")
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _regular(info: os.stat_result, *, limit: int) -> None:
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1
            or not 0 <= info.st_size <= limit):
        raise _Refused("unsafe_store")


def _lock(directory: int) -> int:
    import fcntl

    descriptor = os.open(".lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK,
                         0o600, dir_fd=directory)
    try:
        _regular(os.fstat(descriptor), limit=0)
        deadline = time.monotonic() + _LOCK_WAIT_SECONDS
        while True:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError as error:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise _Refused("store_busy") from error
                time.sleep(min(_LOCK_POLL_SECONDS, remaining))
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _capacity(directory: int, pending_bytes: int) -> None:
    count = total = 0
    with os.scandir(directory) as entries:
        for entry in entries:
            count += 1
            if count > _MAX_FILES:
                raise _Refused("store_capacity_exceeded")
            if entry.name != ".lock" and not _NAME.fullmatch(entry.name):
                raise _Refused("unsafe_store")
            info = os.stat(entry.name, dir_fd=directory, follow_symlinks=False)
            _regular(info, limit=0 if entry.name == ".lock" else _MAX_PACKET_BYTES)
            total += info.st_size
            if total > _MAX_TOTAL_BYTES:
                raise _Refused("store_capacity_exceeded")
    # Reserve the transient final+pending hardlink names too. This conservative
    # bound holds throughout publication, not only after successful unlink.
    if count + 2 > _MAX_FILES or total + 2 * pending_bytes > _MAX_TOTAL_BYTES:
        raise _Refused("store_capacity_exceeded")


def _encoded(packet: dict[str, object], artifact_id: str) -> bytes:
    if (type(packet) is not dict or type(packet.get("schema_version")) is not int
            or packet.get("schema_version") != 1
            or packet.get("diagnostic_visibility") != "operator_only"
            or not isinstance(packet.get("response_body"), dict)):
        raise _Refused("invalid_packet")
    body = packet["response_body"]
    if not isinstance(body, dict) or body.get("content_state") not in {
        "exact_utf8", "redacted", "withheld", "unavailable",
    }:
        raise _Refused("invalid_packet")
    if "utf8" in body and (not isinstance(body["utf8"], str)
                           or body["content_state"] not in {"exact_utf8", "redacted"}
                           or len(body["utf8"].encode("utf-8")) > 16 * 1024):
        raise _Refused("invalid_packet")
    raw = json.dumps({"artifact_id": artifact_id, "stored_at": datetime.now(UTC).isoformat(),
                      "packet": packet}, ensure_ascii=False, allow_nan=False,
                     separators=(",", ":")).encode("utf-8")
    if len(raw) > _MAX_PACKET_BYTES:
        raise _Refused("packet_budget_exceeded")
    return raw


def _publish(directory: int, artifact_id: str, raw: bytes) -> None:
    pending, final = f"pending-{artifact_id}.json", f"{artifact_id}.json"
    descriptor = os.open(pending, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                         0o600, dir_fd=directory)
    try:
        _regular(os.fstat(descriptor), limit=_MAX_PACKET_BYTES)
        with os.fdopen(descriptor, "wb", closefd=False) as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(descriptor)
        info = os.fstat(descriptor)
        _regular(info, limit=_MAX_PACKET_BYTES)
        if info.st_size != len(raw):
            raise _Refused("store_write_failed")
        os.link(pending, final, src_dir_fd=directory, dst_dir_fd=directory,
                follow_symlinks=False)
        os.unlink(pending, dir_fd=directory)
        os.fsync(directory)
    finally:
        os.close(descriptor)


def store_provider_failure(packet: dict[str, object]) -> StoreResult:
    """Store only the already sanitized private packet; never raise or retry."""
    directory = lock = None
    try:
        artifact_id = str(uuid4())
        raw = _encoded(packet, artifact_id)
        directory = _open_directory()
        lock = _lock(directory)
        _capacity(directory, len(raw))
        _publish(directory, artifact_id, raw)
        return StoreResult("stored", artifact_id, None)
    except _Refused as error:
        return StoreResult("withheld", None, error.reason)
    except FileExistsError:
        return StoreResult("withheld", None, "artifact_collision")
    except Exception:
        return StoreResult("withheld", None, "store_unavailable")
    finally:
        if lock is not None:
            with suppress(OSError):
                os.close(lock)
        if directory is not None:
            with suppress(OSError):
                os.close(directory)
