from __future__ import annotations

import io
from types import SimpleNamespace
from uuid import uuid4

import pytest

from omnia_api.core.errors import ApiError
from omnia_api.models.project import Project
from omnia_api.models.snapshot import Snapshot
from omnia_api.routers import snapshots


class Session:
    def __init__(self, project, snapshot):
        self.project, self.snapshot = project, snapshot

    async def get(self, model, key):
        if model is Project and key == self.project.id:
            return self.project
        if model is Snapshot and key == self.snapshot.id:
            return self.snapshot
        return None


def fixture():
    project = SimpleNamespace(id=uuid4(), owner_id=uuid4())
    sid = uuid4()
    key = f"snapshot-previews/{project.id}/{sid}/" + "c" * 64 + ".png"
    snapshot = SimpleNamespace(
        id=sid,
        project_id=project.id,
        commit_sha="a" * 40,
        preview_commit_sha="a" * 40,
        preview_status="ready",
        preview_manifest=[{"key": key, "width": 390, "height": 2000, "route": "/"}],
    )
    return project, snapshot, Session(project, snapshot)


async def test_private_image_streams_only_the_selected_verified_artifact(monkeypatch):
    project, snap, session = fixture()
    data = b"\x89PNG\r\n\x1a\nimage"
    stream = io.BytesIO(data)
    stream.release_conn = lambda: None
    calls = []

    class Storage:
        def get_object(self, bucket, key):
            calls.append(key)
            return stream

    monkeypatch.setattr(snapshots, "get_minio_client", lambda: Storage(), raising=False)
    response = await snapshots.get_snapshot_preview_image(
        project.id,
        snap.id,
        0,
        session,
        SimpleNamespace(id=project.owner_id),
        v="c" * 64,
    )
    content = b"".join([chunk async for chunk in response.body_iterator])
    assert content == data
    assert calls == [snap.preview_manifest[0]["key"]]
    assert stream.closed
    assert response.headers["content-type"] == "image/png"
    assert "private" in response.headers["cache-control"]
    assert response.headers["x-content-type-options"] == "nosniff"


@pytest.mark.parametrize(
    "failure",
    [
        "foreign_owner",
        "foreign_snapshot",
        "commit_mismatch",
        "missing",
        "wrong_key",
        "index",
        "digest",
    ],
)
async def test_untrusted_or_unauthorized_images_are_not_read(failure, monkeypatch):
    project, snap, session = fixture()
    owner = project.owner_id
    index, digest = 0, "c" * 64
    if failure == "foreign_owner":
        owner = uuid4()
    if failure == "foreign_snapshot":
        snap.project_id = uuid4()
    if failure == "commit_mismatch":
        snap.preview_commit_sha = "b" * 40
    if failure == "missing":
        snap.preview_status = "missing"
    if failure == "wrong_key":
        snap.preview_manifest[0]["key"] = "repos/private.tar.gz"
    if failure == "index":
        index = 1
    if failure == "digest":
        digest = "d" * 64

    def forbidden():
        pytest.fail("Rejected image must not access storage")

    monkeypatch.setattr(snapshots, "get_minio_client", forbidden, raising=False)
    with pytest.raises(ApiError) as exc:
        await snapshots.get_snapshot_preview_image(
            project.id,
            snap.id,
            index,
            session,
            SimpleNamespace(id=owner),
            v=digest,
        )
    assert exc.value.status_code == 404
