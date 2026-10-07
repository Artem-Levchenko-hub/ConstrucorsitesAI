"""An empty legacy source directory is valid; corrupt/missing sources are not."""

from __future__ import annotations

import io
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[3] / "infra/backup/verify-project-sources.py"


def _archive(path: Path, *, root: str = "projects", payload: bytes | None = None) -> None:
    with tarfile.open(path, "w:gz", format=tarfile.USTAR_FORMAT) as archive:
        entry = tarfile.TarInfo(root)
        entry.type = tarfile.DIRTYPE
        archive.addfile(entry)
        if payload is not None:
            entry = tarfile.TarInfo(f"{root}/app.txt")
            entry.size = len(payload)
            archive.addfile(entry, io.BytesIO(payload))


def _run(archive: Path, source: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(_SCRIPT), str(archive), str(source)],
        capture_output=True,
        text=True,
        check=False,
    )


def test_small_archive_of_confirmed_empty_source_directory_is_valid(tmp_path: Path) -> None:
    source = tmp_path / "projects"
    source.mkdir()
    archive = tmp_path / "sources.tgz"
    _archive(archive)
    assert archive.stat().st_size < 200
    result = _run(archive, source)
    assert result.returncode == 0, result.stderr
    assert "empty source directory" in result.stdout


@pytest.mark.parametrize("problem", ["corrupt", "truncated", "wrong_root", "missing_source"])
def test_empty_source_exception_rejects_invalid_archives(tmp_path: Path, problem: str) -> None:
    source = tmp_path / "projects"
    source.mkdir()
    archive = tmp_path / "sources.tgz"
    _archive(archive, root="unrelated" if problem == "wrong_root" else "projects")
    if problem == "corrupt":
        archive.write_bytes(b"private_payload_not_a_tar")
    elif problem == "truncated":
        archive.write_bytes(archive.read_bytes()[:-8])
    elif problem == "missing_source":
        source.rmdir()
    result = _run(archive, source)
    assert result.returncode != 0
    assert "private_payload" not in result.stdout + result.stderr


def test_empty_archive_cannot_hide_existing_sources(tmp_path: Path) -> None:
    source = tmp_path / "projects"
    source.mkdir()
    (source / "app.txt").write_text("private_customer_source", encoding="utf-8")
    archive = tmp_path / "sources.tgz"
    _archive(archive)
    result = _run(archive, source)
    assert result.returncode != 0
    assert "private_customer_source" not in result.stdout + result.stderr


def test_normal_source_archive_still_passes(tmp_path: Path) -> None:
    source = tmp_path / "projects"
    source.mkdir()
    payload = bytes(range(256)) * 8
    (source / "app.txt").write_bytes(payload)
    archive = tmp_path / "sources.tgz"
    _archive(archive, payload=payload)
    assert archive.stat().st_size >= 200
    assert _run(archive, source).returncode == 0
