from __future__ import annotations

import hashlib
import os
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from yleum_api.routers import backups


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


# ── MinIO-first (25.09.2026): bundles live in the private bucket, host is a fallback ──

from minio.error import S3Error  # noqa: E402


class _FakeResponse:
    def __init__(self, data: bytes) -> None:
        self._data, self.closed, self.released = data, False, False

    def read(self, amount: int) -> bytes:
        return self._data[:amount]

    def stream(self, amount: int):
        for start in range(0, len(self._data), amount):
            yield self._data[start : start + amount]

    def close(self) -> None:
        self.closed = True

    def release_conn(self) -> None:
        self.released = True


class _FakeMinio:
    def __init__(self, objects: dict[str, tuple[bytes, datetime]] | None = None, *, error=None):
        self.objects, self.error, self.responses = objects or {}, error, []

    def list_objects(self, bucket: str, recursive: bool = False):
        if self.error is not None:
            raise self.error
        assert bucket == "backups" and recursive
        for key, (data, modified) in self.objects.items():
            yield SimpleNamespace(object_name=key, last_modified=modified, size=len(data))

    def get_object(self, bucket: str, key: str) -> _FakeResponse:
        if key not in self.objects:
            raise S3Error("NoSuchKey", "missing", key, "req", "host", None)
        response = _FakeResponse(self.objects[key][0])
        self.responses.append(response)
        return response


def _bundle(ts: str, payload: bytes, modified: datetime) -> dict[str, tuple[bytes, datetime]]:
    digest = hashlib.sha256(payload).hexdigest()
    return {
        f"{ts}/omnia-backup-{ts}.cms": (payload, modified),
        f"{ts}/OFFHOST_SHA256": (f"{digest}  omnia-backup-{ts}.cms\n".encode(), modified),
    }


def _minio_settings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fake: _FakeMinio) -> None:
    monkeypatch.setattr(
        backups,
        "get_settings",
        lambda: SimpleNamespace(backup_export_root=str(tmp_path), minio_bucket_backups="backups"),
    )
    monkeypatch.setattr(backups, "get_minio_client", lambda: fake)


@pytest.mark.asyncio
async def test_status_prefers_the_newest_bundle_in_minio(tmp_path: Path, monkeypatch):
    _write_export(tmp_path, "20260731-031500", b"host copy")  # stale disk fallback
    older = datetime(2026, 9, 24, 0, 15, tzinfo=UTC)
    newer = datetime(2026, 9, 25, 0, 15, tzinfo=UTC)
    fake = _FakeMinio({
        **_bundle("20260924-001500", b"older", older),
        **_bundle("20260925-001500", b"newest-bundle", newer),
    })
    _minio_settings(monkeypatch, tmp_path, fake)

    status = await backups.offhost_backup_status()

    assert status.created_at == newer
    assert status.size_bytes == len(b"newest-bundle")
    assert status.sha256 == hashlib.sha256(b"newest-bundle").hexdigest()
    assert all(response.closed and response.released for response in fake.responses)


@pytest.mark.asyncio
async def test_download_streams_the_minio_object_with_integrity_headers(
    tmp_path: Path, monkeypatch
):
    payload = b"x" * (3 * 1024 * 1024 + 17)
    fake = _FakeMinio(_bundle("20260925-001500", payload, datetime(2026, 9, 25, 0, 15, tzinfo=UTC)))
    _minio_settings(monkeypatch, tmp_path, fake)

    response = await backups.download_latest_offhost_backup()

    assert not isinstance(response, backups.FileResponse)
    body = b""
    async for chunk in response.body_iterator:
        body += chunk
    assert body == payload
    assert response.headers["content-length"] == str(len(payload))
    assert response.headers["x-backup-sha256"] == hashlib.sha256(payload).hexdigest()
    assert response.headers["content-disposition"] == (
        'attachment; filename="omnia-backup-20260925-001500.cms"'
    )
    assert response.headers["cache-control"] == "no-store"
    assert response.media_type == "application/pkcs7-mime"


@pytest.mark.asyncio
async def test_minio_checksum_mismatch_fails_closed(tmp_path: Path, monkeypatch):
    objects = _bundle("20260925-001500", b"bundle", datetime(2026, 9, 25, tzinfo=UTC))
    objects["20260925-001500/OFFHOST_SHA256"] = (
        ("0" * 64 + "  omnia-backup-20260925-001500.cms\n").encode(),
        datetime(2026, 9, 25, tzinfo=UTC),
    )
    _minio_settings(monkeypatch, tmp_path, _FakeMinio(objects))

    with pytest.raises(HTTPException) as refused:
        await backups.offhost_backup_status()
    assert refused.value.status_code == 503
    assert "integrity" in refused.value.detail


@pytest.mark.asyncio
@pytest.mark.parametrize("fake", [_FakeMinio(), _FakeMinio(error=ConnectionError("minio down"))])
async def test_an_empty_or_unreachable_bucket_falls_back_to_the_host_copy(
    tmp_path: Path, monkeypatch, fake
):
    latest = _write_export(tmp_path, "20260731-031500", b"host copy")
    _minio_settings(monkeypatch, tmp_path, fake)

    status = await backups.offhost_backup_status()
    response = await backups.download_latest_offhost_backup()

    assert status.sha256 == hashlib.sha256(b"host copy").hexdigest()
    assert isinstance(response, backups.FileResponse) and Path(response.path) == latest


@pytest.mark.asyncio
async def test_restore_verdict_is_read_from_the_bucket_next_to_the_bundle(
    tmp_path: Path, monkeypatch
):
    when = datetime(2026, 9, 25, 0, 15, tzinfo=UTC)
    objects = _bundle("20260925-001500", b"bundle", when)
    objects["RESTORE_TEST.json"] = (b'{"ok": true, "tested_at": "2026-09-21T04:40:00Z"}', when)
    (tmp_path / "RESTORE_TEST.json").write_text(
        '{"ok": false, "tested_at": "2026-09-01T00:00:00Z"}'
    )
    _minio_settings(monkeypatch, tmp_path, _FakeMinio(objects))

    status = await backups.offhost_backup_status()

    assert status.restore_test_ok is True
    assert status.restore_tested_at == datetime(2026, 9, 21, 4, 40, tzinfo=UTC)
