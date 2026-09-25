"""Public export of the latest *encrypted* disaster-recovery bundle.

The endpoint deliberately exposes only a CMS envelope encrypted to an offline
RSA key. Raw PostgreSQL dumps, project sources, MinIO objects and the private
key are never mounted into a public container.

Since 25.09.2026 the bundle is served from the private MinIO bucket ``backups``
(``backup-omnia.sh`` uploads ``<ts>/omnia-backup-<ts>.cms`` + ``<ts>/OFFHOST_SHA256``,
``restore-test-omnia.sh`` publishes ``RESTORE_TEST.json``). The host directory
remains a fallback while the bind mount still exists; a MinIO that is down or
empty never hides a bundle that is on disk.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from minio.error import S3Error
from pydantic import BaseModel

from omnia_api.core.config import get_settings
from omnia_api.core.minio import get_minio_client

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/backups/offhost", tags=["meta"])

_TIMESTAMP_RE = re.compile(r"^\d{8}-\d{6}$")
_CMS_RE = re.compile(r"^omnia-backup-(\d{8}-\d{6})\.cms$")
_OBJECT_RE = re.compile(r"^(\d{8}-\d{6})/omnia-backup-\1\.cms$")
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_VERDICT_MAX_BYTES = 4096
_CHUNK = 1024 * 1024


class OffhostBackupStatus(BaseModel):
    status: str
    created_at: datetime
    size_bytes: int
    sha256: str
    # When a restore of a backup was last PROVEN (restore-test-omnia.sh). A copy
    # nobody has restored is a hope, not a backup; the scheduled off-host job
    # raises the alarm when this is missing, failed or stale.
    restore_test_ok: bool | None = None
    restore_tested_at: datetime | None = None


@dataclass(frozen=True)
class BackupExport:
    name: str
    created_at: datetime
    size_bytes: int
    sha256: str
    path: Path | None = None
    object_key: str | None = None


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(_CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _parse_checksum(text: str, expected_name: str) -> str:
    try:
        recorded_hash, recorded_name = text.split()
    except ValueError:
        raise HTTPException(
            status_code=503, detail="off-host backup checksum is unavailable"
        ) from None
    if recorded_name != expected_name or not _HASH_RE.fullmatch(recorded_hash):
        raise HTTPException(status_code=503, detail="off-host backup checksum is invalid")
    return recorded_hash


# ── host directory (fallback) ────────────────────────────────────────────────


def _latest_host_export(root: Path) -> BackupExport:
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
        recorded_hash = _parse_checksum(checksum_file.read_text(encoding="ascii"), path.name)
    except OSError:
        raise HTTPException(
            status_code=503, detail="off-host backup checksum is unavailable"
        ) from None
    actual_hash = _sha256(path)
    if actual_hash != recorded_hash:
        raise HTTPException(status_code=503, detail="off-host backup failed integrity check")
    return BackupExport(
        name=path.name,
        path=path,
        # File mtime is an absolute instant. It remains correct for legacy
        # directories named in the VPS local timezone and for all new UTC names.
        created_at=datetime.fromtimestamp(path.stat().st_mtime, tz=UTC),
        size_bytes=path.stat().st_size,
        sha256=actual_hash,
    )


def _host_restore_verdict(root: Path) -> tuple[bool | None, datetime | None]:
    verdict = root / "RESTORE_TEST.json"
    try:
        if (
            verdict.is_symlink()
            or not verdict.is_file()
            or verdict.stat().st_size > _VERDICT_MAX_BYTES
        ):
            return None, None
        return _parse_verdict(verdict.read_text(encoding="utf-8"))
    except OSError:
        return None, None


def _parse_verdict(text: str) -> tuple[bool | None, datetime | None]:
    """The verdict file is written by the restore test; anything odd reads as unknown."""
    try:
        data = json.loads(text)
        ok, tested_at = data["ok"], datetime.fromisoformat(data["tested_at"].replace("Z", "+00:00"))
    except (ValueError, KeyError, TypeError, AttributeError):
        return None, None
    if not isinstance(ok, bool) or tested_at.tzinfo is None:
        return None, None
    return ok, tested_at


# ── MinIO bucket (preferred) ─────────────────────────────────────────────────


def _bucket() -> str | None:
    # Settings doubles in older tests carry only backup_export_root: no bucket → host only.
    return getattr(get_settings(), "minio_bucket_backups", None) or None


def _read_small_object(client: Any, bucket: str, key: str, limit: int) -> bytes | None:
    try:
        response = client.get_object(bucket, key)
    except S3Error as error:
        if error.code in {"NoSuchKey", "NoSuchBucket"}:
            return None
        raise
    try:
        data = response.read(limit + 1)
    finally:
        response.close()
        response.release_conn()
    return None if len(data) > limit else data


def _object_sha256(client: Any, bucket: str, key: str) -> str:
    digest = hashlib.sha256()
    response = client.get_object(bucket, key)
    try:
        for chunk in response.stream(_CHUNK):
            digest.update(chunk)
    finally:
        response.close()
        response.release_conn()
    return digest.hexdigest()


def _latest_minio_export(client: Any, bucket: str) -> BackupExport | None:
    """The newest bundle in the bucket, integrity-checked; None when there is none."""
    candidates: list[tuple[datetime, int, str, str]] = []
    try:
        for item in client.list_objects(bucket, recursive=True):
            match = _OBJECT_RE.fullmatch(item.object_name or "")
            if match is None or item.last_modified is None:
                continue
            created = item.last_modified
            if created.tzinfo is None:
                created = created.replace(tzinfo=UTC)
            candidates.append((created, int(item.size or 0), item.object_name, match.group(1)))
    except S3Error as error:
        if error.code in {"NoSuchBucket", "AccessDenied"}:
            return None
        raise
    if not candidates:
        return None
    created_at, size, key, timestamp = max(candidates, key=lambda item: item[0])
    name = f"omnia-backup-{timestamp}.cms"
    checksum = _read_small_object(client, bucket, f"{timestamp}/OFFHOST_SHA256", _VERDICT_MAX_BYTES)
    if checksum is None:
        raise HTTPException(status_code=503, detail="off-host backup checksum is unavailable")
    recorded_hash = _parse_checksum(checksum.decode("ascii", errors="replace"), name)
    actual_hash = _object_sha256(client, bucket, key)
    if actual_hash != recorded_hash:
        raise HTTPException(status_code=503, detail="off-host backup failed integrity check")
    return BackupExport(
        name=name,
        object_key=key,
        created_at=created_at,
        size_bytes=size,
        sha256=actual_hash,
    )


def _minio_restore_verdict(client: Any, bucket: str) -> tuple[bool | None, datetime | None]:
    try:
        data = _read_small_object(client, bucket, "RESTORE_TEST.json", _VERDICT_MAX_BYTES)
    except S3Error:
        return None, None
    if data is None:
        return None, None
    return _parse_verdict(data.decode("utf-8", errors="replace"))


# ── resolution ───────────────────────────────────────────────────────────────


def _latest_export() -> BackupExport:
    bucket = _bucket()
    if bucket:
        try:
            export = _latest_minio_export(get_minio_client(), bucket)
        except HTTPException:
            raise
        except Exception as error:
            log.warning("offhost backup: MinIO unavailable, falling back to host (%s)", error)
            export = None
        if export is not None:
            return export
    return _latest_host_export(Path(get_settings().backup_export_root))


def _restore_verdict(export: BackupExport) -> tuple[bool | None, datetime | None]:
    bucket = _bucket()
    if bucket and export.object_key is not None:
        try:
            ok, tested_at = _minio_restore_verdict(get_minio_client(), bucket)
        except Exception as error:
            log.warning("offhost backup: MinIO verdict unavailable (%s)", error)
            ok, tested_at = None, None
        if ok is not None:
            return ok, tested_at
    return _host_restore_verdict(Path(get_settings().backup_export_root))


def _stream_object(bucket: str, key: str) -> Iterator[bytes]:
    response = get_minio_client().get_object(bucket, key)
    try:
        yield from response.stream(_CHUNK)
    finally:
        response.close()
        response.release_conn()


@router.get("/status", response_model=OffhostBackupStatus)
async def offhost_backup_status() -> OffhostBackupStatus:
    export = _latest_export()
    restore_ok, restore_at = _restore_verdict(export)
    return OffhostBackupStatus(
        status="ok",
        created_at=export.created_at,
        size_bytes=export.size_bytes,
        sha256=export.sha256,
        restore_test_ok=restore_ok,
        restore_tested_at=restore_at,
    )


@router.get("/latest", response_class=FileResponse, response_model=None)
async def download_latest_offhost_backup() -> FileResponse | StreamingResponse:
    export = _latest_export()
    headers = {
        "Cache-Control": "no-store",
        "X-Backup-Created-At": export.created_at.isoformat(),
        "X-Backup-SHA256": export.sha256,
    }
    if export.object_key is not None:
        bucket = _bucket()
        assert bucket is not None
        return StreamingResponse(
            _stream_object(bucket, export.object_key),
            media_type="application/pkcs7-mime",
            headers={
                **headers,
                "Content-Length": str(export.size_bytes),
                "Content-Disposition": f'attachment; filename="{export.name}"',
            },
        )
    assert export.path is not None
    return FileResponse(
        path=export.path,
        filename=export.name,
        media_type="application/pkcs7-mime",
        headers=headers,
    )
