"""Even an unexpected private-storage archive must remain inside its temp checkout."""

import io
import tarfile
from pathlib import Path
from uuid import uuid4

import pytest

from yleum_api.services import repo as storage

# The broad suite replaces storage I/O automatically. Retain the real function
# before that fixture, then replace only its MinIO client for these unit cases.
download_archive = storage._try_download


def fake_archive(monkeypatch, members):
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for member, contents in members:
            archive.addfile(member, io.BytesIO(contents) if contents is not None else None)

    class Client:
        def fget_object(self, _bucket, _key, destination):
            Path(destination).write_bytes(buffer.getvalue())

    monkeypatch.setattr(storage, "get_minio_client", lambda: Client())


def test_regular_private_repo_archive_preserves_source_bytes(monkeypatch, tmp_path):
    contents = b"export const title = 'project data';\n"
    member = tarfile.TarInfo("./src/app/page.tsx")
    member.size = len(contents)
    fake_archive(monkeypatch, [(member, contents)])
    target = tmp_path / "repo"
    target.mkdir()
    assert download_archive(uuid4(), target) is True
    assert (target / "src/app/page.tsx").read_bytes() == contents
    assert not (tmp_path / "repo.tar.gz").exists()


@pytest.mark.parametrize("kind", ["traversal", "symlink"])
def test_archive_cannot_escape_the_checkout(monkeypatch, tmp_path, kind):
    if kind == "traversal":
        member = tarfile.TarInfo("../escape.txt")
        member.size = 8
        members = [(member, b"sentinel")]
    else:
        member = tarfile.TarInfo("link")
        member.type = tarfile.SYMTYPE
        member.linkname = "../outside"
        members = [(member, None)]
    fake_archive(monkeypatch, members)
    target = tmp_path / "repo"
    target.mkdir()
    with pytest.raises(tarfile.FilterError):
        download_archive(uuid4(), target)
    assert not (tmp_path / "escape.txt").exists()
    assert not (tmp_path / "outside").exists()
    assert not (target / "link").exists()
    assert not (tmp_path / "repo.tar.gz").exists()
