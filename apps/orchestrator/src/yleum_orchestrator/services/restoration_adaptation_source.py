"""Canonical byte-preserving source set for restoration adaptation."""

from __future__ import annotations

import hashlib
import io
import os
import tarfile
from collections.abc import Mapping
from pathlib import Path, PurePosixPath

from yleum_orchestrator.core.cell_resources import CellIdentityConflict, CellResourceError
from yleum_orchestrator.services.restoration_binding import canonical_digest

MAX_SOURCE_FILES = 4096
MAX_SOURCE_FILE_BYTES = 16 * 1024 * 1024
MAX_SOURCE_BYTES = 64 * 1024 * 1024
MAX_SOURCE_ARCHIVE_BYTES = 72 * 1024 * 1024

_EXCLUDED_PARTS = frozenset(
    {
        ".git",
        ".next",
        ".pnpm-store",
        ".turbo",
        ".cache",
        ".venv",
        "__pycache__",
        "node_modules",
        "coverage",
        "dist",
        "build",
        "vendor",
    }
)
_SECRET_NAMES = frozenset(
    {
        ".env",
        "secrets.json",
        "secrets.yaml",
        "secrets.yml",
    }
)


def canonical_source_files(files: Mapping[str, bytes]) -> dict[str, bytes]:
    """Return the bounded source-only byte set used by copy, proof, and seal."""

    result: dict[str, bytes] = {}
    total = 0
    for raw_path, raw_payload in sorted(files.items()):
        if not isinstance(raw_path, str) or not isinstance(raw_payload, bytes):
            raise CellIdentityConflict("adaptation source inventory is invalid")
        path = PurePosixPath(raw_path.replace("\\", "/"))
        normalized = path.as_posix().lstrip("/")
        parts = PurePosixPath(normalized).parts
        if (
            not normalized
            or normalized == "."
            or path.is_absolute()
            or ".." in parts
            or any(part in _EXCLUDED_PARTS for part in parts)
            or any(part.casefold() in _SECRET_NAMES for part in parts)
            or any(part.casefold().startswith(".env.") for part in parts)
            or normalized.endswith(".tsbuildinfo")
        ):
            continue
        if normalized in result:
            raise CellIdentityConflict("adaptation source path is duplicated")
        if len(raw_payload) > MAX_SOURCE_FILE_BYTES:
            raise CellResourceError("adaptation source file exceeds byte limit")
        total += len(raw_payload)
        if len(result) >= MAX_SOURCE_FILES or total > MAX_SOURCE_BYTES:
            raise CellResourceError("adaptation source exceeds bounded inventory")
        result[normalized] = raw_payload
    if not result:
        raise CellIdentityConflict("adaptation source inventory is empty")
    return result


def source_manifest(files: Mapping[str, bytes]) -> tuple[dict[str, object], ...]:
    canonical = canonical_source_files(files)
    return tuple(
        {
            "path": path,
            "size": len(payload),
            "sha256": hashlib.sha256(payload).hexdigest(),
        }
        for path, payload in canonical.items()
    )


def source_manifest_digest(files: Mapping[str, bytes]) -> str:
    return canonical_digest(source_manifest(files))


def write_source_archive(files: Mapping[str, bytes], destination: Path) -> str:
    """Write one deterministic tar of the already filtered source set."""

    canonical = canonical_source_files(files)
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w", format=tarfile.USTAR_FORMAT) as archive:
        for path, payload in canonical.items():
            info = tarfile.TarInfo(path)
            info.size = len(payload)
            info.mode = 0o644
            info.uid = 0
            info.gid = 0
            info.uname = ""
            info.gname = ""
            info.mtime = 0
            archive.addfile(info, io.BytesIO(payload))
    raw = buffer.getvalue()
    if len(raw) > MAX_SOURCE_ARCHIVE_BYTES:
        raise CellResourceError("adaptation source archive exceeds byte limit")
    with destination.open("xb") as handle:
        handle.write(raw)
        handle.flush()
        os.fsync(handle.fileno())
    return hashlib.sha256(raw).hexdigest()
