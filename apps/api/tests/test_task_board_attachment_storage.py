from types import SimpleNamespace
from typing import Any
from uuid import UUID

import pytest
from minio.error import S3Error

from omnia_api.services import task_board_attachments as storage


def _settings(bucket: str = "task-board") -> SimpleNamespace:
    return SimpleNamespace(
        minio_bucket_task_board=bucket,
        minio_bucket_projects="projects",
        minio_bucket_previews="previews",
        minio_bucket_images="omnia-images",
        minio_bucket_photos="omnia-photos",
    )


def _s3_error(code: str) -> S3Error:
    return S3Error(None, code, code, None, None, None)  # type: ignore[arg-type]


class FakeMinio:
    def __init__(self) -> None:
        self.policy_removed = False
        self.get_error: Exception = _s3_error("NoSuchKey")
        self.put_error: Exception | None = None

    def bucket_exists(self, _bucket: str) -> bool:
        return True

    def delete_bucket_policy(self, _bucket: str) -> None:
        self.policy_removed = True

    def get_object(self, _bucket: str, _object_key: str) -> Any:
        raise self.get_error

    def put_object(self, *_args: Any, **_kwargs: Any) -> None:
        if self.put_error is not None:
            raise self.put_error


def test_attachment_bucket_is_forced_private(monkeypatch: pytest.MonkeyPatch) -> None:
    client = FakeMinio()
    monkeypatch.setattr(storage, "get_settings", _settings)
    monkeypatch.setattr(storage, "get_minio_client", lambda: client)

    _client, bucket = storage._ensure_private_bucket()

    assert bucket == "task-board"
    assert client.policy_removed is True


def test_attachment_bucket_must_not_collide_with_media(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(storage, "get_settings", lambda: _settings("previews"))
    monkeypatch.setattr(storage, "get_minio_client", FakeMinio)

    with pytest.raises(storage.AttachmentStorageError, match="dedicated"):
        storage._ensure_private_bucket()


def test_download_only_maps_missing_object_to_none(monkeypatch: pytest.MonkeyPatch) -> None:
    client = FakeMinio()
    monkeypatch.setattr(storage, "get_settings", _settings)
    monkeypatch.setattr(storage, "get_minio_client", lambda: client)

    assert storage.load_attachment("missing") is None

    client.get_error = _s3_error("ServiceUnavailable")
    with pytest.raises(storage.AttachmentStorageError, match="download failed"):
        storage.load_attachment("temporarily-unavailable")

    client.get_error = RuntimeError("transport offline")
    with pytest.raises(storage.AttachmentStorageError, match="download failed"):
        storage.load_attachment("transport-failure")


def test_ambiguous_put_error_carries_deterministic_object_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = FakeMinio()
    client.put_error = RuntimeError("connection closed after PUT")
    monkeypatch.setattr(storage, "get_settings", _settings)
    monkeypatch.setattr(storage, "get_minio_client", lambda: client)
    task_id = UUID("00000000-0000-0000-0000-000000000001")
    attachment_id = UUID("00000000-0000-0000-0000-000000000002")

    with pytest.raises(storage.AttachmentUploadError) as raised:
        storage.store_attachment(
            task_id,
            attachment_id,
            "artifact.html",
            "text/html",
            b"<html></html>",
        )

    assert raised.value.object_key == (
        "tasks/00000000-0000-0000-0000-000000000001/00000000-0000-0000-0000-000000000002.html"
    )
