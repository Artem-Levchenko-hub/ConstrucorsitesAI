from __future__ import annotations

import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from omnia_api.routers import backups


def _write_export(root: Path, timestamp: str, payload: bytes = b"encrypted-cms") -> Path:
    directory = root / timestamp
    directory.mkdir(parents=True)
    export = directory / f"omnia-backup-{timestamp}.cms"
    export.write_bytes(payload)
    digest = hashlib.sha256(payload).hexdigest()
    (directory / "OFFHOST_SHA256").write_text(
        f"{digest}  {export.name}\n",
        encoding="ascii",
    )
    return export


@pytest.mark.asyncio
async def test_status_returns_latest_integrity_checked_export(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_export(tmp_path, "20260730-031500", b"older")
    latest = _write_export(tmp_path, "20260731-031500", b"latest")
    monkeypatch.setattr(
        backups,
        "get_settings",
        lambda: SimpleNamespace(backup_export_root=str(tmp_path)),
    )

    status = await backups.offhost_backup_status()

    assert status.status == "ok"
    assert status.created_at.isoformat() == "2026-07-31T03:15:00+00:00"
    assert status.size_bytes == latest.stat().st_size
    assert status.sha256 == hashlib.sha256(b"latest").hexdigest()


@pytest.mark.asyncio
async def test_download_exposes_only_fixed_encrypted_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    latest = _write_export(tmp_path, "20260731-031500")
    (latest.parent / "platform-omnia.sql.gz").write_bytes(b"sensitive")
    monkeypatch.setattr(
        backups,
        "get_settings",
        lambda: SimpleNamespace(backup_export_root=str(tmp_path)),
    )

    response = await backups.download_latest_offhost_backup()

    assert Path(response.path) == latest
    assert response.media_type == "application/pkcs7-mime"
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-backup-sha256"] == hashlib.sha256(
        b"encrypted-cms"
    ).hexdigest()


@pytest.mark.asyncio
async def test_corrupted_export_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    export = _write_export(tmp_path, "20260731-031500")
    export.write_bytes(b"tampered")
    monkeypatch.setattr(
        backups,
        "get_settings",
        lambda: SimpleNamespace(backup_export_root=str(tmp_path)),
    )

    with pytest.raises(HTTPException) as exc:
        await backups.offhost_backup_status()

    assert exc.value.status_code == 503
    assert exc.value.detail == "off-host backup failed integrity check"


@pytest.mark.asyncio
async def test_missing_export_is_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        backups,
        "get_settings",
        lambda: SimpleNamespace(backup_export_root=str(tmp_path)),
    )

    with pytest.raises(HTTPException) as exc:
        await backups.offhost_backup_status()

    assert exc.value.status_code == 503
