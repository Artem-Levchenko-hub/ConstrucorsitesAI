"""An empty legacy source directory is valid; corrupt/missing sources are not."""

from __future__ import annotations

import gzip
import io
import os
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


def _tiny_gnu_archive(path: Path, entries: list[tuple[str, bytes, bytes]]) -> None:
    """Comparable to the real GNU tar backup; compressed bytes stay below 200."""
    with tarfile.open(path, "w:gz", format=tarfile.GNU_FORMAT) as archive:
        root = tarfile.TarInfo("projects")
        root.type = tarfile.DIRTYPE
        archive.addfile(root)
        for name, kind, payload in entries:
            entry = tarfile.TarInfo(name)
            entry.type = kind
            if kind == tarfile.REGTYPE:
                entry.size = len(payload)
                archive.addfile(entry, io.BytesIO(payload))
            else:
                if kind in {tarfile.SYMTYPE, tarfile.LNKTYPE}:
                    entry.linkname = "projects/a"
                archive.addfile(entry)
    assert path.stat().st_size < 200, "fixture must exercise the small-archive branch"


def _two_tiny_sources(tmp_path: Path) -> Path:
    source = tmp_path / "projects"
    source.mkdir()
    (source / "a").write_bytes(b"a\n")
    (source / "b").write_bytes(b"b\n")
    return source


def test_tiny_gnu_archive_of_two_two_byte_files_is_valid(tmp_path: Path) -> None:
    source = _two_tiny_sources(tmp_path)
    archive = tmp_path / "sources.tgz"
    _tiny_gnu_archive(archive, [
        ("projects/a", tarfile.REGTYPE, b"a\n"),
        ("projects/b", tarfile.REGTYPE, b"b\n"),
    ])
    result = _run(archive, source)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("problem", ["same_size_content", "changed_size", "missing_file",
                                     "extra_file", "file_became_directory", "duplicate"])
def test_small_nonempty_archive_requires_exact_tree_and_content(
    tmp_path: Path, problem: str,
) -> None:
    source = _two_tiny_sources(tmp_path)
    archive = tmp_path / "sources.tgz"
    entries = [("projects/a", tarfile.REGTYPE, b"a\n"),
               ("projects/b", tarfile.REGTYPE, b"b\n")]
    if problem == "same_size_content":
        (source / "b").write_bytes(b"z\n")
    elif problem == "changed_size":
        (source / "b").write_bytes(b"bigger")
    elif problem == "missing_file":
        entries.pop()
    elif problem == "extra_file":
        entries.append(("projects/c", tarfile.REGTYPE, b"c\n"))
    elif problem == "file_became_directory":
        (source / "b").unlink()
        (source / "b").mkdir()
    else:
        entries.append(entries[0])
    _tiny_gnu_archive(archive, entries)
    assert _run(archive, source).returncode != 0


@pytest.mark.parametrize("name", ["projects/../outside", "/projects/a", "projects/./a",
                                  "projects//a", "unrelated/a", "projects\\a"])
def test_small_archive_rejects_unsafe_or_noncanonical_paths(tmp_path: Path, name: str) -> None:
    source = _two_tiny_sources(tmp_path)
    archive = tmp_path / "sources.tgz"
    _tiny_gnu_archive(archive, [(name, tarfile.REGTYPE, b"a\n")])
    assert _run(archive, source).returncode != 0
    assert not (tmp_path / "outside").exists()


def test_small_archive_cannot_alias_a_regular_file_as_a_directory_path(tmp_path: Path) -> None:
    source = _two_tiny_sources(tmp_path)
    archive = tmp_path / "sources.tgz"
    _tiny_gnu_archive(archive, [("projects/a/", tarfile.REGTYPE, b"a\n"),
                              ("projects/b", tarfile.REGTYPE, b"b\n")])
    assert _run(archive, source).returncode != 0


@pytest.mark.parametrize("kind", [tarfile.SYMTYPE, tarfile.LNKTYPE, tarfile.FIFOTYPE,
                                  tarfile.CHRTYPE, tarfile.BLKTYPE])
def test_small_archive_rejects_links_and_special_members(tmp_path: Path, kind: bytes) -> None:
    source = _two_tiny_sources(tmp_path)
    archive = tmp_path / "sources.tgz"
    _tiny_gnu_archive(archive, [("projects/a", kind, b""),
                              ("projects/b", tarfile.REGTYPE, b"b\n")])
    assert _run(archive, source).returncode != 0


def test_small_archive_rejects_filesystem_hardlinks(tmp_path: Path) -> None:
    source = _two_tiny_sources(tmp_path)
    (source / "b").unlink()
    os.link(source / "a", source / "b")
    archive = tmp_path / "sources.tgz"
    _tiny_gnu_archive(archive, [("projects/a", tarfile.REGTYPE, b"a\n"),
                              ("projects/b", tarfile.REGTYPE, b"a\n")])
    assert _run(archive, source).returncode != 0


def test_small_archive_accepts_a_regular_empty_file_and_nested_directory(tmp_path: Path) -> None:
    source = tmp_path / "projects"
    (source / "nested").mkdir(parents=True)
    (source / "nested" / "empty").write_bytes(b"")
    archive = tmp_path / "sources.tgz"
    _tiny_gnu_archive(archive, [("projects/nested", tarfile.DIRTYPE, b""),
                              ("projects/nested/empty", tarfile.REGTYPE, b"")])
    result = _run(archive, source)
    assert result.returncode == 0, result.stderr


def test_small_archive_of_confirmed_empty_source_directory_is_valid(tmp_path: Path) -> None:
    source = tmp_path / "projects"
    source.mkdir()
    archive = tmp_path / "sources.tgz"
    _archive(archive)
    assert archive.stat().st_size < 200
    result = _run(archive, source)
    assert result.returncode == 0, result.stderr
    assert "empty source directory" in result.stdout


@pytest.mark.parametrize(
    "problem", ["corrupt", "truncated", "inner_truncated", "wrong_root", "missing_source"]
)
def test_empty_source_exception_rejects_invalid_archives(tmp_path: Path, problem: str) -> None:
    source = tmp_path / "projects"
    source.mkdir()
    archive = tmp_path / "sources.tgz"
    _archive(archive, root="unrelated" if problem == "wrong_root" else "projects")
    if problem == "corrupt":
        archive.write_bytes(b"private_payload_not_a_tar")
    elif problem == "truncated":
        archive.write_bytes(archive.read_bytes()[:-8])
    elif problem == "inner_truncated":
        entry = tarfile.TarInfo("projects")
        entry.type = tarfile.DIRTYPE
        archive.write_bytes(gzip.compress(entry.tobuf()))
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
