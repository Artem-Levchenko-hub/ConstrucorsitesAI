"""Public export of the latest *encrypted* disaster-recovery bundle.

The endpoint deliberately exposes only a CMS envelope encrypted to an offline
RSA key. Raw PostgreSQL dumps, project sources, MinIO objects and the private
key are never mounted into a public container.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from omnia_api.core.config import get_settings

router = APIRouter(prefix="/api/backups/offhost", tags=["meta"])

_TIMESTAMP_RE = re.compile(r"^\d{8}-\d{6}$")
_CMS_RE = re.compile(r"^omnia-backup-(\d{8}-\d{6})\.cms$")
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")


class OffhostBackupStatus(BaseModel):
    status: str
    created_at: datetime
    size_bytes: int
    sha256: str


@dataclass(frozen=True)
class BackupExport:
    path: Path
    created_at: datetime
    size_bytes: int
    sha256: str


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _latest_export(root: Path) -> BackupExport:
    if not root.is_dir():
        raise HTTPException(status_code=503, detail="off-host backup is not available")

    candidates: list[tuple[int, Path]] = []
    for directory in root.iterdir():
        if (
            directory.is_symlink()
            or not directory.is_dir()
            or not _TIMESTAMP_RE.fullmatch(directory.name)
        ):
            continue
        expected = directory / f"omnia-backup-{directory.name}.cms"
        if expected.is_file() and not expected.is_symlink():
            candidates.append((expected.stat().st_mtime_ns, expected))

    if not candidates:
        raise HTTPException(status_code=503, detail="off-host backup is not available")

    _, path = max(candidates, key=lambda item: item[0])
    timestamp = path.parent.name
    match = _CMS_RE.fullmatch(path.name)
    if match is None or match.group(1) != timestamp:
        raise HTTPException(status_code=503, detail="off-host backup metadata is invalid")

    checksum_file = path.parent / "OFFHOST_SHA256"
    try:
        recorded_hash, recorded_name = checksum_file.read_text(encoding="ascii").split()
    except (OSError, ValueError):
        raise HTTPException(
            status_code=503, detail="off-host backup checksum is unavailable"
        ) from None
    if recorded_name != path.name or not _HASH_RE.fullmatch(recorded_hash):
        raise HTTPException(status_code=503, detail="off-host backup checksum is invalid")

    actual_hash = _sha256(path)
    if actual_hash != recorded_hash:
        raise HTTPException(status_code=503, detail="off-host backup failed integrity check")

    return BackupExport(
        path=path,
        # File mtime is an absolute instant. It remains correct for legacy
        # directories named in the VPS local timezone and for all new UTC names.
        created_at=datetime.fromtimestamp(path.stat().st_mtime, tz=UTC),
        size_bytes=path.stat().st_size,
        sha256=actual_hash,
    )


@router.get("/status", response_model=OffhostBackupStatus)
async def offhost_backup_status() -> OffhostBackupStatus:
    export = _latest_export(Path(get_settings().backup_export_root))
    return OffhostBackupStatus(
        status="ok",
        created_at=export.created_at,
        size_bytes=export.size_bytes,
        sha256=export.sha256,
    )


@router.get("/latest", response_class=FileResponse)
async def download_latest_offhost_backup() -> FileResponse:
    export = _latest_export(Path(get_settings().backup_export_root))
    return FileResponse(
        path=export.path,
        filename=export.path.name,
        media_type="application/pkcs7-mime",
        headers={
            "Cache-Control": "no-store",
            "X-Backup-Created-At": export.created_at.isoformat(),
            "X-Backup-SHA256": export.sha256,
        },
    )
