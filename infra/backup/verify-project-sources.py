"""Verify source archives, proving the full tree when compressed bytes are small."""

from __future__ import annotations

import gzip
import os
import stat
import sys
import tarfile
from pathlib import Path


def _identity(info: os.stat_result) -> tuple[int, ...]:
    return (info.st_dev, info.st_ino, info.st_mode, info.st_size, info.st_nlink,
            info.st_mtime_ns)


def _verify_small_tree(tar: tarfile.TarFile, members: list[tarfile.TarInfo], source: Path) -> None:
    archived: dict[str, tarfile.TarInfo] = {}
    for member in members:
        name = member.name.rstrip("/") if member.isdir() else member.name
        parts = name.split("/")
        if (parts[0] != source.name or "\\" in name
                or any(part in {"", ".", ".."} for part in parts)
                or name in archived or not (member.isdir() or member.isreg())
                or (member.isdir() and member.size != 0)):
            raise ValueError("small project sources archive has unsafe or duplicate entries")
        archived[name] = member

    actual: dict[str, tuple[Path, os.stat_result]] = {}
    pending = [source]
    while pending:
        path = pending.pop()
        info = path.lstat()
        if stat.S_ISDIR(info.st_mode):
            pending.extend(path.iterdir())
        elif not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ValueError("small project sources tree contains links or special files")
        name = source.name if path == source else f"{source.name}/{path.relative_to(source).as_posix()}"
        actual[name] = (path, info)
    if actual.keys() != archived.keys():
        raise ValueError("small project sources archive does not match the source tree")

    for name, (path, info) in actual.items():
        member = archived[name]
        if member.isdir() != stat.S_ISDIR(info.st_mode):
            raise ValueError("small project sources archive has a mismatched entry type")
        if member.isdir():
            continue
        if member.size != info.st_size:
            raise ValueError("small project sources archive has a mismatched file size")
        # Read only regular, unchanged files. extractfile returns a byte stream;
        # no archive member is extracted onto the filesystem.
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
        with os.fdopen(os.open(path, flags), "rb") as current:
            if _identity(os.fstat(current.fileno())) != _identity(info):
                raise ValueError("project source file changed during verification")
            saved = tar.extractfile(member)
            if saved is None:
                raise ValueError("small project sources archive file is unreadable")
            with saved:
                while True:
                    content = current.read(1024 * 1024)
                    if content != saved.read(1024 * 1024):
                        raise ValueError("small project sources archive has mismatched file content")
                    if not content:
                        break
            if _identity(os.fstat(current.fileno())) != _identity(info):
                raise ValueError("project source file changed during verification")
    for path, info in actual.values():
        current_info = path.lstat()
        # Compare ctime only through the same path API: Windows fd stat can
        # report a different ctime for an unchanged file than path stat.
        if (_identity(current_info) != _identity(info)
                or current_info.st_ctime_ns != info.st_ctime_ns):
            raise ValueError("project sources tree changed during verification")


def verify(archive: Path, source: Path) -> str:
    if archive.is_symlink() or source.is_symlink() or not source.is_dir():
        raise ValueError("project sources archive or source directory is invalid")
    # Tar accepts EOF after a member without its two end records. Check those
    # records explicitly, then consume gzip through EOF to validate its trailer.
    # Never include archive contents in diagnostics.
    with gzip.open(archive, "rb") as stream:
        with tarfile.open(fileobj=stream, mode="r:") as tar:
            members = tar.getmembers()
            end = max(
                (member.offset_data + ((member.size + 511) // 512) * 512 for member in members),
                default=0,
            )
            stream.seek(end)
            if stream.read(1024) != bytes(1024):
                raise ValueError("project sources tar archive is incomplete")
            while stream.read(1024 * 1024):
                pass
            roots = [member for member in members if member.name.rstrip("/") == source.name]
            if len(roots) != 1 or not roots[0].isdir():
                raise ValueError("project sources archive has no expected source directory")
            if archive.stat().st_size >= 200:
                return "project sources archive verified"
            _verify_small_tree(tar, members, source)
            if len(members) == 1:
                return "project sources archive verified: empty source directory"
            return "project sources archive verified: matching small source tree"


def main() -> int:
    try:
        if len(sys.argv) != 3:
            raise ValueError("project sources verification requires archive and source directory")
        print(verify(Path(sys.argv[1]), Path(sys.argv[2])))
    except (OSError, EOFError, tarfile.TarError, ValueError):
        print("project sources archive verification failed", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
