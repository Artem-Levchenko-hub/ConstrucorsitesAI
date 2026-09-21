from __future__ import annotations

import hashlib
import os
from datetime import UTC, datetime
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
    created_at = datetime.strptime(timestamp, "%Y%m%d-%H%M%S").replace(tzinfo=UTC)
    os.utime(export, (created_at.timestamp(), created_at.timestamp()))
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


@pytest.mark.asyncio
async def test_latest_export_uses_absolute_mtime_not_timezone_affected_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    misleading = _write_export(tmp_path, "20260731-133907", b"old-local-time")
    actual_latest = _write_export(tmp_path, "20260731-104500", b"new-utc-time")
    old_epoch = datetime(2026, 7, 31, 10, 39, 7, tzinfo=UTC).timestamp()
    latest_epoch = datetime(2026, 7, 31, 10, 45, tzinfo=UTC).timestamp()
    os.utime(misleading, (old_epoch, old_epoch))
    os.utime(actual_latest, (latest_epoch, latest_epoch))
    monkeypatch.setattr(
        backups,
        "get_settings",
        lambda: SimpleNamespace(backup_export_root=str(tmp_path)),
    )

    response = await backups.download_latest_offhost_backup()

    assert Path(response.path) == actual_latest
    assert response.headers["x-backup-created-at"] == "2026-07-31T10:45:00+00:00"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "verdict, expected",
    [
        (
            '{"ok": true, "tested_at": "2026-09-21T05:20:00Z", "source": "20260921-051517"}',
            (True, "2026-09-21T05:20:00+00:00"),
        ),
        (
            '{"ok": false, "tested_at": "2026-09-21T05:20:00Z", "source": "x"}',
            (False, "2026-09-21T05:20:00+00:00"),
        ),
        (None, (None, None)),
        ("not json", (None, None)),
        ('{"ok": "yes", "tested_at": "2026-09-21T05:20:00Z"}', (None, None)),
        ('{"ok": true, "tested_at": "2026-09-21T05:20:00"}', (None, None)),
        ('{"ok": true}', (None, None)),
    ],
)
async def test_status_reports_when_a_restore_was_last_proven(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, verdict: str | None, expected
) -> None:
    _write_export(tmp_path, "20260731-031500")
    if verdict is not None:
        (tmp_path / "RESTORE_TEST.json").write_text(verdict, encoding="utf-8")
    monkeypatch.setattr(
        backups, "get_settings", lambda: SimpleNamespace(backup_export_root=str(tmp_path))
    )

    status = await backups.offhost_backup_status()

    tested_at = status.restore_tested_at.isoformat() if status.restore_tested_at else None
    assert (status.restore_test_ok, tested_at) == expected
    assert status.status == "ok", "an unknown restore verdict never hides the copy itself"


@pytest.mark.asyncio
async def test_restore_verdict_never_follows_a_symlink(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_export(tmp_path, "20260731-031500")
    secret = tmp_path / "elsewhere.json"
    secret.write_text('{"ok": true, "tested_at": "2026-09-21T05:20:00Z"}', encoding="utf-8")
    (tmp_path / "RESTORE_TEST.json").symlink_to(secret)
    monkeypatch.setattr(
        backups, "get_settings", lambda: SimpleNamespace(backup_export_root=str(tmp_path))
    )

    status = await backups.offhost_backup_status()

    assert (status.restore_test_ok, status.restore_tested_at) == (None, None)
